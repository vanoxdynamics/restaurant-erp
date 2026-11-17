"""
Dress shop stock: how many pieces of each variant a branch has, and every change to it.

Stock changes only through `move`, which writes one ledger row per change, so a variant's quantity is always the sum of its
ledger. A missing stock row means nothing on hand; rows are made lazily and locked in a fixed order (sorted variant id) so two
sales cannot deadlock. Quantities may go below zero: a sale is never refused because the count is behind the shelf.
"""

import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_branch_access, resolve_branch_id
from app.core.clock import business_date
from app.core.enums import ApparelMovementKind, Topic
from app.core.errors import NotFound
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.pagination import PageParams
from app.models.apparel import (
    ApparelCategory,
    ApparelProduct,
    ApparelPurchase,
    ApparelStock,
    ApparelStockMovement,
    ApparelVariant,
)
from app.models.floor import Branch
from app.models.staff import StaffUser
from app.schemas.apparel import ApparelPurchaseCreate, ApparelStockAdjust, ApparelStockSettings


def is_low(level: ApparelStock) -> bool:
    return level.low_stock_level is not None and level.quantity <= level.low_stock_level


async def lock_levels(db: AsyncSession, branch_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, ApparelStock]:
    """The branch's stock rows for these variants, made if missing, locked in variant-id order."""
    ids = sorted(set(variant_ids))
    if not ids:
        return {}
    await db.execute(pg_insert(ApparelStock).values([{"id": uuid.uuid4(), "branch_id": branch_id, "variant_id": v} for v in ids])
                     .on_conflict_do_nothing(constraint="uq_apparel_stock_branch_variant"))
    rows = (await db.scalars(
        select(ApparelStock).where(ApparelStock.branch_id == branch_id, ApparelStock.variant_id.in_(ids))
        .order_by(ApparelStock.variant_id).with_for_update().execution_options(populate_existing=True)
    )).all()
    return {r.variant_id: r for r in rows}


def move(
    db: AsyncSession, level: ApparelStock, kind: ApparelMovementKind, delta: int, *, user_id: Optional[uuid.UUID],
    reason: Optional[str] = None, unit_cost: Any = None, purchase_id: Optional[uuid.UUID] = None, **refs: Any,
) -> None:
    """Change a locked stock row and write its ledger row. Announces the variant the moment it becomes low."""
    if delta == 0:
        return
    was_low = is_low(level)
    level.quantity = level.quantity + delta
    db.add(ApparelStockMovement(
        branch_id=level.branch_id, variant_id=level.variant_id, kind=kind.value, quantity=delta, balance_after=level.quantity,
        unit_cost=unit_cost, reason=reason, purchase_id=purchase_id, created_by_id=user_id, **refs,
    ))
    if is_low(level) and not was_low:
        queue_event(db, level.branch_id, Topic.CATALOG, "apparel.stock.low", level.variant_id,
                    {"variant_id": str(level.variant_id), "quantity": level.quantity, "low_stock_level": level.low_stock_level})


def announce(db: AsyncSession, branch_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]) -> None:
    queue_event(db, branch_id, Topic.CATALOG, "apparel.stock.changed", None,
                {"variant_ids": sorted(str(v) for v in set(variant_ids))})


async def variants_of(db: AsyncSession, org_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, ApparelVariant]:
    """The organization's variants by id; an id from elsewhere (or nowhere) is a 404."""
    wanted = set(variant_ids)
    found = {v.id: v for v in (await db.scalars(
        select(ApparelVariant).where(ApparelVariant.id.in_(wanted), ApparelVariant.organization_id == org_id))).all()}
    missing = wanted - set(found)
    if missing:
        raise NotFound("Variant not found", code="variant_not_found", details={"variant_ids": sorted(map(str, missing))})
    return found


# --- reading stock -----------------------------------------------------------------------------------------------------------


def _row(level: Optional[ApparelStock], variant: ApparelVariant, product: ApparelProduct, category: str,
         with_cost: bool) -> Dict[str, Any]:
    quantity = level.quantity if level else 0
    cost = level.cost_price if (level and with_cost) else None
    return {
        "variant_id": variant.id, "product_id": product.id, "product_name": product.name, "category_id": product.category_id,
        "category_name": category, "size": variant.size, "colour": variant.colour, "sku": variant.sku,
        "barcode": variant.barcode, "price": variant.price, "quantity": quantity,
        "low_stock_level": level.low_stock_level if level else None, "is_low": bool(level and is_low(level)),
        "is_out": quantity <= 0, "cost_price": cost,
        "stock_value": q(max(quantity, 0) * cost) if cost is not None else None,
    }


async def list_stock(
    db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID], *, search: Optional[str], low_only: bool,
    category_id: Optional[uuid.UUID], product_id: Optional[uuid.UUID] = None,
) -> List[Dict[str, Any]]:
    """Every active variant with its stock in the branch (a variant never stocked reads as 0). Cost goes to managers only."""
    branch_id = resolve_branch_id(user, branch_id)
    stmt = (
        select(ApparelVariant, ApparelProduct, ApparelCategory.name, ApparelStock)
        .join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .join(ApparelCategory, ApparelCategory.id == ApparelProduct.category_id)
        .outerjoin(ApparelStock, (ApparelStock.variant_id == ApparelVariant.id) & (ApparelStock.branch_id == branch_id))
        .where(ApparelVariant.organization_id == user.organization_id, ApparelVariant.is_active.is_(True),
               ApparelProduct.is_active.is_(True))
        .order_by(ApparelProduct.name, ApparelProduct.id, ApparelVariant.sort_order, ApparelVariant.created_at)
    )
    if category_id is not None:
        stmt = stmt.where(ApparelProduct.category_id == category_id)
    if product_id is not None:
        stmt = stmt.where(ApparelProduct.id == product_id)
    if search and search.strip():
        term = search.strip()
        like = f"%{term.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(ApparelProduct.name.ilike(like), ApparelProduct.style_code.ilike(like),
                              ApparelVariant.barcode == term, ApparelVariant.sku == term))
    if low_only:
        stmt = stmt.where(or_(ApparelStock.id.is_(None), ApparelStock.quantity <= 0,
                              ApparelStock.quantity <= ApparelStock.low_stock_level))
    return [_row(level, v, p, c, user.is_manager) for v, p, c, level in (await db.execute(stmt)).all()]


async def _read_one(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID, variant_id: uuid.UUID) -> Dict[str, Any]:
    v, p, c = (await db.execute(
        select(ApparelVariant, ApparelProduct, ApparelCategory.name)
        .join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .join(ApparelCategory, ApparelCategory.id == ApparelProduct.category_id).where(ApparelVariant.id == variant_id)
    )).one()
    level = await db.scalar(select(ApparelStock).where(ApparelStock.branch_id == branch_id, ApparelStock.variant_id == variant_id)
                            .execution_options(populate_existing=True))
    return _row(level, v, p, c, user.is_manager)


async def update_settings(db: AsyncSession, user: CurrentUser, variant_id: uuid.UUID,
                          payload: ApparelStockSettings) -> Dict[str, Any]:
    """The low-stock level and cost price of a variant in a branch."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    await variants_of(db, user.organization_id, [variant_id])
    level = (await lock_levels(db, branch_id, [variant_id]))[variant_id]
    for field, value in payload.model_dump(exclude_unset=True, exclude={"branch_id"}).items():
        setattr(level, field, value)
    announce(db, branch_id, [variant_id])
    await commit(db)
    return await _read_one(db, user, branch_id, variant_id)


# --- purchases ---------------------------------------------------------------------------------------------------------------


async def receive_purchase(db: AsyncSession, user: CurrentUser, payload: ApparelPurchaseCreate) -> Dict[str, Any]:
    """A delivery: every line adds to stock, and a line's unit cost becomes the variant's cost price in the branch."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    branch = await get_or_404(db, Branch, branch_id)
    await variants_of(db, user.organization_id, [line.variant_id for line in payload.lines])
    purchase = ApparelPurchase(
        id=uuid.uuid4(), branch_id=branch_id, supplier_name=payload.supplier_name, invoice_number=payload.invoice_number,
        received_on=payload.received_on or business_date(branch.timezone), notes=payload.notes, created_by_id=user.staff_id,
        total_cost=q(sum((line.unit_cost * line.quantity for line in payload.lines if line.unit_cost is not None), ZERO)),
    )
    db.add(purchase)
    await db.flush()
    levels = await lock_levels(db, branch_id, [line.variant_id for line in payload.lines])
    for line in payload.lines:
        level = levels[line.variant_id]
        if line.unit_cost is not None:
            level.cost_price = line.unit_cost
        move(db, level, ApparelMovementKind.PURCHASE, line.quantity, user_id=user.staff_id, unit_cost=line.unit_cost,
             purchase_id=purchase.id)
    announce(db, branch_id, levels)
    await commit(db)
    return await get_purchase(db, user, purchase.id)


async def _purchase_rows(db: AsyncSession, purchases: Sequence[ApparelPurchase]) -> List[Dict[str, Any]]:
    if not purchases:
        return []
    lines: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for m, name, size, colour in (await db.execute(
        select(ApparelStockMovement, ApparelProduct.name, ApparelVariant.size, ApparelVariant.colour)
        .join(ApparelVariant, ApparelVariant.id == ApparelStockMovement.variant_id)
        .join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .where(ApparelStockMovement.purchase_id.in_([p.id for p in purchases]))
        .order_by(ApparelStockMovement.created_at, ApparelProduct.name, ApparelVariant.sort_order)
    )).all():
        lines.setdefault(m.purchase_id or uuid.UUID(int=0), []).append({
            "variant_id": m.variant_id, "product_name": name, "size": size, "colour": colour, "quantity": m.quantity,
            "unit_cost": m.unit_cost, "line_cost": q(m.quantity * m.unit_cost) if m.unit_cost is not None else None,
        })
    names = await _staff_names(db, [p.created_by_id for p in purchases])
    return [{
        "id": p.id, "branch_id": p.branch_id, "supplier_name": p.supplier_name, "invoice_number": p.invoice_number,
        "received_on": p.received_on, "notes": p.notes, "total_cost": p.total_cost, "created_by": names.get(p.created_by_id) if p.created_by_id else None,
        "created_at": p.created_at, "lines": lines.get(p.id, []),
    } for p in purchases]


async def _staff_names(db: AsyncSession, ids: Iterable[Optional[uuid.UUID]]) -> Dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return dict((await db.execute(select(StaffUser.id, StaffUser.full_name).where(StaffUser.id.in_(wanted)))).tuples().all())


async def get_purchase(db: AsyncSession, user: CurrentUser, purchase_id: uuid.UUID) -> Dict[str, Any]:
    purchase = await get_or_404(db, ApparelPurchase, purchase_id)
    require_branch_access(user, purchase.branch_id)
    return (await _purchase_rows(db, [purchase]))[0]


async def list_purchases(db: AsyncSession, user: CurrentUser, params: PageParams,
                         branch_id: Optional[uuid.UUID]) -> Tuple[List[Dict[str, Any]], int]:
    branch_id = resolve_branch_id(user, branch_id)
    where = ApparelPurchase.branch_id == branch_id
    total = await db.scalar(select(func.count()).select_from(ApparelPurchase).where(where))
    purchases = (await db.scalars(
        select(ApparelPurchase).where(where).order_by(ApparelPurchase.received_on.desc(), ApparelPurchase.created_at.desc())
        .limit(params.limit).offset(params.offset))).all()
    return await _purchase_rows(db, purchases), int(total or 0)


# --- adjustments and counts --------------------------------------------------------------------------------------------------


async def adjust(db: AsyncSession, user: CurrentUser, payload: ApparelStockAdjust) -> Dict[str, Any]:
    """`delta` adds or removes pieces (damage, a found carton); `counted` replaces the quantity with a physical count."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    await variants_of(db, user.organization_id, [payload.variant_id])
    level = (await lock_levels(db, branch_id, [payload.variant_id]))[payload.variant_id]
    if payload.counted is not None:
        kind, delta = ApparelMovementKind.COUNT, payload.counted - level.quantity
    else:
        assert payload.delta is not None
        kind, delta = ApparelMovementKind.ADJUSTMENT, payload.delta
    move(db, level, kind, delta, user_id=user.staff_id, reason=payload.reason)  # a count that matches writes nothing
    announce(db, branch_id, [payload.variant_id])
    await commit(db)
    return await _read_one(db, user, branch_id, payload.variant_id)


# --- the ledger --------------------------------------------------------------------------------------------------------------


async def list_movements(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID],
    variant_id: Optional[uuid.UUID], kind: Optional[ApparelMovementKind],
) -> Tuple[List[Dict[str, Any]], int]:
    branch_id = resolve_branch_id(user, branch_id)
    where = [ApparelStockMovement.branch_id == branch_id]
    if variant_id is not None:
        where.append(ApparelStockMovement.variant_id == variant_id)
    if kind is not None:
        where.append(ApparelStockMovement.kind == kind.value)
    total = await db.scalar(select(func.count()).select_from(ApparelStockMovement).where(*where))
    rows = (await db.execute(
        select(ApparelStockMovement, ApparelProduct.name, ApparelVariant.size, ApparelVariant.colour, StaffUser.full_name)
        .join(ApparelVariant, ApparelVariant.id == ApparelStockMovement.variant_id)
        .join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .outerjoin(StaffUser, StaffUser.id == ApparelStockMovement.created_by_id)
        .where(*where).order_by(ApparelStockMovement.created_at.desc(), ApparelStockMovement.id)
        .limit(params.limit).offset(params.offset)
    )).all()
    return [{
        "id": m.id, "variant_id": m.variant_id, "product_name": name, "size": size, "colour": colour, "kind": m.kind,
        "quantity": m.quantity, "balance_after": m.balance_after, "unit_cost": m.unit_cost if user.is_manager else None,
        "reason": m.reason, "purchase_id": m.purchase_id, "created_by": staff, "created_at": m.created_at,
    } for m, name, size, colour, staff in rows], int(total or 0)
