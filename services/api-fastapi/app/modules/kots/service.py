import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver, resolve_branch_id
from app.core.enums import (
    KOT_TO_ORDER_ITEM_STATUS,
    KOT_TRANSITIONS,
    SLIP_KOT_TRANSITIONS,
    AuditAction,
    Fulfilment,
    KOTStatus,
    OrderItemStatus,
    OrderStatus,
    Role,
    Station,
    Topic,
)
from app.core.errors import Conflict, Forbidden, InvalidTransition
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO
from app.core.numbering import next_document_number
from app.models.floor import Branch, DiningTable
from app.models.kots import KOT, KOTItem
from app.models.orders import Order, OrderItem
from app.models.staff import StaffUser
from app.modules.kots import slip as slips
from app.modules.orders.service import load_order, recompute_totals
from app.schemas.kots import KOTFireRequest, VoidKOTRequest
from app.worker.tasks import send_manager_push_alert

KOT_LOADERS = (selectinload(KOT.items),)
# Only these roles run the kitchen's stages; floor staff may only mark a ready ticket served.
KITCHEN_STAGE_ROLES = {Role.OWNER, Role.MANAGER, Role.KITCHEN}


async def annotate_kots(db: AsyncSession, kots: Sequence[KOT]) -> Sequence[KOT]:
    """Attach order and table numbers (not columns on KOT) so listings can show them."""
    order_ids = {k.order_id for k in kots}
    if not order_ids:
        return kots
    rows = await db.execute(
        select(Order.id, Order.order_number, DiningTable.table_number)
        .outerjoin(DiningTable, DiningTable.id == Order.table_id)
        .where(Order.id.in_(order_ids))
    )
    labels = {order_id: (number, table) for order_id, number, table in rows}
    for kot in kots:
        kot.order_number, kot.table_number = labels.get(kot.order_id, (None, None))
    return kots
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
    *,
    screen_only: bool = False,
) -> Sequence[KOT]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = select(KOT).where(KOT.branch_id == branch_id).options(*KOT_LOADERS).order_by(KOT.created_at)
    if screen_only:  # the kitchen screen never shows tickets that went out on paper
        stmt = stmt.where(KOT.fulfilment == Fulfilment.SCREEN.value)
    if station is not None:
        stmt = stmt.where(KOT.station == station.value)
    if status is not None:
        stmt = stmt.where(KOT.status == status.value)
    elif order_id is None:
        stmt = stmt.where(KOT.status.in_(OPEN_KOT_STATUSES))
    if order_id is not None:
        stmt = stmt.where(KOT.order_id == order_id)
    return await annotate_kots(db, (await db.scalars(stmt)).all())


def _kot_line_name(item: OrderItem) -> str:
    return f"{item.name_snapshot} ({item.variant_name_snapshot})" if item.variant_name_snapshot else item.name_snapshot


def _kot_line_notes(item: OrderItem) -> Optional[str]:
    parts = [m.name_snapshot for m in item.modifiers]
    if item.notes:
        parts.append(item.notes)
    return "; ".join(parts) or None


async def fire_kots(db: AsyncSession, user: CurrentUser, payload: KOTFireRequest, *, kds: bool = True) -> List[KOT]:
    """
    Send pending order items to the kitchen. With the kitchen display module there is one KOT per prep station, each on
    that station's screen. Without it the send becomes a single slip for the whole table (`fulfilment`), so one piece of
    paper goes to the kitchen; its lines are grouped by station on the paper.
    """
    fulfilment = Fulfilment.SCREEN if kds else Fulfilment.SLIP
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

    groups = by_station if kds else {Station.KITCHEN.value: list(pending)}

    kots: List[KOT] = []
    for station in sorted(groups):
        kot = KOT(
            branch_id=order.branch_id,
            order_id=order.id,
            kot_number=await next_document_number(db, branch, "kot"),
            station=station,
            status=KOTStatus.QUEUED.value,
            fulfilment=fulfilment.value,
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
            for item in groups[station]
        ]
        db.add(kot)
        kots.append(kot)
        for item in groups[station]:
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


async def update_kot_status(
    db: AsyncSession, user: CurrentUser, kot_id: uuid.UUID, new_status: KOTStatus, *, kds: bool = True
) -> KOT:
    kot = await load_kot(db, kot_id, for_update=True)
    require_branch_access(user, kot.branch_id)
    if user.role not in KITCHEN_STAGE_ROLES and new_status != KOTStatus.SERVED:
        raise Forbidden("Only the kitchen can start or finish a ticket; floor staff can mark it served")
    if new_status in (KOTStatus.PREPARING, KOTStatus.READY) and not kds:
        raise Forbidden("The kitchen display is not part of your plan", code="module_disabled", details={"module": "kds"})
    current = KOTStatus(kot.status)
    if kot.fulfilment == Fulfilment.SLIP.value:
        if new_status in (KOTStatus.PREPARING, KOTStatus.READY):
            raise InvalidTransition("A printed slip has no kitchen stages; mark it served", code="slip_ticket",
                                    details={"fulfilment": kot.fulfilment})
        transitions = SLIP_KOT_TRANSITIONS
    elif not kds:
        transitions = SLIP_KOT_TRANSITIONS  # a screen ticket left over after the module was removed: floor staff can still close it
    else:
        transitions = KOT_TRANSITIONS
    if new_status not in transitions[current]:
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
    return (await annotate_kots(db, [await load_kot(db, kot.id)]))[0]


# A ready ticket can still be cancelled: whether the cooked food is wasted or goes to another
# table is the kitchen's call. Once it is served the guest has it, so it can only be comped.
VOIDABLE_STATUSES = {KOTStatus.QUEUED, KOTStatus.PREPARING, KOTStatus.READY}


def ensure_voidable(kot: KOT) -> None:
    if KOTStatus(kot.status) not in VOIDABLE_STATUSES:
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
    stage_when_voided = kot.status
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
        details={"kot_number": kot.kot_number, "reason": reason, "stage": stage_when_voided,
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


# --- kitchen slips ---------------------------------------------------------------------------------------------------------------


async def build_slip(db: AsyncSession, user: CurrentUser, kot_id: uuid.UUID) -> Tuple[slips.Slip, KOT]:
    """Assemble the slip for a ticket. Reading it changes nothing: printing is recorded separately (`record_print`)."""
    kot = await load_kot(db, kot_id)
    require_branch_access(user, kot.branch_id)
    order = await get_or_404(db, Order, kot.order_id)
    branch = await get_or_404(db, Branch, kot.branch_id)
    table = await db.get(DiningTable, order.table_id) if order.table_id else None
    fired_by = await db.get(StaffUser, kot.fired_by_id) if kot.fired_by_id else None
    # Which round of this station's tickets is this? Earlier tickets count even if they were cancelled: the kitchen saw them.
    earlier = await db.scalar(
        select(func.count()).select_from(KOT).where(
            KOT.order_id == kot.order_id, KOT.station == kot.station,
            (KOT.created_at < kot.created_at) | ((KOT.created_at == kot.created_at) & (KOT.kot_number < kot.kot_number)))
    ) or 0
    voided = kot.status == KOTStatus.VOIDED.value
    if voided:
        marker = slips.VOID
    elif kot.print_count > 0:
        marker = slips.REPRINT
    elif earlier > 0:
        marker = slips.ADDITIONAL
    else:
        marker = slips.NEW
    fired_at = (kot.created_at or datetime.now(timezone.utc)).astimezone(ZoneInfo(branch.timezone))
    # A slip-plan ticket carries the whole send, so each line keeps its prep station for grouping on the paper.
    stations = dict((await db.execute(
        select(OrderItem.id, OrderItem.station_snapshot).where(OrderItem.id.in_([i.order_item_id for i in kot.items]))
    )).all())
    lines = [
        slips.SlipLine(quantity=i.quantity, name=i.item_name, notes=i.notes, cancelled=i.status == KOTStatus.VOIDED.value and not voided,
                       station=stations.get(i.order_item_id))
        for i in kot.items
    ]
    slip = slips.Slip(
        marker=marker, branch_name=branch.name, branch_code=branch.code, kot_number=kot.kot_number, order_number=order.order_number,
        station=kot.station, order_type=order.order_type, table_number=table.table_number if table else None,
        guest_count=order.guest_count, fired_at=fired_at, fired_by=fired_by.full_name if fired_by else None,
        round=earlier + 1, print_count=kot.print_count, lines=lines, order_notes=order.notes,
    )
    return slip, kot


async def record_print(db: AsyncSession, user: CurrentUser, kot_id: uuid.UUID) -> KOT:
    """
    The app confirms that a slip came out of the printer. The first confirmation is the original print; every later one is a
    reprint, which is counted, attributed to the person and written to the audit log, so duplicate slips cannot be produced
    unnoticed.
    """
    kot = await load_kot(db, kot_id, for_update=True)
    require_branch_access(user, kot.branch_id)
    now = datetime.now(timezone.utc)
    if kot.status == KOTStatus.VOIDED.value and kot.print_count > 0:
        # A cancelled ticket whose slip already went out: this print is the VOID notice for the kitchen, not a reprint of the order.
        # The first one is expected; another copy is a reprint and audited like any other.
        if kot.void_printed_at is not None:
            record_audit(db, branch_id=kot.branch_id, action=AuditAction.KOT_REPRINT, actor_id=user.staff_id, entity_type="kot",
                         entity_id=kot.id, details={"kot_number": kot.kot_number, "status": kot.status, "void_notice": True})
        kot.void_printed_at = kot.void_printed_at or now
    else:
        kot.print_count += 1
        kot.first_printed_at = kot.first_printed_at or now
        if kot.print_count > 1:
            record_audit(db, branch_id=kot.branch_id, action=AuditAction.KOT_REPRINT, actor_id=user.staff_id, entity_type="kot",
                         entity_id=kot.id, details={"kot_number": kot.kot_number, "print_count": kot.print_count, "status": kot.status})
    kot.last_printed_at, kot.last_printed_by_id = now, user.staff_id
    queue_event(db, kot.branch_id, Topic.KOTS, "kot.printed", kot.id,
                {"print_count": kot.print_count, "station": kot.station, "order_id": str(kot.order_id)})
    await commit(db)
    return (await annotate_kots(db, [await load_kot(db, kot.id)]))[0]


async def print_queue(db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID]) -> Sequence[KOT]:
    """
    What the branch's print station (the counter device) still has to print, oldest first: paper tickets that have never printed,
    and VOID notices for cancelled tickets whose slip already went to the kitchen.
    """
    branch_id = resolve_branch_id(user, branch_id)
    never_printed = (KOT.status == KOTStatus.QUEUED.value) & (KOT.print_count == 0)
    void_owed = (KOT.status == KOTStatus.VOIDED.value) & (KOT.print_count > 0) & KOT.void_printed_at.is_(None)
    stmt = (
        select(KOT).where(KOT.branch_id == branch_id, KOT.fulfilment == Fulfilment.SLIP.value, never_printed | void_owed)
        .options(*KOT_LOADERS).order_by(KOT.created_at)
    )
    return await annotate_kots(db, (await db.scalars(stmt)).all())
