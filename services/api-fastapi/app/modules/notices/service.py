"""
Notices for floor staff: what happened since they last looked that they should act on. The phone asks every few seconds with the
`now` of its previous answer. Waiters hear about their own orders; captains, managers and owners about the whole branch.

Every notice has a stable id, so a notice seen twice (overlapping windows) is shown once.
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import CurrentUser
from app.core.clock import utcnow
from app.core.enums import BillStatus, Fulfilment, KOTStatus, Role, VoidRequestStatus
from app.core.money import q
from app.models.billing import Bill
from app.models.catalog import MenuItem
from app.models.floor import DiningTable
from app.models.kots import KOT, VoidRequest
from app.models.orders import Order
from app.models.staff import StaffUser
from app.modules.kots.slip import qty_text
from app.schemas.notices import Notice, NoticesRead

# A phone that was away longer than this only hears about the last few minutes, not a backlog.
MAX_LOOKBACK = timedelta(minutes=10)
OVERLAP = timedelta(seconds=30)


def _where(order: Order, table: Optional[str]) -> str:
    return f"Table {table}" if table else f"Takeaway #{order.order_number.split('-')[-1]}"


def _items(kot: KOT) -> str:
    return ", ".join(f"{qty_text(i.quantity)} × {i.item_name}" for i in kot.items if i.status != KOTStatus.VOIDED.value)


async def notices(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID, since: Optional[datetime]) -> NoticesRead:
    now = utcnow()
    if since is None:  # the first ask after signing in only sets the clock: what already happened is on the screens
        return NoticesRead(now=now, notices=[])
    # Rows are stamped when their transaction starts, so one can appear a moment after its time: look back a little further
    # every time. The phone drops ids it already showed.
    since = max(since, now - MAX_LOOKBACK) - OVERLAP
    own_only = user.role == Role.WAITER
    mine = (Order.waiter_id == user.id) if own_only else True
    out: List[Notice] = []

    # Kitchen tickets: ready to pick up, cancelled by the kitchen, or printed at the counter.
    kot_rows = (await db.execute(
        select(KOT, Order, DiningTable.table_number)
        .join(Order, Order.id == KOT.order_id).outerjoin(DiningTable, DiningTable.id == Order.table_id)
        .where(KOT.branch_id == branch_id, KOT.updated_at > since, mine)
        .options(selectinload(KOT.items))
    )).all()
    for kot, order, table in kot_rows:
        where = _where(order, table)
        number = kot.kot_number.split("-")[-1]
        if kot.status == KOTStatus.READY.value and kot.fulfilment == Fulfilment.SCREEN.value:
            out.append(Notice(id=f"ready:{kot.id}", kind="food_ready", title=f"Ready · {where}", detail=_items(kot),
                              order_id=order.id, at=kot.updated_at))
        elif kot.status == KOTStatus.VOIDED.value and kot.voided_by_id != user.id:
            out.append(Notice(id=f"kot-void:{kot.id}", kind="kot_cancelled", title=f"KOT {number} cancelled · {where}",
                              detail=kot.void_reason or "Cancelled by the kitchen", order_id=order.id, at=kot.updated_at))
        if (kot.fulfilment == Fulfilment.SLIP.value and kot.first_printed_at and kot.first_printed_at > since
                and kot.status != KOTStatus.VOIDED.value):
            out.append(Notice(id=f"printed:{kot.id}", kind="kot_printed", title=f"KOT {number} printed · {where}",
                              detail="Take it from the counter printer to the kitchen.", order_id=order.id, at=kot.first_printed_at))

    # Cancel requests that a manager or the kitchen answered.
    requester = VoidRequest.requested_by_id == user.id if own_only else True
    void_rows = (await db.execute(
        select(VoidRequest, Order, DiningTable.table_number, StaffUser.full_name)
        .join(Order, Order.id == VoidRequest.order_id).outerjoin(DiningTable, DiningTable.id == Order.table_id)
        .outerjoin(StaffUser, StaffUser.id == VoidRequest.reviewed_by_id)
        .where(VoidRequest.branch_id == branch_id, VoidRequest.reviewed_at > since, requester,
               VoidRequest.status.in_([VoidRequestStatus.APPROVED.value, VoidRequestStatus.REJECTED.value]))
    )).all()
    for request, order, table, reviewer in void_rows:
        approved = request.status == VoidRequestStatus.APPROVED.value
        detail = f"{'Approved' if approved else 'Declined'} by {reviewer or 'a manager'}"
        if request.review_note:
            detail += f": {request.review_note}"
        out.append(Notice(id=f"void:{request.id}", kind="cancel_approved" if approved else "cancel_declined",
                          title=f"Cancel {'approved' if approved else 'declined'} · {_where(order, table)}", detail=detail,
                          order_id=order.id, at=request.reviewed_at))

    # Bills paid: the guests are done and the table can be cleared.
    paid_rows = (await db.execute(
        select(Bill, Order, DiningTable.table_number)
        .join(Order, Order.id == Bill.order_id).outerjoin(DiningTable, DiningTable.id == Order.table_id)
        .where(Bill.branch_id == branch_id, Bill.status == BillStatus.PAID.value, Bill.settled_at > since, mine)
    )).all()
    for bill, order, table in paid_rows:
        out.append(Notice(id=f"paid:{bill.id}", kind="bill_paid", title=f"Paid · {_where(order, table)}",
                          detail=f"₹{q(bill.grand_total):,.2f}" + (" · the table can be cleared" if table else ""),
                          order_id=order.id, at=bill.settled_at))

    # Items that just ran out, so nobody promises them to a guest.
    sold_out = (await db.execute(
        select(MenuItem.id, MenuItem.name, MenuItem.updated_at).where(
            MenuItem.organization_id == user.organization_id, MenuItem.is_active.is_(True),
            MenuItem.is_available_86.is_(False), MenuItem.updated_at > since)
    )).all()
    for item_id, name, updated in sold_out:
        out.append(Notice(id=f"86:{item_id}:{int(updated.timestamp())}", kind="sold_out", title=f"{name} is sold out",
                          detail="Not available until it is switched back on.", at=updated))

    out.sort(key=lambda n: n.at)
    return NoticesRead(now=now, notices=out[-20:])
