import uuid
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver, resolve_branch_id
from app.core.clock import business_date
from app.core.enums import (
    AuditAction,
    KOTStatus,
    OrderItemStatus,
    OrderStatus,
    OrderType,
    TableStatus,
    Topic,
)
from app.core.errors import Conflict, InvalidTransition, NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.numbering import next_document_number
from app.core.pagination import PageParams, paginate
from app.models.catalog import MenuItem, ModifierGroup
from app.models.floor import Branch, DiningTable, Floor
from app.models.kots import KOT, KOTItem
from app.models.orders import Order, OrderItem, OrderItemModifier
from app.modules.staff.service import get_open_shift
from app.schemas.orders import (
    OrderCancelRequest,
    OrderCreate,
    OrderItemCancelRequest,
    OrderItemCreate,
    OrderItemsAppend,
    OrderUpdate,
)

ORDER_LOADERS = (selectinload(Order.items).selectinload(OrderItem.modifiers),)


async def load_order(db: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
    return await get_or_404(db, Order, order_id, *ORDER_LOADERS, for_update=for_update, populate_existing=True)


async def get_order(db: AsyncSession, user: CurrentUser, order_id: uuid.UUID) -> Order:
    order = await load_order(db, order_id)
    require_branch_access(user, order.branch_id)
    return order


async def list_orders(
    db: AsyncSession,
    user: CurrentUser,
    params: PageParams,
    branch_id: Optional[uuid.UUID],
    status: Optional[OrderStatus],
    table_id: Optional[uuid.UUID],
    on_date: Optional[date],
) -> Tuple[List[Order], int]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = select(Order).where(Order.branch_id == branch_id).options(*ORDER_LOADERS).order_by(Order.created_at.desc())
    if status is not None:
        stmt = stmt.where(Order.status == status.value)
    if table_id is not None:
        stmt = stmt.where(Order.table_id == table_id)
    if on_date is not None:
        stmt = stmt.where(Order.business_date == on_date)
    return await paginate(db, stmt, params)


def _ensure_version(order: Order, expected: int) -> None:
    if order.version != expected:
        raise Conflict(
            "Order was modified by someone else; reload and retry",
            code="stale_version",
            details={"current_version": order.version},
        )


def _ensure_active(order: Order) -> None:
    if order.status != OrderStatus.ACTIVE.value:
        raise InvalidTransition(f"Order is {order.status}", details={"status": order.status})


def recompute_totals(order: Order) -> None:
    subtotal = sum(
        (i.line_total for i in order.items if i.status != OrderItemStatus.CANCELLED.value), ZERO
    )
    order.subtotal = q(subtotal)
    order.total_amount = order.subtotal


async def _build_order_items(db: AsyncSession, requested: List[OrderItemCreate]) -> List[OrderItem]:
    """Validate requested lines against the live catalog and snapshot names and prices."""
    item_ids = {r.item_id for r in requested}
    menu_items: Dict[uuid.UUID, MenuItem] = {
        m.id: m
        for m in (await db.scalars(
            select(MenuItem)
            .where(MenuItem.id.in_(item_ids))
            .options(
                selectinload(MenuItem.variants),
                selectinload(MenuItem.modifier_groups).selectinload(ModifierGroup.modifiers),
            )
        )).all()
    }

    lines: List[OrderItem] = []
    for req in requested:
        item = menu_items.get(req.item_id)
        if item is None or not item.is_active or not item.is_available_86:
            raise ValidationFailed("Item is unavailable", code="item_unavailable", details={"item_id": str(req.item_id)})

        active_variants = [v for v in item.variants if v.is_active]
        variant = None
        if req.variant_id is not None:
            variant = next((v for v in active_variants if v.id == req.variant_id), None)
            if variant is None or not variant.is_available_86:
                raise ValidationFailed("Variant is unavailable", code="variant_unavailable",
                                       details={"item_id": str(item.id), "variant_id": str(req.variant_id)})
        elif active_variants:
            raise ValidationFailed("A variant must be chosen for this item", code="variant_required",
                                   details={"item_id": str(item.id)})

        modifiers = []
        selected = set(req.modifier_ids)
        if len(selected) != len(req.modifier_ids):
            raise ValidationFailed("Duplicate modifiers", code="invalid_modifiers", details={"item_id": str(item.id)})
        matched: set = set()
        for group in item.modifier_groups:
            chosen = [m for m in group.modifiers if m.id in selected]
            if not (group.min_selection <= len(chosen) <= group.max_selection):
                raise ValidationFailed(
                    f"Select between {group.min_selection} and {group.max_selection} options for {group.name}",
                    code="invalid_modifiers",
                    details={"item_id": str(item.id), "modifier_group_id": str(group.id)},
                )
            for modifier in chosen:
                if not modifier.is_available:
                    raise ValidationFailed("Modifier is unavailable", code="modifier_unavailable",
                                           details={"modifier_id": str(modifier.id)})
                modifiers.append(modifier)
                matched.add(modifier.id)
        if matched != selected:
            raise ValidationFailed("Modifier does not apply to this item", code="invalid_modifiers",
                                   details={"modifier_ids": sorted(str(m) for m in selected - matched)})

        unit_price = variant.price if variant else item.base_price
        modifier_total = sum((m.price for m in modifiers), ZERO)
        line = OrderItem(
            item_id=item.id,
            variant_id=variant.id if variant else None,
            name_snapshot=item.name,
            variant_name_snapshot=variant.name if variant else None,
            station_snapshot=item.station,
            unit_price_snapshot=unit_price,
            tax_rate_snapshot=item.tax_rate,
            quantity=req.quantity,
            line_total=q((unit_price + modifier_total) * req.quantity),
            status=OrderItemStatus.ORDERED.value,
            notes=req.notes,
        )
        line.modifiers = [
            OrderItemModifier(modifier_item_id=m.id, name_snapshot=m.name, price_snapshot=m.price) for m in modifiers
        ]
        lines.append(line)
    return lines


async def _lock_table_for_seating(db: AsyncSession, table_id: uuid.UUID, branch_id: uuid.UUID) -> DiningTable:
    row = (await db.execute(
        select(DiningTable, Floor.branch_id)
        .join(Floor, DiningTable.floor_id == Floor.id)
        .where(DiningTable.id == table_id, DiningTable.is_active.is_(True))
        .with_for_update(of=DiningTable)
        .execution_options(populate_existing=True)
    )).first()
    if row is None or row[1] != branch_id:
        raise NotFound("Table not found")
    table = row[0]
    if table.merged_into_table_id is not None:
        raise Conflict("Table is merged into another table; seat the order there", code="table_merged",
                       details={"primary_table_id": str(table.merged_into_table_id)})
    if table.status not in (TableStatus.VACANT.value, TableStatus.RESERVED.value):
        raise Conflict("Table is not available", code="table_not_vacant", details={"status": table.status})
    return table


async def create_order(db: AsyncSession, user: CurrentUser, payload: OrderCreate) -> Order:
    branch_id = resolve_branch_id(user, payload.branch_id)
    branch = await get_or_404(db, Branch, branch_id)

    table = None
    if payload.order_type == OrderType.DINE_IN and payload.table_id is not None:
        table = await _lock_table_for_seating(db, payload.table_id, branch.id)
        table.status = TableStatus.OCCUPIED.value

    shift = await get_open_shift(db, user.id)
    order = Order(
        branch_id=branch.id,
        table_id=table.id if table else None,
        waiter_id=user.id,
        opened_by_shift_id=shift.id if shift else None,
        order_number=await next_document_number(db, branch, "order"),
        order_type=payload.order_type.value,
        status=OrderStatus.ACTIVE.value,
        guest_count=payload.guest_count,
        notes=payload.notes,
        business_date=business_date(branch.timezone),
    )
    order.items = await _build_order_items(db, payload.items)
    recompute_totals(order)
    db.add(order)
    await db.flush()

    queue_event(db, branch.id, Topic.ORDERS, "order.created", order.id, {"order_number": order.order_number})
    if table is not None:
        queue_event(db, branch.id, Topic.TABLES, "table.status_changed", table.id, {"status": table.status})
    await commit(db)
    return await load_order(db, order.id)


async def _load_for_change(db: AsyncSession, user: CurrentUser, order_id: uuid.UUID, version: int) -> Order:
    order = await load_order(db, order_id, for_update=True)
    require_branch_access(user, order.branch_id)
    _ensure_version(order, version)
    return order


async def update_order(db: AsyncSession, user: CurrentUser, order_id: uuid.UUID, payload: OrderUpdate) -> Order:
    order = await _load_for_change(db, user, order_id, payload.version)
    _ensure_active(order)
    for field, value in payload.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(order, field, value)
    queue_event(db, order.branch_id, Topic.ORDERS, "order.updated", order.id)
    await commit(db)
    return await load_order(db, order.id)


async def add_order_items(db: AsyncSession, user: CurrentUser, order_id: uuid.UUID, payload: OrderItemsAppend) -> Order:
    order = await _load_for_change(db, user, order_id, payload.version)
    _ensure_active(order)
    order.items.extend(await _build_order_items(db, payload.items))
    recompute_totals(order)
    queue_event(db, order.branch_id, Topic.ORDERS, "order.items_added", order.id, {"count": len(payload.items)})
    await commit(db)
    return await load_order(db, order.id)


async def _void_kot_lines(db: AsyncSession, order_item_ids: Sequence[uuid.UUID]) -> List[KOT]:
    """Void KOT lines for the given order items; void whole KOTs whose lines are all voided."""
    if not order_item_ids:
        return []
    kot_items = (await db.scalars(
        select(KOTItem).where(KOTItem.order_item_id.in_(order_item_ids), KOTItem.status != KOTStatus.VOIDED.value)
    )).all()
    for kot_item in kot_items:
        kot_item.status = KOTStatus.VOIDED.value
    # Flush before reloading: populate_existing would otherwise overwrite the unflushed statuses.
    await db.flush()
    kot_ids = {k.kot_id for k in kot_items}
    affected = []
    if kot_ids:
        kots = (await db.scalars(
            select(KOT).where(KOT.id.in_(kot_ids)).options(selectinload(KOT.items))
            .execution_options(populate_existing=True)
        )).all()
        for kot in kots:
            if all(i.status == KOTStatus.VOIDED.value for i in kot.items) and kot.status != KOTStatus.VOIDED.value:
                kot.status = KOTStatus.VOIDED.value
            affected.append(kot)
    return affected


async def cancel_order_item(
    db: AsyncSession, user: CurrentUser, order_id: uuid.UUID, item_id: uuid.UUID,
    payload: OrderItemCancelRequest, redis_client=None,
) -> Order:
    order = await load_order(db, order_id, for_update=True)
    require_branch_access(user, order.branch_id)
    _ensure_active(order)
    line = next((i for i in order.items if i.id == item_id), None)
    if line is None:
        raise NotFound("Order item not found")
    if line.status == OrderItemStatus.CANCELLED.value:
        raise InvalidTransition("Item is already cancelled")
    if line.status == OrderItemStatus.SERVED.value:
        raise InvalidTransition("Served items cannot be cancelled; apply a bill discount instead")

    approver_id = None
    if line.status != OrderItemStatus.ORDERED.value:
        # Already in the kitchen: needs manager approval and voids the KOT line.
        approver_id = await resolve_approver(db, user, order.branch_id, payload.approver_pin, redis_client)
        for kot in await _void_kot_lines(db, [line.id]):
            queue_event(db, order.branch_id, Topic.KOTS, "kot.updated", kot.id, {"status": kot.status})

    line.status = OrderItemStatus.CANCELLED.value
    recompute_totals(order)
    record_audit(
        db, branch_id=order.branch_id, action=AuditAction.ORDER_ITEM_CANCEL, actor_id=user.id,
        approver_id=approver_id, entity_type="order_item", entity_id=line.id, amount=line.line_total,
        details={"order_number": order.order_number, "item": line.name_snapshot, "reason": payload.reason},
    )
    queue_event(db, order.branch_id, Topic.ORDERS, "order.updated", order.id)
    await commit(db)
    return await load_order(db, order.id)


async def set_table_vacant(db: AsyncSession, table_id: Optional[uuid.UUID]) -> Optional[DiningTable]:
    """Free a table and any tables merged into it."""
    if table_id is None:
        return None
    table = await get_or_404(db, DiningTable, table_id, for_update=True)
    table.status = TableStatus.VACANT.value
    merged = (await db.scalars(
        select(DiningTable).where(DiningTable.merged_into_table_id == table.id).with_for_update()
    )).all()
    for other in merged:
        other.merged_into_table_id = None
        other.status = TableStatus.VACANT.value
    return table


async def set_table_status(db: AsyncSession, table_id: Optional[uuid.UUID], status: TableStatus) -> None:
    if table_id is None:
        return
    table = await get_or_404(db, DiningTable, table_id, for_update=True)
    table.status = status.value


async def cancel_order(
    db: AsyncSession, user: CurrentUser, order_id: uuid.UUID, payload: OrderCancelRequest, redis_client=None,
) -> Order:
    order = await _load_for_change(db, user, order_id, payload.version)
    if order.status != OrderStatus.ACTIVE.value:
        raise InvalidTransition("Only active orders can be cancelled; void the bill first",
                                details={"status": order.status})
    approver_id = await resolve_approver(db, user, order.branch_id, payload.approver_pin, redis_client)

    live_ids = [i.id for i in order.items if i.status != OrderItemStatus.CANCELLED.value]
    for kot in await _void_kot_lines(db, live_ids):
        queue_event(db, order.branch_id, Topic.KOTS, "kot.voided", kot.id, {"status": kot.status})
    amount = order.subtotal
    for line in order.items:
        line.status = OrderItemStatus.CANCELLED.value
    order.status = OrderStatus.CANCELLED.value
    order.cancel_reason = payload.reason
    recompute_totals(order)

    table = await set_table_vacant(db, order.table_id)
    record_audit(
        db, branch_id=order.branch_id, action=AuditAction.ORDER_CANCEL, actor_id=user.id,
        approver_id=approver_id, entity_type="order", entity_id=order.id, amount=amount,
        details={"order_number": order.order_number, "reason": payload.reason},
    )
    queue_event(db, order.branch_id, Topic.ORDERS, "order.cancelled", order.id)
    if table is not None:
        queue_event(db, order.branch_id, Topic.TABLES, "table.status_changed", table.id, {"status": table.status})
    await commit(db)
    return await load_order(db, order.id)


def sum_lines(items: Sequence[OrderItem]) -> Decimal:
    return sum((i.line_total for i in items), ZERO)
