"""
The counter device as its branch's print station.

These endpoints are authenticated by the device token issued when the counter was linked (X-Device-Token), not by a person's
session, so KOTs sent from waiters' phones keep printing while the counter's screen is locked or no one is signed in on it.
They can only list, render and confirm the printing of the branch's own KOTs. Prints confirmed here are attributed to no
person; a reprint someone asks for goes through the staff endpoints and is audited to them.
"""

import uuid
from typing import List

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_print_station
from app.core.database import get_db
from app.core.errors import ValidationFailed
from app.core.idempotency import IdempotencyGuard
from app.core.redis import get_redis
from app.modules.kots import service
from app.modules.kots import slip as slips
from app.modules.kots.router import slip_read
from app.schemas.kots import KOTRead, SlipRead

router = APIRouter(prefix="/kots/station", tags=["Kitchen Order Tickets (KOT)"])


async def station_idempotency(
    idempotency_key: str = Header(..., alias="X-Idempotency-Key", min_length=8, max_length=100),
    redis_client: aioredis.Redis = Depends(get_redis),
    station: CurrentUser = Depends(require_print_station),
) -> IdempotencyGuard:
    return IdempotencyGuard(redis_client, scope="kot-printed-station", key=idempotency_key, user_id=station.id, required=True)


@router.get("/print-queue", response_model=List[KOTRead])
async def station_print_queue(station: CurrentUser = Depends(require_print_station), db: AsyncSession = Depends(get_db)):
    """What this branch still has to print, oldest first: paper KOTs never printed, and VOID notices owed to the kitchen."""
    return await service.print_queue(db, station, None)


@router.get("/{kot_id}/slip", response_model=SlipRead)
async def station_slip(
    kot_id: uuid.UUID,
    width: int = Query(80, description="Paper width in mm: 58 or 80"),
    station: CurrentUser = Depends(require_print_station),
    db: AsyncSession = Depends(get_db),
):
    """A KOT as print rows for the paper width."""
    if width not in slips.COLUMNS:
        raise ValidationFailed("Paper width must be 58 or 80 mm", code="invalid_width")
    slip, kot = await service.build_slip(db, station, kot_id)
    return slip_read(slip, kot, width)


@router.post("/{kot_id}/printed", response_model=KOTRead)
async def station_printed(
    kot_id: uuid.UUID,
    station: CurrentUser = Depends(require_print_station),
    guard: IdempotencyGuard = Depends(station_idempotency),
    db: AsyncSession = Depends(get_db),
):
    """The printer reported success. Retrying with the same X-Idempotency-Key is never a second print."""
    cached = await guard.begin({"kot": str(kot_id)})
    if cached:
        return cached.as_response()
    try:
        kot = await service.record_print(db, station, kot_id)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, KOTRead.model_validate(kot))
