import uuid
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import RootModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import KOTStatus, Role, Station
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.redis import get_redis
from app.modules.kots import service
from app.schemas.kots import KOTFireRequest, KOTRead, KOTStatusUpdate, VoidKOTRequest

router = APIRouter(
    prefix="/kots",
    tags=["Kitchen Order Tickets (KOT)"],
    dependencies=[Depends(get_current_user)],
)

board_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.KITCHEN, Role.WAITER, Role.CASHIER)
fire_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER)
void_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER, Role.KITCHEN)

KOTList = RootModel[List[KOTRead]]


@router.get("", response_model=List[KOTRead])
async def list_kots(
    station: Optional[Station] = None,
    status: Optional[KOTStatus] = None,
    order_id: Optional[uuid.UUID] = None,
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(board_roles),
    db: AsyncSession = Depends(get_db),
):
    """List KOTs filtered by station and status (defaults to open tickets)."""
    return await service.list_kots(db, user, branch_id, station, status, order_id)


@router.post("/fire", response_model=List[KOTRead], status_code=status.HTTP_201_CREATED)
async def fire_kot_batch(
    payload: KOTFireRequest,
    user: CurrentUser = Depends(fire_roles),
    guard: IdempotencyGuard = Depends(idempotent("kots")),
    db: AsyncSession = Depends(get_db),
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
        kots = await service.fire_kots(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, KOTList.model_validate(kots))


@router.patch("/{kot_id}/status", response_model=KOTRead)
async def update_kot_status(
    kot_id: uuid.UUID,
    payload: KOTStatusUpdate,
    user: CurrentUser = Depends(board_roles),
    db: AsyncSession = Depends(get_db),
):
    """
    Advance KOT status (queued -> preparing -> ready -> served). The kitchen runs the first
    stages; floor staff may only mark a ready ticket served.
    """
    return await service.update_kot_status(db, user, kot_id, payload.status)


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
