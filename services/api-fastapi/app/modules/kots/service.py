import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver, resolve_branch_id
from app.core.enums import (
    KOT_TO_ORDER_ITEM_STATUS,
    KOT_TRANSITIONS,
    AuditAction,
    KOTStatus,
    OrderItemStatus,
    OrderStatus,
    Station,
    Topic,
)
from app.core.errors import Conflict, InvalidTransition
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO
from app.core.numbering import next_document_number
from app.models.floor import Branch
from app.models.kots import KOT, KOTItem
from app.models.orders import Order, OrderItem
from app.modules.orders.service import load_order, recompute_totals
from app.schemas.kots import KOTFireRequest, VoidKOTRequest
from app.worker.tasks import send_manager_push_alert

KOT_LOADERS = (selectinload(KOT.items),)
OPEN_KOT_STATUSES = [KOTStatus.QUEUED.value, KOTStatus.PREPARING.value, KOTStatus.READY.value]


async def load_kot(db: AsyncSession, kot_id: uuid.UUID, *, for_update: bool = False) -> KOT:
    return await get_or_404(db, KOT, kot_id, *KOT_LOADERS, for_update=for_update, populate_existing=True)


async def list_kots(
    db: AsyncSession,
    user: CurrentUser,
    branch_id: Optional[uuid.UUID],
    station: Optional[Station],
    status: Optional[KOTStatus],
    order_id: Optional[uuid.UUID],
) -> Sequence[KOT]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = select(KOT).where(KOT.branch_id == branch_id).options(*KOT_LOADERS).order_by(KOT.created_at)
    if station is not None:
        stmt = stmt.where(KOT.station == station.value)
    if status is not None:
        stmt = stmt.where(KOT.status == status.value)
    elif order_id is None:
        stmt = stmt.where(KOT.status.in_(OPEN_KOT_STATUSES))
    if order_id is not None:
        stmt = stmt.where(KOT.order_id == order_id)
    return (await db.scalars(stmt)).all()


def _kot_line_name(item: OrderItem) -> str:
    return f"{item.name_snapshot} ({item.variant_name_snapshot})" if item.variant_name_snapshot else item.name_snapshot


def _kot_line_notes(item: OrderItem) -> Optional[str]:
    parts = [m.name_snapshot for m in item.modifiers]
    if item.notes:
        parts.append(item.notes)
    return "; ".join(parts) or None


async def fire_kots(db: AsyncSession, user: CurrentUser, payload: KOTFireRequest) -> List[KOT]:
    """Send pending order items to their prep stations, one KOT per station."""
    order = await get_or_404(db, Order, payload.order_id, for_update=True, populate_existing=True)
    require_branch_access(user, order.branch_id)
    if order.status != OrderStatus.ACTIVE.value:
        raise InvalidTransition(f"Order is {order.status}", details={"status": order.status})

    # Lock pending lines so concurrent fires serialize; the loser sees nothing left to fire.
    stmt = (
        select(OrderItem)
        .where(OrderItem.order_id == order.id, OrderItem.status == OrderItemStatus.ORDERED.value)
        .options(selectinload(OrderItem.modifiers))
        .order_by(OrderItem.created_at)
        .with_for_update(of=OrderItem)
        .execution_options(populate_existing=True)
    )
    if payload.order_item_ids:
        stmt = stmt.where(OrderItem.id.in_(payload.order_item_ids))
    pending = (await db.scalars(stmt)).all()
    if not pending:
        raise Conflict("No pending items to fire", code="nothing_to_fire")

    branch = await get_or_404(db, Branch, order.branch_id)
    by_station: Dict[str, List[OrderItem]] = defaultdict(list)
    for item in pending:
        by_station[item.station_snapshot].append(item)

    kots: List[KOT] = []
    for station in sorted(by_station):
        kot = KOT(
            branch_id=order.branch_id,
            order_id=order.id,
            kot_number=await next_document_number(db, branch, "kot"),
            station=station,
            status=KOTStatus.QUEUED.value,
            fired_by_id=user.id,
        )
        kot.items = [
            KOTItem(
                order_item_id=item.id,
                item_name=_kot_line_name(item),
                quantity=item.quantity,
                notes=_kot_line_notes(item),
                status=KOTStatus.QUEUED.value,
            )
            for item in by_station[station]
        ]
        db.add(kot)
        kots.append(kot)
        for item in by_station[station]:
            item.status = OrderItemStatus.FIRED.value

    await db.flush()
    for kot in kots:
        queue_event(db, order.branch_id, Topic.KOTS, "kot.fired", kot.id,
                    {"kot_number": kot.kot_number, "station": kot.station, "order_id": str(order.id)})
    queue_event(db, order.branch_id, Topic.ORDERS, "order.items_fired", order.id)
    await commit(db)
    return [await load_kot(db, kot.id) for kot in kots]


async def _sync_order_items(db: AsyncSession, kot: KOT, status: OrderItemStatus) -> None:
    ids = [i.order_item_id for i in kot.items if i.status != KOTStatus.VOIDED.value]
    if not ids:
        return
    lines = (await db.scalars(
        select(OrderItem).where(OrderItem.id.in_(ids)).execution_options(populate_existing=True)
    )).all()
    for line in lines:
        if line.status != OrderItemStatus.CANCELLED.value:
            line.status = status.value


async def update_kot_status(db: AsyncSession, user: CurrentUser, kot_id: uuid.UUID, new_status: KOTStatus) -> KOT:
    kot = await load_kot(db, kot_id, for_update=True)
    require_branch_access(user, kot.branch_id)
    current = KOTStatus(kot.status)
    if new_status not in KOT_TRANSITIONS[current]:
        raise InvalidTransition(f"Cannot move KOT from {current} to {new_status}",
                                details={"from": current.value, "to": new_status.value})
    kot.status = new_status.value
    for item in kot.items:
        if item.status != KOTStatus.VOIDED.value:
            item.status = new_status.value
    await _sync_order_items(db, kot, KOT_TO_ORDER_ITEM_STATUS[new_status])
    queue_event(db, kot.branch_id, Topic.KOTS, "kot.status_changed", kot.id,
                {"status": kot.status, "station": kot.station, "order_id": str(kot.order_id)})
    await commit(db)
    return await load_kot(db, kot.id)


def ensure_voidable(kot: KOT) -> None:
    if KOTStatus.VOIDED not in KOT_TRANSITIONS[KOTStatus(kot.status)]:
        raise InvalidTransition(
            f"A {kot.status} KOT cannot be voided; comp it with a bill discount instead",
            details={"status": kot.status},
        )


def live_line_total(kot: KOT, order: Order) -> Decimal:
    """Value of the order lines on this ticket that are not already cancelled."""
    live_ids = {i.order_item_id for i in kot.items if i.status != KOTStatus.VOIDED.value}
    return sum(
        (line.line_total for line in order.items
         if line.id in live_ids and line.status != OrderItemStatus.CANCELLED.value),
        ZERO,
    )


async def apply_void(
    db: AsyncSession,
    kot: KOT,
    *,
    reason: str,
    requested_by_id: uuid.UUID,
    approver_id: uuid.UUID,
) -> Decimal:
    """
    Void a locked, voidable KOT: cancel its order lines, record the audit entry and queue events.
    Shared by on-the-spot voids and approved remote requests. Does not commit.
    """
    order = await load_order(db, kot.order_id, for_update=True)
    live_ids = {i.order_item_id for i in kot.items if i.status != KOTStatus.VOIDED.value}
    voided_amount = ZERO
    for line in order.items:
        if line.id in live_ids and line.status != OrderItemStatus.CANCELLED.value:
            line.status = OrderItemStatus.CANCELLED.value
            voided_amount += line.line_total
    recompute_totals(order)

    kot.status = KOTStatus.VOIDED.value
    kot.void_reason = reason
    kot.voided_by_id = requested_by_id
    kot.voided_approved_by_id = approver_id
    for item in kot.items:
        item.status = KOTStatus.VOIDED.value

    record_audit(
        db, branch_id=kot.branch_id, action=AuditAction.KOT_VOID, actor_id=requested_by_id,
        approver_id=approver_id, entity_type="kot", entity_id=kot.id, amount=voided_amount,
        details={"kot_number": kot.kot_number, "reason": reason,
                 "items": [{"name": i.item_name, "quantity": i.quantity} for i in kot.items]},
    )
    queue_event(db, kot.branch_id, Topic.KOTS, "kot.voided", kot.id, {"station": kot.station})
    queue_event(db, kot.branch_id, Topic.ORDERS, "order.updated", order.id)
    return voided_amount


async def void_kot(
    db: AsyncSession,
    user: CurrentUser,
    kot_id: uuid.UUID,
    payload: VoidKOTRequest,
    background: Optional[BackgroundTasks] = None,
    redis_client=None,
) -> KOT:
    kot = await load_kot(db, kot_id, for_update=True)
    require_branch_access(user, kot.branch_id)
    approver_id = await resolve_approver(db, user, kot.branch_id, payload.approver_pin, redis_client)
    ensure_voidable(kot)
    voided_amount = await apply_void(
        db, kot, reason=payload.void_reason, requested_by_id=user.id, approver_id=approver_id
    )
    await commit(db)

    if background is not None:
        background.add_task(
            send_manager_push_alert, kot.branch_id, "KOT voided",
            f"{kot.kot_number} voided by {user.full_name}: {payload.void_reason}",
            {"kot_id": str(kot.id), "amount": str(voided_amount)},
        )
    return await load_kot(db, kot.id)
