import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_module, require_roles
from app.core.database import get_db
from app.core.enums import Role, StockMovementKind
from app.core.idempotency import IdempotencyGuard, idempotent
from app.core.modules import ModuleKey
from app.core.pagination import PageParams, page_params
from app.modules.inventory import service
from app.schemas.common import Page
from app.schemas.inventory import (
    MovementRead,
    PurchaseCreate,
    PurchaseRead,
    StockAdjust,
    StockItemRead,
    StockSettingsUpdate,
)

router = APIRouter(
    prefix="/inventory",
    tags=["Inventory"],
    dependencies=[Depends(require_module(ModuleKey.INVENTORY))],
)

managers = require_roles(Role.OWNER, Role.MANAGER)


@router.get("/stock", response_model=List[StockItemRead])
async def list_stock(
    branch_id: Optional[uuid.UUID] = None,
    low_only: bool = False,
    include_untracked: bool = False,
    search: Optional[str] = Query(default=None, max_length=100),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Stock on hand for the branch's tracked items. `low_only` keeps items at or below their low-stock level or with nothing
    left. Every signed-in member of staff may read it (the counter shows what is left); cost and value go to managers only.
    """
    return await service.list_stock(db, user, branch_id, low_only=low_only, include_untracked=include_untracked, search=search)


@router.put("/stock/{item_id}", response_model=StockItemRead)
async def update_stock_settings(
    item_id: uuid.UUID, payload: StockSettingsUpdate, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db),
):
    """Start or stop tracking an item in a branch, and set its low-stock level and cost price."""
    return await service.update_settings(db, user, item_id, payload)


@router.post("/purchases", response_model=PurchaseRead, status_code=status.HTTP_201_CREATED)
async def receive_purchase(
    payload: PurchaseCreate,
    user: CurrentUser = Depends(managers),
    guard: IdempotencyGuard = Depends(idempotent("stock_purchases")),
    db: AsyncSession = Depends(get_db),
):
    """
    Record stock received from a supplier; every line adds to stock. Requires X-Idempotency-Key, so a retry cannot add the
    delivery twice. A purchase is not edited afterwards: correct a mistake with an adjustment.
    """
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        purchase = await service.receive_purchase(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_201_CREATED, PurchaseRead.model_validate(purchase))


@router.get("/purchases", response_model=Page[PurchaseRead])
async def list_purchases(
    branch_id: Optional[uuid.UUID] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Deliveries received, newest first."""
    items, total = await service.list_purchases(db, user, params, branch_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.get("/purchases/{purchase_id}", response_model=PurchaseRead)
async def get_purchase(purchase_id: uuid.UUID, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    return await service.get_purchase(db, user, purchase_id)


@router.post("/adjustments", response_model=StockItemRead)
async def adjust_stock(
    payload: StockAdjust,
    user: CurrentUser = Depends(managers),
    guard: IdempotencyGuard = Depends(idempotent("stock_adjustments")),
    db: AsyncSession = Depends(get_db),
):
    """
    Correct stock by hand with a reason: `delta` adds or removes, `counted` sets the quantity to a physical count.
    Requires X-Idempotency-Key.
    """
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        row = await service.adjust(db, user, payload)
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status.HTTP_200_OK, StockItemRead.model_validate(row))


@router.get("/movements", response_model=Page[MovementRead])
async def list_movements(
    branch_id: Optional[uuid.UUID] = None,
    item_id: Optional[uuid.UUID] = None,
    kind: Optional[StockMovementKind] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """The stock ledger, newest first: every purchase, sale, adjustment and count."""
    items, total = await service.list_movements(db, user, params, branch_id, item_id, kind)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)
