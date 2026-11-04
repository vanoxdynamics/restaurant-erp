import base64
import uuid
from enum import StrEnum
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from pydantic import RootModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, module_flag, require_roles
from app.core.database import get_db
from app.core.enums import KOTStatus, Role, Station
from app.core.errors import Forbidden
from app.core.modules import ModuleKey
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.redis import get_redis
from app.modules.kots import service
from app.modules.kots import slip as slips
from app.schemas.billing import PrintRowRead
from app.schemas.kots import KOTFireRequest, KOTRead, KOTStatusUpdate, SlipLineRead, SlipRead, VoidKOTRequest

router = APIRouter(
    prefix="/kots",
    tags=["Kitchen Order Tickets (KOT)"],
    dependencies=[Depends(get_current_user)],
)

board_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.KITCHEN, Role.WAITER, Role.CASHIER)
fire_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER)
void_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER, Role.KITCHEN)

KOTList = RootModel[List[KOTRead]]
kds_enabled = module_flag(ModuleKey.KDS)


async def board_user(user: CurrentUser = Depends(board_roles), has_kds: bool = Depends(kds_enabled)) -> CurrentUser:
    """Everyone on the board list, except that kitchen staff only have a screen to use when the plan includes it."""
    if user.role == Role.KITCHEN and not has_kds:
        raise Forbidden("The kitchen display is not part of your plan", code="module_disabled", details={"module": "kds"})
    return user


@router.get("", response_model=List[KOTRead])
async def list_kots(
    station: Optional[Station] = None,
    status: Optional[KOTStatus] = None,
    order_id: Optional[uuid.UUID] = None,
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(board_user),
    db: AsyncSession = Depends(get_db),
):
    """List KOTs filtered by station and status (defaults to open tickets). The kitchen role only sees screen tickets."""
    return await service.list_kots(db, user, branch_id, station, status, order_id, screen_only=user.role == Role.KITCHEN)


@router.get("/print-queue", response_model=List[KOTRead])
async def print_queue(
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(board_user),
    db: AsyncSession = Depends(get_db),
):
    """
    What the counter's print station still has to print, oldest first: paper tickets never printed, and VOID notices owed to the
    kitchen for cancelled tickets whose slip already went out. Print each, then confirm with `POST /kots/{id}/slip/printed`.
    """
    return await service.print_queue(db, user, branch_id)


@router.post("/fire", response_model=List[KOTRead], status_code=status.HTTP_201_CREATED)
async def fire_kot_batch(
    payload: KOTFireRequest,
    user: CurrentUser = Depends(fire_roles),
    guard: IdempotencyGuard = Depends(idempotent("kots")),
    db: AsyncSession = Depends(get_db),
    has_kds: bool = Depends(kds_enabled),
):
    """
    Fire pending order items from handheld/POS tablet.
    Splits line items across their prep stations (e.g., kitchen vs grill), one KOT per station.
    Requires an X-Idempotency-Key header.
    """
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        kots = await service.fire_kots(db, user, payload, kds=has_kds)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, KOTList.model_validate(kots))


@router.patch("/{kot_id}/status", response_model=KOTRead)
async def update_kot_status(
    kot_id: uuid.UUID,
    payload: KOTStatusUpdate,
    user: CurrentUser = Depends(board_user),
    db: AsyncSession = Depends(get_db),
    has_kds: bool = Depends(kds_enabled),
):
    """
    Advance KOT status (queued -> preparing -> ready -> served). The kitchen runs the first
    stages; floor staff may only mark a ready ticket served. Without the kitchen display module a ticket is a printed slip:
    floor staff mark it served straight from queued.
    """
    return await service.update_kot_status(db, user, kot_id, payload.status, kds=has_kds)


@router.post("/{kot_id}/void", response_model=KOTRead)
async def void_kot(
    kot_id: uuid.UUID,
    payload: VoidKOTRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(void_roles),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Void a KOT with manager authorization and reason tracking for fraud audit."""
    return await service.void_kot(db, user, kot_id, payload, background, redis_client)


class SlipFormat(StrEnum):
    JSON = "json"
    TEXT = "text"
    HTML = "html"
    ESCPOS = "escpos"
    ESCPOS_BASE64 = "escpos_base64"


_HTML_HEADERS = {
    # The slip contains staff-typed text. It is escaped, and this stops the browser running anything even if that ever failed.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


@router.get("/{kot_id}/slip", response_model=SlipRead, responses={
    200: {"content": {"text/plain": {}, "text/html": {}, "application/octet-stream": {}}}})
async def get_slip(
    kot_id: uuid.UUID,
    format: SlipFormat = SlipFormat.JSON,
    width: int = Query(80, description="Paper width in mm: 58 or 80"),
    user: CurrentUser = Depends(board_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The kitchen slip for a ticket, as JSON, plain text, a printable HTML page, or ESC/POS bytes for a thermal printer
    (`escpos`, or `escpos_base64` for bridges that need text). Reading never counts as printing: after the paper comes out,
    call `POST /kots/{id}/slip/printed` so a later print is marked REPRINT.
    """
    if width not in slips.COLUMNS:
        raise Forbidden("Paper width must be 58 or 80 mm", code="invalid_width", status_code=422)
    slip, kot = await service.build_slip(db, user, kot_id)
    if format == SlipFormat.TEXT:
        return Response(slips.render_text(slip, width), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})
    if format == SlipFormat.HTML:
        return Response(slips.render_html(slip, width), media_type="text/html; charset=utf-8", headers=_HTML_HEADERS)
    if format in (SlipFormat.ESCPOS, SlipFormat.ESCPOS_BASE64):
        data, warnings = slips.render_escpos(slip, width)
        headers = {"Cache-Control": "no-store", "X-Slip-Warnings": ",".join(warnings)}
        if format == SlipFormat.ESCPOS_BASE64:
            return Response(base64.b64encode(data).decode(), media_type="text/plain", headers=headers)
        return Response(data, media_type="application/octet-stream", headers=headers)
    return slip_read(slip, kot, width)


def slip_read(slip: slips.Slip, kot, width: int) -> SlipRead:
    _, warnings = slips.render_escpos(slip, width)
    return SlipRead(
        marker=slip.marker, branch_name=slip.branch_name, branch_code=slip.branch_code, kot_number=slip.kot_number,
        order_number=slip.order_number, station=slip.station, order_type=slip.order_type, table_number=slip.table_number,
        guest_count=slip.guest_count, fired_at=slip.fired_at.isoformat(), fired_by=slip.fired_by, round=slip.round,
        print_count=slip.print_count, status=kot.status, fulfilment=kot.fulfilment, order_notes=slip.order_notes,
        lines=[SlipLineRead(quantity=i.quantity, name=slips.clean(i.name), notes=slips.clean(i.notes) or None, cancelled=i.cancelled,
                            station=i.station) for i in slip.lines],
        text=slips.render_text(slip, 80), warnings=warnings, paper_mm=width,
        rows=[PrintRowRead(style=st, align=al, text=tx) for st, al, tx in slips.print_rows(slip, width)],
    )


@router.post("/{kot_id}/slip/printed", response_model=KOTRead)
async def slip_printed(
    kot_id: uuid.UUID,
    user: CurrentUser = Depends(board_user),
    guard: IdempotencyGuard = Depends(idempotent("kot-printed")),
    db: AsyncSession = Depends(get_db),
):
    """Confirm that the slip printed. The first confirmation is the original; later ones are counted and audited as reprints."""
    cached = await guard.begin({"kot": str(kot_id)})
    if cached:
        return cached.as_response()
    try:
        kot = await service.record_print(db, user, kot_id)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, KOTRead.model_validate(kot))
