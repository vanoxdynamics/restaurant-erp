"""
Remote void approvals.

Floor staff raise a request against a KOT; the kitchen (or a manager from a password session) approves
(the KOT is voided exactly as an on-the-spot void would be) or rejects it. One open request per KOT.
"""

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_branch_access
from app.core.clock import utcnow
from app.core.enums import KOTStatus, OrderStatus, Station, Topic, VoidRequestStatus
from app.core.errors import Conflict, Forbidden, InvalidTransition
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.pagination import PageParams, paginate
from app.models.floor import DiningTable
from app.models.kots import VoidRequest
from app.models.orders import Order
from app.models.staff import StaffUser
from app.modules.kots.service import apply_void, ensure_voidable, live_line_total, load_kot
from app.modules.orders.service import load_order
from app.schemas.approvals import VoidRequestItem, VoidRequestRead


async def _to_read(db: AsyncSession, request: VoidRequest) -> VoidRequestRead:
    kot = await load_kot(db, request.kot_id)
    order = await db.get(Order, request.order_id)
    table = await db.get(DiningTable, order.table_id) if order and order.table_id else None
    requester = await db.get(StaffUser, request.requested_by_id)
    reviewer = await db.get(StaffUser, request.reviewed_by_id) if request.reviewed_by_id else None
    return VoidRequestRead(
        id=request.id,
        branch_id=request.branch_id,
        kot_id=kot.id,
        kot_number=kot.kot_number,
        station=Station(kot.station),
        kot_status=KOTStatus(kot.status),
        order_id=request.order_id,
        order_number=order.order_number if order else "",
        table_number=table.table_number if table else None,
        items=[VoidRequestItem(name=i.item_name, quantity=i.quantity) for i in kot.items],
        amount=request.amount,
        reason=request.reason,
        status=VoidRequestStatus(request.status),
        requested_by_id=request.requested_by_id,
        requested_by_name=requester.full_name if requester else "",
        reviewed_by_name=reviewer.full_name if reviewer else None,
        review_note=request.review_note,
        created_at=request.created_at,
        reviewed_at=request.reviewed_at,
    )


async def create_void_request(
    db: AsyncSession,
    user: CurrentUser,
    kot_id: uuid.UUID,
    reason: str,
) -> VoidRequestRead:
    kot = await load_kot(db, kot_id, for_update=True)
    require_branch_access(user, kot.branch_id)
    ensure_voidable(kot)
    order = await load_order(db, kot.order_id)
    if order.status != OrderStatus.ACTIVE.value:
        raise InvalidTransition(f"Order is {order.status}", details={"status": order.status})

    request = VoidRequest(
        branch_id=kot.branch_id,
        kot_id=kot.id,
        order_id=order.id,
        reason=reason,
        amount=live_line_total(kot, order),
        status=VoidRequestStatus.PENDING.value,
        requested_by_id=user.id,
    )
    db.add(request)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("A void request for this ticket is already pending", code="void_request_pending") from exc

    queue_event(db, kot.branch_id, Topic.APPROVALS, "void_request.created", request.id,
                {"kot_number": kot.kot_number, "amount": str(request.amount)})
    await commit(db)

    return await _to_read(db, request)


async def list_void_requests(
    db: AsyncSession,
    branch_id: uuid.UUID,
    status: Optional[VoidRequestStatus],
    params: PageParams,
) -> Tuple[List[VoidRequestRead], int]:
    stmt = select(VoidRequest).where(VoidRequest.branch_id == branch_id)
    if status is not None:
        stmt = stmt.where(VoidRequest.status == status.value)
    # Pending: oldest first (first come, first served). History: newest first.
    order = VoidRequest.created_at.asc() if status == VoidRequestStatus.PENDING else VoidRequest.created_at.desc()
    rows, total = await paginate(db, stmt.order_by(order), params)
    return [await _to_read(db, r) for r in rows], total


async def list_order_void_requests(db: AsyncSession, user: CurrentUser, order_id: uuid.UUID) -> List[VoidRequestRead]:
    order = await load_order(db, order_id)
    require_branch_access(user, order.branch_id)
    rows = await db.scalars(
        select(VoidRequest).where(VoidRequest.order_id == order_id).order_by(VoidRequest.created_at.desc())
    )
    return [await _to_read(db, r) for r in rows]


async def _load_pending(db: AsyncSession, user: CurrentUser, request_id: uuid.UUID) -> VoidRequest:
    request = await get_or_404(db, VoidRequest, request_id, for_update=True)
    require_branch_access(user, request.branch_id)
    if request.requested_by_id == user.id:
        raise Forbidden("You can't review your own request", code="self_review")
    if request.status != VoidRequestStatus.PENDING.value:
        raise InvalidTransition(f"Request is already {request.status}", details={"status": request.status})
    return request


async def approve_void_request(
    db: AsyncSession, user: CurrentUser, request_id: uuid.UUID, note: Optional[str]
) -> VoidRequestRead:
    request = await _load_pending(db, user, request_id)
    kot = await load_kot(db, request.kot_id, for_update=True)
    ensure_voidable(kot)
    request.amount = await apply_void(
        db, kot, reason=request.reason, requested_by_id=request.requested_by_id, approver_id=user.id
    )
    request.status = VoidRequestStatus.APPROVED.value
    request.reviewed_by_id = user.id
    request.reviewed_at = utcnow()
    request.review_note = note
    queue_event(db, request.branch_id, Topic.APPROVALS, "void_request.approved", request.id)
    await commit(db)
    return await _to_read(db, request)


async def reject_void_request(
    db: AsyncSession, user: CurrentUser, request_id: uuid.UUID, note: Optional[str]
) -> VoidRequestRead:
    request = await _load_pending(db, user, request_id)
    request.status = VoidRequestStatus.REJECTED.value
    request.reviewed_by_id = user.id
    request.reviewed_at = utcnow()
    request.review_note = note
    queue_event(db, request.branch_id, Topic.APPROVALS, "void_request.rejected", request.id)
    await commit(db)
    return await _to_read(db, request)

