"""
Hardware shop stock: how much of each product a branch has, in its base unit, and every change to it.

Stock changes only through `move`, which writes one ledger row per change, so a product's quantity is always the sum of its
ledger. A missing stock row means nothing on hand; rows are made lazily and locked in a fixed order (sorted product id) so two
requests cannot deadlock. Quantities may go below zero: a sale is never refused because the count is behind the shelf.

A delivery is typed in any unit of the product (2 coils at 3,000.00); the service converts it to base units (180 m) and the cost to
a cost per base unit (33.3333 a metre). Adjustments and counts are in the base unit.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_branch_access, resolve_branch_id
from app.core.clock import business_date
from app.core.enums import HardwareMovementKind, Topic
from app.core.errors import NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.pagination import PageParams
from app.models.floor import Branch
from app.models.hardware import (
    HardwareCategory,
    HardwareProduct,
    HardwarePurchase,
    HardwareStock,
    HardwareStockMovement,
)
from app.models.staff import StaffUser
from app.modules.hardware import catalog, pricing
from app.schemas.hardware import HardwarePurchaseCreate, HardwareStockAdjust, HardwareStockSettings

TENTH_MILLI = Decimal("0.0001")


def is_low(level: HardwareStock) -> bool:
    return level.low_stock_level is not None and level.quantity <= level.low_stock_level


async def lock_levels(db: AsyncSession, branch_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, HardwareStock]:
    """The branch's stock rows for these products, made if missing, locked in product-id order."""
    ids = sorted(set(product_ids))
    if not ids:
        return {}
    await db.execute(pg_insert(HardwareStock).values([{"id": uuid.uuid4(), "branch_id": branch_id, "product_id": p} for p in ids])
                     .on_conflict_do_nothing(constraint="uq_hardware_stock_branch_product"))
    rows = (await db.scalars(
        select(HardwareStock).where(HardwareStock.branch_id == branch_id, HardwareStock.product_id.in_(ids))
        .order_by(HardwareStock.product_id).with_for_update().execution_options(populate_existing=True)
    )).all()
    return {r.product_id: r for r in rows}


def move(
    db: AsyncSession, level: HardwareStock, kind: HardwareMovementKind, delta: Decimal, *, user_id: Optional[uuid.UUID],
    reason: Optional[str] = None, unit_cost: Any = None, purchase_id: Optional[uuid.UUID] = None, **refs: Any,
) -> None:
    """Change a locked stock row and write its ledger row. Announces the product the moment it becomes low."""
    if delta == 0:
        return
    was_low = is_low(level)
    level.quantity = level.quantity + delta
    db.add(HardwareStockMovement(
        branch_id=level.branch_id, product_id=level.product_id, kind=kind.value, quantity=delta, balance_after=level.quantity,
        unit_cost=unit_cost, reason=reason, purchase_id=purchase_id, created_by_id=user_id, **refs,
    ))
    if is_low(level) and not was_low:
        queue_event(db, level.branch_id, Topic.CATALOG, "hardware.stock.low", level.product_id,
                    {"product_id": str(level.product_id), "quantity": str(level.quantity),
                     "low_stock_level": str(level.low_stock_level)})


def announce(db: AsyncSession, branch_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> None:
    queue_event(db, branch_id, Topic.CATALOG, "hardware.stock.changed", None,
                {"product_ids": sorted(str(p) for p in set(product_ids))})


async def products_of(db: AsyncSession, org_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, HardwareProduct]:
    """The organization's products (with their units) by id; an id from elsewhere (or nowhere) is a 404."""
    wanted = set(product_ids)
    found = {p.id: p for p in (await db.scalars(
        select(HardwareProduct).where(HardwareProduct.id.in_(wanted), HardwareProduct.organization_id == org_id)
        .options(*catalog.PRODUCT_LOADERS).execution_options(populate_existing=True))).all()}
    missing = wanted - set(found)
    if missing:
        raise NotFound("Product not found", code="product_not_found", details={"product_ids": sorted(map(str, missing))})
    return found


def _require_whole(product: HardwareProduct, quantity: Decimal) -> None:
    if not product.allow_fraction and quantity != quantity.to_integral_value():
        raise ValidationFailed("This product is counted in whole pieces only", code="whole_quantity_required",
                               details={"product_id": str(product.id), "quantity": str(quantity)})


# --- reading stock -----------------------------------------------------------------------------------------------------------


def _row(level: Optional[HardwareStock], product: HardwareProduct, category: str, with_cost: bool) -> Dict[str, Any]:
    quantity = level.quantity if level else ZERO
    cost = level.cost_price if (level and with_cost) else None
    return {
        "product_id": product.id, "product_name": product.name, "brand": product.brand, "size_spec": product.size_spec,
        "category_id": product.category_id, "category_name": category, "sku": product.sku, "barcode": product.barcode,
        "base_unit": product.base_unit, "allow_fraction": product.allow_fraction, "price": product.price, "quantity": quantity,
        "low_stock_level": level.low_stock_level if level else None, "is_low": bool(level and is_low(level)),
        "is_out": quantity <= 0, "cost_price": cost,
        "stock_value": q(max(quantity, ZERO) * cost) if cost is not None else None,
        "units": list(product.units),
    }


async def list_stock(
    db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID], *, search: Optional[str], low_only: bool,
    category_id: Optional[uuid.UUID], product_id: Optional[uuid.UUID] = None,
) -> List[Dict[str, Any]]:
    """Every active product with its stock in the branch (never stocked reads as 0). Cost goes to managers only."""
    branch_id = resolve_branch_id(user, branch_id)
    stmt = (
        select(HardwareProduct, HardwareCategory.name, HardwareStock)
        .join(HardwareCategory, HardwareCategory.id == HardwareProduct.category_id)
        .outerjoin(HardwareStock, (HardwareStock.product_id == HardwareProduct.id) & (HardwareStock.branch_id == branch_id))
        .where(HardwareProduct.organization_id == user.organization_id, HardwareProduct.is_active.is_(True))
        .options(*catalog.PRODUCT_LOADERS)
        .order_by(HardwareProduct.name, HardwareProduct.size_spec, HardwareProduct.id)
    )
    if category_id is not None:
        stmt = stmt.where(HardwareProduct.category_id == category_id)
    if product_id is not None:
        stmt = stmt.where(HardwareProduct.id == product_id)
    if search and search.strip():
        stmt = stmt.where(catalog._matches(user.organization_id, search.strip()))
    if low_only:
        stmt = stmt.where(or_(HardwareStock.id.is_(None), HardwareStock.quantity <= 0,
                              HardwareStock.quantity <= HardwareStock.low_stock_level))
    return [_row(level, p, c, user.is_manager) for p, c, level in (await db.execute(stmt)).all()]


async def _read_one(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID, product_id: uuid.UUID) -> Dict[str, Any]:
    product = (await products_of(db, user.organization_id, [product_id]))[product_id]
    category = await db.scalar(select(HardwareCategory.name).where(HardwareCategory.id == product.category_id))
    level = await db.scalar(select(HardwareStock).where(HardwareStock.branch_id == branch_id, HardwareStock.product_id == product_id)
                            .execution_options(populate_existing=True))
    return _row(level, product, category or "", user.is_manager)


async def update_settings(db: AsyncSession, user: CurrentUser, product_id: uuid.UUID,
                          payload: HardwareStockSettings) -> Dict[str, Any]:
    """The low-stock level and cost price (per base unit) of a product in a branch."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    product = (await products_of(db, user.organization_id, [product_id]))[product_id]
    changes = payload.model_dump(exclude_unset=True, exclude={"branch_id"})
    if changes.get("low_stock_level") is not None:
        _require_whole(product, changes["low_stock_level"])
    level = (await lock_levels(db, branch_id, [product_id]))[product_id]
    for field, value in changes.items():
        setattr(level, field, value)
    announce(db, branch_id, [product_id])
    await commit(db)
    return await _read_one(db, user, branch_id, product_id)


# --- purchases ---------------------------------------------------------------------------------------------------------------


def _factor_of(product: HardwareProduct, unit: Optional[str]) -> Tuple[str, Decimal]:
    """The unit a line was typed in and how many base units one of it holds."""
    if unit is None or unit == product.base_unit:
        return product.base_unit, pricing.ONE
    found = next((u for u in product.units if u.unit == unit), None)
    if found is None:
        raise ValidationFailed(f"{product.name} is not bought or sold in {unit}", code="unit_not_available",
                               details={"product_id": str(product.id), "unit": str(unit)})
    return found.unit, found.factor


async def receive_purchase(db: AsyncSession, user: CurrentUser, payload: HardwarePurchaseCreate) -> Dict[str, Any]:
    """
    A delivery: every line adds to stock in base units, and a line's cost per base unit becomes the product's cost price in the
    branch. The delivery's total is the sum of each line's quantity x cost in the unit it was typed in.
    """
    branch_id = resolve_branch_id(user, payload.branch_id)
    branch = await get_or_404(db, Branch, branch_id)
    products = await products_of(db, user.organization_id, [line.product_id for line in payload.lines])
    prepared = []
    for line in payload.lines:
        product = products[line.product_id]
        unit, factor = _factor_of(product, line.unit.value if line.unit else None)
        base = pricing.base_quantity(line.quantity, factor, allow_fraction=product.allow_fraction)
        base_cost = (line.unit_cost / factor).quantize(TENTH_MILLI, rounding=ROUND_HALF_UP) if line.unit_cost is not None else None
        line_cost = q(line.quantity * line.unit_cost) if line.unit_cost is not None else None
        prepared.append((line, unit, base, base_cost, line_cost))
    purchase = HardwarePurchase(
        id=uuid.uuid4(), branch_id=branch_id, supplier_name=payload.supplier_name, invoice_number=payload.invoice_number,
        received_on=payload.received_on or business_date(branch.timezone), notes=payload.notes, created_by_id=user.staff_id,
        total_cost=sum((cost for *_, cost in prepared if cost is not None), ZERO),
    )
    db.add(purchase)
    await db.flush()
    levels = await lock_levels(db, branch_id, [line.product_id for line in payload.lines])
    for line, unit, base, base_cost, _ in prepared:
        level = levels[line.product_id]
        if base_cost is not None:
            level.cost_price = base_cost
        move(db, level, HardwareMovementKind.PURCHASE, base, user_id=user.staff_id, unit_cost=base_cost,
             purchase_id=purchase.id, entered_unit=unit, entered_quantity=line.quantity, entered_unit_cost=line.unit_cost)
    announce(db, branch_id, levels)
    await commit(db)
    return await get_purchase(db, user, purchase.id)


async def _staff_names(db: AsyncSession, ids: Iterable[Optional[uuid.UUID]]) -> Dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return dict((await db.execute(select(StaffUser.id, StaffUser.full_name).where(StaffUser.id.in_(wanted)))).all())


async def _purchase_rows(db: AsyncSession, purchases: Sequence[HardwarePurchase]) -> List[Dict[str, Any]]:
    if not purchases:
        return []
    lines: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for m, name, size_spec, base_unit in (await db.execute(
        select(HardwareStockMovement, HardwareProduct.name, HardwareProduct.size_spec, HardwareProduct.base_unit)
        .join(HardwareProduct, HardwareProduct.id == HardwareStockMovement.product_id)
        .where(HardwareStockMovement.purchase_id.in_([p.id for p in purchases]))
        .order_by(HardwareStockMovement.created_at, HardwareProduct.name, HardwareStockMovement.id)
    )).all():
        lines.setdefault(m.purchase_id or uuid.UUID(int=0), []).append({
            "product_id": m.product_id, "product_name": name, "size_spec": size_spec,
            "unit": m.entered_unit or base_unit,
            "quantity": m.entered_quantity if m.entered_quantity is not None else m.quantity,
            "unit_cost": m.entered_unit_cost,
            "line_cost": q(m.entered_quantity * m.entered_unit_cost)
            if m.entered_quantity is not None and m.entered_unit_cost is not None else None,
            "base_unit": base_unit, "base_quantity": m.quantity, "base_unit_cost": m.unit_cost,
        })
    names = await _staff_names(db, [p.created_by_id for p in purchases])
    return [{
        "id": p.id, "branch_id": p.branch_id, "supplier_name": p.supplier_name, "invoice_number": p.invoice_number,
        "received_on": p.received_on, "notes": p.notes, "total_cost": p.total_cost,
        "created_by": names.get(p.created_by_id) if p.created_by_id else None, "created_at": p.created_at,
        "lines": lines.get(p.id, []),
    } for p in purchases]


async def get_purchase(db: AsyncSession, user: CurrentUser, purchase_id: uuid.UUID) -> Dict[str, Any]:
    purchase = await get_or_404(db, HardwarePurchase, purchase_id)
    require_branch_access(user, purchase.branch_id)
    return (await _purchase_rows(db, [purchase]))[0]


async def list_purchases(db: AsyncSession, user: CurrentUser, params: PageParams,
                         branch_id: Optional[uuid.UUID]) -> Tuple[List[Dict[str, Any]], int]:
    branch_id = resolve_branch_id(user, branch_id)
    where = HardwarePurchase.branch_id == branch_id
    total = await db.scalar(select(func.count()).select_from(HardwarePurchase).where(where))
    purchases = (await db.scalars(
        select(HardwarePurchase).where(where).order_by(HardwarePurchase.received_on.desc(), HardwarePurchase.created_at.desc())
        .limit(params.limit).offset(params.offset))).all()
    return await _purchase_rows(db, purchases), int(total or 0)


# --- adjustments and counts --------------------------------------------------------------------------------------------------


async def adjust(db: AsyncSession, user: CurrentUser, payload: HardwareStockAdjust) -> Dict[str, Any]:
    """`delta` adds or removes base units (damage, a found carton); `counted` replaces the quantity with a physical count."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    product = (await products_of(db, user.organization_id, [payload.product_id]))[payload.product_id]
    _require_whole(product, payload.counted if payload.counted is not None else payload.delta)
    level = (await lock_levels(db, branch_id, [payload.product_id]))[payload.product_id]
    if payload.counted is not None:
        kind, delta = HardwareMovementKind.COUNT, payload.counted - level.quantity
    else:
        assert payload.delta is not None
        kind, delta = HardwareMovementKind.ADJUSTMENT, payload.delta
    move(db, level, kind, delta, user_id=user.staff_id, reason=payload.reason)  # a count that matches writes nothing
    announce(db, branch_id, [payload.product_id])
    await commit(db)
    return await _read_one(db, user, branch_id, payload.product_id)


# --- the ledger --------------------------------------------------------------------------------------------------------------


async def list_movements(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID],
    product_id: Optional[uuid.UUID], kind: Optional[HardwareMovementKind],
) -> Tuple[List[Dict[str, Any]], int]:
    branch_id = resolve_branch_id(user, branch_id)
    where = [HardwareStockMovement.branch_id == branch_id]
    if product_id is not None:
        where.append(HardwareStockMovement.product_id == product_id)
    if kind is not None:
        where.append(HardwareStockMovement.kind == kind.value)
    total = await db.scalar(select(func.count()).select_from(HardwareStockMovement).where(*where))
    rows = (await db.execute(
        select(HardwareStockMovement, HardwareProduct.name, HardwareProduct.size_spec, HardwareProduct.base_unit, StaffUser.full_name)
        .join(HardwareProduct, HardwareProduct.id == HardwareStockMovement.product_id)
        .outerjoin(StaffUser, StaffUser.id == HardwareStockMovement.created_by_id)
        .where(*where).order_by(HardwareStockMovement.created_at.desc(), HardwareStockMovement.id)
        .limit(params.limit).offset(params.offset)
    )).all()
    return [{
        "id": m.id, "product_id": m.product_id, "product_name": name, "size_spec": size_spec, "base_unit": base_unit,
        "kind": m.kind, "quantity": m.quantity, "balance_after": m.balance_after,
        "unit_cost": m.unit_cost if user.is_manager else None, "reason": m.reason, "purchase_id": m.purchase_id,
        "created_by": staff, "created_at": m.created_at,
    } for m, name, size_spec, base_unit, staff in rows], int(total or 0)
