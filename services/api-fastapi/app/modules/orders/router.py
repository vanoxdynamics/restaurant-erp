import uuid
from datetime import date
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import OrderStatus, Role
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.pagination import PageParams, page_params
from app.core.redis import get_redis
from app.modules.orders import service
from app.schemas.common import Page
from app.schemas.orders import (
    OrderCancelRequest,
    OrderCreate,
    OrderItemCancelRequest,
    OrderItemsAppend,
    OrderRead,
    OrderUpdate,
)

router = APIRouter(
    prefix="/orders",
    tags=["Order Lifecycle"],
    dependencies=[Depends(get_current_user)],
)

order_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER)


@router.get("", response_model=Page[OrderRead])
async def list_orders(
    branch_id: Optional[uuid.UUID] = None,
    status: Optional[OrderStatus] = None,
    table_id: Optional[uuid.UUID] = None,
    business_date: Optional[date] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List active and historical orders with line-item snapshots."""
    items, total = await service.list_orders(db, user, params, branch_id, status, table_id, business_date)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    user: CurrentUser = Depends(order_roles),
    guard: IdempotencyGuard = Depends(idempotent("orders")),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new order with line items and price snapshots.
    Requires an X-Idempotency-Key header; retries replay the original response.
    """
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        order = await service.create_order(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, OrderRead.model_validate(order))


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve an order by ID."""
    return await service.get_order(db, user, order_id)


@router.patch("/{order_id}", response_model=OrderRead)
async def update_order(
    order_id: uuid.UUID,
    payload: OrderUpdate,
    user: CurrentUser = Depends(order_roles),
    db: AsyncSession = Depends(get_db),
):
    """
    Update guest count / notes with optimistic locking.
    Fails with HTTP 409 if the version has changed in the meantime.
    """
    return await service.update_order(db, user, order_id, payload)


@router.post("/{order_id}/items", response_model=OrderRead)
async def add_order_items(
    order_id: uuid.UUID,
    payload: OrderItemsAppend,
    user: CurrentUser = Depends(order_roles),
    db: AsyncSession = Depends(get_db),
):
    """Append new items to an active running dining table order."""
    return await service.add_order_items(db, user, order_id, payload)


@router.post("/{order_id}/items/{item_id}/cancel", response_model=OrderRead)
async def cancel_order_item(
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: OrderItemCancelRequest,
    user: CurrentUser = Depends(order_roles),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Cancel one line. Items already sent to the kitchen need manager approval."""
    return await service.cancel_order_item(db, user, order_id, item_id, payload, redis_client)


@router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    order_id: uuid.UUID,
    payload: OrderCancelRequest,
    user: CurrentUser = Depends(order_roles),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Cancel an unbilled order (manager approval required); voids open KOTs and frees the table."""
    return await service.cancel_order(db, user, order_id, payload, redis_client)
