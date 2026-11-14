"""
Inventory: how much of each item a branch has, and every change to it.

A branch tracks an item once it has a stock level for it (created by the first purchase, adjustment or by switching tracking
on). Stock changes only through `_move`, which writes one ledger row per change, so the quantity on record is always the sum of
the ledger. Paid bills take their items out of stock (see `deduct_for_order`, called by billing when a bill is settled).

Stock is counted per item: an item's variants (half / full) share its stock. A sale is never refused for lack of stock; the
quantity goes negative and shows up as something to recount.
"""

import uuid
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_branch_access, resolve_branch_id
from app.core.clock import business_date
from app.core.enums import OrderItemStatus, StockMovementKind, Topic
from app.core.errors import NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.pagination import PageParams
from app.models.catalog import Category, MenuItem
from app.models.floor import Branch
from app.models.inventory import StockLevel, StockMovement, StockPurchase
from app.models.orders import Order
from app.models.staff import StaffUser
from app.schemas.inventory import PurchaseCreate, StockAdjust, StockSettingsUpdate

NOTHING = Decimal("0")


def _is_low(level: StockLevel) -> bool:
    return level.is_tracked and level.low_stock_level is not None and level.quantity <= level.low_stock_level


def _stock_row(level: StockLevel, item: MenuItem, category_name: str, with_cost: bool) -> Dict[str, Any]:
    cost = level.cost_price if with_cost else None
    return {
        "item_id": item.id, "name": item.name, "category_id": item.category_id, "category_name": category_name,
        "unit": item.unit, "sku": item.sku, "barcode": item.barcode, "quantity": level.quantity,
        "low_stock_level": level.low_stock_level, "is_low": _is_low(level),
        "is_out": level.is_tracked and level.quantity <= 0, "is_tracked": level.is_tracked, "cost_price": cost,
        "stock_value": q(max(level.quantity, NOTHING) * cost) if cost is not None else None,
        "updated_at": level.updated_at,
    }


async def list_stock(
    db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID], *, low_only: bool = False,
    include_untracked: bool = False, search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = (
        select(StockLevel, MenuItem, Category.name)
        .join(MenuItem, MenuItem.id == StockLevel.item_id).join(Category, Category.id == MenuItem.category_id)
        .where(StockLevel.branch_id == branch_id, MenuItem.organization_id == user.organization_id,
               MenuItem.is_active.is_(True))
        .order_by(MenuItem.name)
    )
    if not include_untracked:
        stmt = stmt.where(StockLevel.is_tracked.is_(True))
    if low_only:
        stmt = stmt.where(StockLevel.is_tracked.is_(True),
                          or_(StockLevel.quantity <= 0, StockLevel.quantity <= StockLevel.low_stock_level))
    if search and search.strip():
        term = search.strip()
        stmt = stmt.where(or_(MenuItem.name.ilike(f"%{term}%"), MenuItem.barcode == term, MenuItem.sku == term))
    return [_stock_row(level, item, category, user.is_manager) for level, item, category in (await db.execute(stmt)).all()]


async def _read_one(db: AsyncSession, user: CurrentUser, level: StockLevel) -> Dict[str, Any]:
    await db.refresh(level)
    item, category = (await db.execute(
        select(MenuItem, Category.name).join(Category, Category.id == MenuItem.category_id).where(MenuItem.id == level.item_id)
    )).one()
    return _stock_row(level, item, category, user.is_manager)


async def _items(db: AsyncSession, org_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, MenuItem]:
    """The organization's items by id; an id from elsewhere (or nowhere) is a 404."""
    wanted = set(item_ids)
    found = {i.id: i for i in (await db.scalars(
        select(MenuItem).where(MenuItem.id.in_(wanted), MenuItem.organization_id == org_id))).all()}
    missing = wanted - set(found)
    if missing:
        raise NotFound("Item not found", details={"item_ids": sorted(map(str, missing))})
    return found


def _require_whole(item: MenuItem, quantity: Decimal) -> None:
    if not item.sold_by_weight and quantity != quantity.to_integral_value():
        raise ValidationFailed(f"{item.name} is counted in whole numbers", code="whole_quantity_required",
                               details={"item_id": str(item.id), "quantity": str(quantity)})


async def _lock_levels(
    db: AsyncSession, branch_id: uuid.UUID, item_ids: Iterable[uuid.UUID], *, create: bool, tracked_only: bool = False,
) -> Dict[uuid.UUID, StockLevel]:
    """Lock the branch's stock levels for these items, always in the same order so two sales cannot deadlock."""
    ids = sorted(set(item_ids))
    if not ids:
        return {}
    if create:
        await db.execute(
            pg_insert(StockLevel)
            .values([{"id": uuid.uuid4(), "branch_id": branch_id, "item_id": i} for i in ids])
            .on_conflict_do_nothing(constraint="uq_stock_levels_branch_item")
        )
    stmt = (select(StockLevel).where(StockLevel.branch_id == branch_id, StockLevel.item_id.in_(ids))
            .order_by(StockLevel.item_id).with_for_update().execution_options(populate_existing=True))
    if tracked_only:
        stmt = stmt.where(StockLevel.is_tracked.is_(True))
    return {level.item_id: level for level in (await db.scalars(stmt)).all()}


def _move(
    db: AsyncSession, level: StockLevel, kind: StockMovementKind, delta: Decimal, *, user_id: Optional[uuid.UUID],
    reason: Optional[str] = None, unit_cost: Optional[Decimal] = None, purchase_id: Optional[uuid.UUID] = None,
    order_id: Optional[uuid.UUID] = None,
) -> None:
    """Change a (locked) stock level and write the ledger row. Announces the item the moment it becomes low."""
    was_low = _is_low(level)
    level.quantity = level.quantity + delta
    db.add(StockMovement(
        branch_id=level.branch_id, item_id=level.item_id, kind=kind.value, quantity=delta, balance_after=level.quantity,
        unit_cost=unit_cost, reason=reason, purchase_id=purchase_id, order_id=order_id, created_by_id=user_id,
    ))
    if _is_low(level) and not was_low:
        queue_event(db, level.branch_id, Topic.CATALOG, "stock.low", level.item_id,
                    {"item_id": str(level.item_id), "quantity": str(level.quantity),
                     "low_stock_level": str(level.low_stock_level)})


def _announce(db: AsyncSession, branch_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> None:
    queue_event(db, branch_id, Topic.CATALOG, "stock.changed", None, {"item_ids": sorted(str(i) for i in set(item_ids))})


# --- settings ----------------------------------------------------------------------------------------------------------------


async def update_settings(db: AsyncSession, user: CurrentUser, item_id: uuid.UUID, payload: StockSettingsUpdate) -> Dict[str, Any]:
    branch_id = resolve_branch_id(user, payload.branch_id)
    await _items(db, user.organization_id, [item_id])
    level = (await _lock_levels(db, branch_id, [item_id], create=True))[item_id]
    for field, value in payload.model_dump(exclude_unset=True, exclude={"branch_id"}).items():
        if field == "is_tracked" and value is None:
            continue
        setattr(level, field, value)
    _announce(db, branch_id, [item_id])
    await commit(db)
    return await _read_one(db, user, level)


# --- purchases ---------------------------------------------------------------------------------------------------------------


async def receive_purchase(db: AsyncSession, user: CurrentUser, payload: PurchaseCreate) -> Dict[str, Any]:
    """Record a delivery: every line adds to stock, and a line's unit cost becomes the item's cost price."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    branch = await get_or_404(db, Branch, branch_id)
    items = await _items(db, user.organization_id, [line.item_id for line in payload.lines])
    for line in payload.lines:
        _require_whole(items[line.item_id], line.quantity)

    purchase = StockPurchase(
        id=uuid.uuid4(), branch_id=branch_id, supplier_name=payload.supplier_name, invoice_number=payload.invoice_number,
        received_on=payload.received_on or business_date(branch.timezone), notes=payload.notes, created_by_id=user.id,
        total_cost=q(sum((line.quantity * line.unit_cost for line in payload.lines if line.unit_cost is not None), ZERO)),
    )
    db.add(purchase)
    await db.flush()
    levels = await _lock_levels(db, branch_id, items, create=True)
    for line in payload.lines:
        level = levels[line.item_id]
        level.is_tracked = True
        if line.unit_cost is not None:
            level.cost_price = line.unit_cost
        _move(db, level, StockMovementKind.PURCHASE, line.quantity, user_id=user.id, unit_cost=line.unit_cost,
              purchase_id=purchase.id)
    _announce(db, branch_id, items)
    await commit(db)
    return await get_purchase(db, user, purchase.id)


async def _purchase_rows(db: AsyncSession, purchases: Sequence[StockPurchase]) -> List[Dict[str, Any]]:
    if not purchases:
        return []
    ids = [p.id for p in purchases]
    lines: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for move, name, unit in (await db.execute(
        select(StockMovement, MenuItem.name, MenuItem.unit).join(MenuItem, MenuItem.id == StockMovement.item_id)
        .where(StockMovement.purchase_id.in_(ids)).order_by(StockMovement.created_at, MenuItem.name)
    )).all():
        lines.setdefault(move.purchase_id, []).append({
            "item_id": move.item_id, "name": name, "unit": unit, "quantity": move.quantity, "unit_cost": move.unit_cost,
            "line_cost": q(move.quantity * move.unit_cost) if move.unit_cost is not None else None,
        })
    staff_ids = {p.created_by_id for p in purchases if p.created_by_id}
    names = dict((await db.execute(select(StaffUser.id, StaffUser.full_name).where(StaffUser.id.in_(staff_ids)))).all()) \
        if staff_ids else {}
    return [{
        "id": p.id, "branch_id": p.branch_id, "supplier_name": p.supplier_name, "invoice_number": p.invoice_number,
        "received_on": p.received_on, "notes": p.notes, "total_cost": p.total_cost, "created_by": names.get(p.created_by_id),
        "created_at": p.created_at, "lines": lines.get(p.id, []),
    } for p in purchases]


async def get_purchase(db: AsyncSession, user: CurrentUser, purchase_id: uuid.UUID) -> Dict[str, Any]:
    purchase = await get_or_404(db, StockPurchase, purchase_id)
    require_branch_access(user, purchase.branch_id)
    return (await _purchase_rows(db, [purchase]))[0]


async def list_purchases(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID],
) -> Tuple[List[Dict[str, Any]], int]:
    branch_id = resolve_branch_id(user, branch_id)
    where = StockPurchase.branch_id == branch_id
    total = await db.scalar(select(func.count()).select_from(StockPurchase).where(where))
    purchases = (await db.scalars(
        select(StockPurchase).where(where).order_by(StockPurchase.received_on.desc(), StockPurchase.created_at.desc())
        .limit(params.limit).offset(params.offset)
    )).all()
    return await _purchase_rows(db, purchases), int(total or 0)


# --- adjustments and counts --------------------------------------------------------------------------------------------------


async def adjust(db: AsyncSession, user: CurrentUser, payload: StockAdjust) -> Dict[str, Any]:
    branch_id = resolve_branch_id(user, payload.branch_id)
    item = (await _items(db, user.organization_id, [payload.item_id]))[payload.item_id]
    _require_whole(item, payload.delta if payload.delta is not None else payload.counted)
    level = (await _lock_levels(db, branch_id, [item.id], create=True))[item.id]
    level.is_tracked = True
    if payload.counted is not None:
        kind, delta = StockMovementKind.COUNT, payload.counted - level.quantity
    else:
        kind, delta = StockMovementKind.ADJUSTMENT, payload.delta
    if delta != 0:  # a count that matches the record changes nothing and leaves no ledger row
        _move(db, level, kind, delta, user_id=user.id, reason=payload.reason)
    _announce(db, branch_id, [item.id])
    await commit(db)
    return await _read_one(db, user, level)


# --- ledger ------------------------------------------------------------------------------------------------------------------


async def list_movements(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID],
    item_id: Optional[uuid.UUID], kind: Optional[StockMovementKind],
) -> Tuple[List[Dict[str, Any]], int]:
    branch_id = resolve_branch_id(user, branch_id)
    where = [StockMovement.branch_id == branch_id]
    if item_id is not None:
        where.append(StockMovement.item_id == item_id)
    if kind is not None:
        where.append(StockMovement.kind == kind.value)
    total = await db.scalar(select(func.count()).select_from(StockMovement).where(*where))
    rows = (await db.execute(
        select(StockMovement, MenuItem.name, MenuItem.unit, StaffUser.full_name)
        .join(MenuItem, MenuItem.id == StockMovement.item_id)
        .outerjoin(StaffUser, StaffUser.id == StockMovement.created_by_id)
        .where(*where).order_by(StockMovement.created_at.desc(), StockMovement.id)
        .limit(params.limit).offset(params.offset)
    )).all()
    return [{
        "id": m.id, "item_id": m.item_id, "name": name, "unit": unit, "kind": m.kind, "quantity": m.quantity,
        "balance_after": m.balance_after, "reason": m.reason, "purchase_id": m.purchase_id, "order_id": m.order_id,
        "created_by": staff, "created_at": m.created_at,
    } for m, name, unit, staff in rows], int(total or 0)


# --- sales -------------------------------------------------------------------------------------------------------------------


async def deduct_for_order(db: AsyncSession, order: Order, user_id: Optional[uuid.UUID] = None) -> None:
    """
    Take a paid order's items out of stock, inside the transaction that settles its bill. Only items the branch tracks are
    touched, so a business without inventory pays one cheap query. An order is deducted once: a card payment that is reversed
    and collected again does not take the goods out twice.

    This does not ask whether the `inventory` module is on. A branch has tracked items only if it used the module, and keeping
    the count right through a lapsed subscription is what the owner wants when it comes back.
    """
    sold: Dict[uuid.UUID, Decimal] = {}
    for line in order.items:
        if line.status != OrderItemStatus.CANCELLED.value:
            sold[line.item_id] = sold.get(line.item_id, NOTHING) + line.quantity
    levels = await _lock_levels(db, order.branch_id, sold, create=False, tracked_only=True)
    if not levels:
        return
    already = await db.scalar(
        select(StockMovement.id).where(StockMovement.order_id == order.id, StockMovement.kind == StockMovementKind.SALE.value)
        .limit(1))
    if already is not None:
        return
    for item_id, level in levels.items():
        _move(db, level, StockMovementKind.SALE, -sold[item_id], user_id=user_id, order_id=order.id)
    _announce(db, order.branch_id, levels)
