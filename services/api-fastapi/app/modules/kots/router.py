import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from app.core.database import get_db
from app.core.redis import get_redis
from app.core.idempotency import verify_idempotency_key
from app.schemas.kots import (
    KOTCreate,
    KOTRead,
    KOTStatusUpdate,
    VoidKOTRequest,
)

router = APIRouter(prefix="/kots", tags=["Kitchen Order Tickets (KOT)"])


@router.get("", response_model=List[KOTRead])
async def list_kots(
    station: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List active KOTs filtered by station (kitchen, bar, grill, dessert) and status."""
    return []


@router.post("/fire", response_model=List[KOTRead], status_code=status.HTTP_201_CREATED)
async def fire_kot_batch(
    payload: KOTCreate,
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    Fire KOT batch from handheld/POS tablet.
    Automatically splits line items across respective prep stations (e.g., kitchen vs bar).
    Generates daily atomic sequential KOT numbers via Redis.
    """
    await verify_idempotency_key(redis_client, idempotency_key, scope="kots")
    raise NotImplementedError("Feature implementation pending")


@router.patch("/{kot_id}/status", response_model=KOTRead)
async def update_kot_status(
    kot_id: uuid.UUID,
    payload: KOTStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Advance KOT status (queued -> preparing -> ready -> served)."""
    raise NotImplementedError("Feature implementation pending")


@router.post("/{kot_id}/void", response_model=KOTRead)
async def void_kot(
    kot_id: uuid.UUID,
    payload: VoidKOTRequest,
    db: AsyncSession = Depends(get_db),
):
    """Void a KOT with mandatory manager authorization and reason tracking for fraud audit."""
    raise NotImplementedError("Feature implementation pending")
