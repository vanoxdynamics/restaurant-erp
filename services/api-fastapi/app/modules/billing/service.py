import uuid
from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple
from urllib.parse import quote, urlencode

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver, resolve_branch_id
from app.core.clock import utcnow
from app.core.config import settings
from app.core.enums import (
    AuditAction,
    BillStatus,
    OrderItemStatus,
    OrderStatus,
    PaymentMode,
    PaymentStatus,
    TableStatus,
    Topic,
)
from app.core.errors import Conflict, InvalidTransition, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.numbering import next_document_number
from app.core.pagination import PageParams, paginate
from app.models.billing import Bill, Payment
from app.models.floor import Branch
from app.modules.billing.tax import TaxLine, compute_bill
from app.modules.orders.service import load_order, set_table_status, set_table_vacant
from app.modules.staff.service import get_open_shift
from app.schemas.billing import BillCreate, BillVoidRequest, PaymentCreate
from app.worker.tasks import deduct_bom_recipe_inventory, send_manager_push_alert

BILL_LOADERS = (selectinload(Bill.payments),)
PAYABLE = (BillStatus.UNPAID.value, BillStatus.PARTIALLY_PAID.value)


async def load_bill(db: AsyncSession, bill_id: uuid.UUID, *, for_update: bool = False) -> Bill:
    return await get_or_404(db, Bill, bill_id, *BILL_LOADERS, for_update=for_update, populate_existing=True)


async def get_bill(db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID) -> Bill:
    bill = await load_bill(db, bill_id)
    require_branch_access(user, bill.branch_id)
    return bill


async def list_bills(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID],
    status: Optional[BillStatus], on_date: Optional[date],
) -> Tuple[List[Bill], int]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = select(Bill).where(Bill.branch_id == branch_id).options(*BILL_LOADERS).order_by(Bill.created_at.desc())
    if status is not None:
        stmt = stmt.where(Bill.status == status.value)
    if on_date is not None:
        stmt = stmt.where(Bill.business_date == on_date)
    return await paginate(db, stmt, params)


async def generate_bill(
    db: AsyncSession, user: CurrentUser, payload: BillCreate,
    background: Optional[BackgroundTasks] = None, redis_client=None,
) -> Bill:
    order = await load_order(db, payload.order_id, for_update=True)
    require_branch_access(user, order.branch_id)
    if order.status != OrderStatus.ACTIVE.value:
        raise InvalidTransition(f"Order is {order.status}", details={"status": order.status})

    lines = [i for i in order.items if i.status != OrderItemStatus.CANCELLED.value]
    if not lines:
        raise Conflict("Order has no billable items", code="empty_order")
    unfired = [str(i.id) for i in lines if i.status == OrderItemStatus.ORDERED.value]
    if unfired:
        raise Conflict("Fire all items to the kitchen before billing", code="unfired_items",
                       details={"order_item_ids": unfired})

    breakdown = compute_bill([TaxLine(i.line_total, i.tax_rate_snapshot) for i in lines], payload.discount_amount)

    approver_id = None
    high_discount = False
    if breakdown.discount > ZERO:
        pct = breakdown.discount / breakdown.subtotal * 100
        if pct > settings.DISCOUNT_APPROVAL_THRESHOLD_PERCENT:
            high_discount = True
            approver_id = await resolve_approver(db, user, order.branch_id, payload.approver_pin, redis_client)

    branch = await get_or_404(db, Branch, order.branch_id)
    bill = Bill(
        branch_id=order.branch_id,
        order_id=order.id,
        bill_number=await next_document_number(db, branch, "bill"),
        business_date=order.business_date,
        subtotal=breakdown.subtotal,
        tax_cgst=breakdown.cgst,
        tax_sgst=breakdown.sgst,
        discount_amount=breakdown.discount,
        discount_reason=payload.discount_reason,
        discount_approved_by_id=approver_id,
        grand_total=breakdown.grand_total,
        paid_amount=ZERO,
        status=BillStatus.UNPAID.value,
    )
    bill.payments = []
    db.add(bill)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Order already has an active bill", code="already_billed") from exc

    order.status = OrderStatus.BILLED.value
    await set_table_status(db, order.table_id, TableStatus.BILLED)

    if high_discount:
        record_audit(
            db, branch_id=order.branch_id, action=AuditAction.HIGH_DISCOUNT, actor_id=user.id,
            approver_id=approver_id, entity_type="bill", entity_id=bill.id, amount=breakdown.discount,
            details={"bill_number": bill.bill_number, "subtotal": breakdown.subtotal, "reason": payload.discount_reason},
        )
    queue_event(db, order.branch_id, Topic.BILLING, "bill.created", bill.id,
                {"bill_number": bill.bill_number, "grand_total": str(bill.grand_total), "order_id": str(order.id)})
    if order.table_id:
        queue_event(db, order.branch_id, Topic.TABLES, "table.status_changed", order.table_id,
                    {"status": TableStatus.BILLED.value})
    await commit(db)

    if high_discount and background is not None:
        background.add_task(send_manager_push_alert, order.branch_id, "High discount",
                            f"{bill.bill_number}: ₹{breakdown.discount} off by {user.full_name}",
                            {"bill_id": str(bill.id)})
    return await load_bill(db, bill.id)


async def _settle_if_paid(db: AsyncSession, bill: Bill, background: Optional[BackgroundTasks]) -> None:
    if bill.paid_amount < bill.grand_total:
        bill.status = BillStatus.PARTIALLY_PAID.value
        queue_event(db, bill.branch_id, Topic.BILLING, "bill.payment_recorded", bill.id,
                    {"balance_due": str(bill.balance_due)})
        return

    bill.status = BillStatus.PAID.value
    bill.settled_at = utcnow()
    order = await load_order(db, bill.order_id, for_update=True)
    order.status = OrderStatus.COMPLETED.value
    table = await set_table_vacant(db, order.table_id)
    queue_event(db, bill.branch_id, Topic.BILLING, "bill.settled", bill.id, {"bill_number": bill.bill_number})
    queue_event(db, bill.branch_id, Topic.ORDERS, "order.completed", order.id)
    if table is not None:
        queue_event(db, bill.branch_id, Topic.TABLES, "table.status_changed", table.id,
                    {"status": TableStatus.VACANT.value})
    if background is not None:
        background.add_task(deduct_bom_recipe_inventory, order.id)


async def _new_payment(
    db: AsyncSession, user: CurrentUser, bill: Bill, payment: PaymentCreate, amount: Decimal,
    idempotency_key: Optional[str],
) -> Payment:
    shift = await get_open_shift(db, user.id)
    if payment.payment_mode == PaymentMode.CASH and shift is None:
        raise Conflict("Open a shift before accepting cash", code="no_open_shift")
    record = Payment(
        bill_id=bill.id,
        payment_mode=payment.payment_mode.value,
        amount=amount,
        reference_id=payment.reference_id,
        status=PaymentStatus.SUCCESS.value,
        received_by_id=user.id,
        shift_id=shift.id if shift else None,
        idempotency_key=idempotency_key,
    )
    bill.payments.append(record)
    bill.paid_amount = q(bill.paid_amount + amount)
    return record


async def _load_payable(db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID) -> Bill:
    bill = await load_bill(db, bill_id, for_update=True)
    require_branch_access(user, bill.branch_id)
    if bill.status not in PAYABLE:
        raise InvalidTransition(f"Bill is {bill.status}", details={"status": bill.status})
    return bill


async def record_payment(
    db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID, payload: PaymentCreate,
    idempotency_key: Optional[str] = None, background: Optional[BackgroundTasks] = None,
) -> Tuple[Payment, Bill, Decimal]:
    """Record one tender. Cash may exceed the balance (change is returned); other modes may not."""
    bill = await _load_payable(db, user, bill_id)
    balance = bill.balance_due
    change = ZERO
    amount = payload.amount
    if amount > balance:
        if payload.payment_mode != PaymentMode.CASH:
            raise ValidationFailed("Payment exceeds the balance due", code="overpayment",
                                   details={"balance_due": str(balance)})
        change = amount - balance
        amount = balance

    payment = await _new_payment(db, user, bill, payload, amount, idempotency_key)
    await _settle_if_paid(db, bill, background)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Duplicate payment", code="duplicate_payment") from exc
    await commit(db)
    return payment, await load_bill(db, bill.id), change


async def process_split_payment(
    db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID, payments: List[PaymentCreate],
    background: Optional[BackgroundTasks] = None,
) -> Bill:
    """Settle the full balance across several tenders atomically."""
    bill = await _load_payable(db, user, bill_id)
    total = sum((p.amount for p in payments), ZERO)
    if total != bill.balance_due:
        raise ValidationFailed("Split tenders must add up exactly to the balance due", code="split_mismatch",
                               details={"balance_due": str(bill.balance_due), "tendered": str(total)})
    for payment in payments:
        await _new_payment(db, user, bill, payment, payment.amount, None)
    await _settle_if_paid(db, bill, background)
    await commit(db)
    return await load_bill(db, bill.id)


async def upi_payment_request(db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID) -> dict:
    """
    Build a UPI intent for the remaining balance. The cashier records the payment with the UTR
    as reference_id; automatic confirmation needs a PSP webhook (not implemented).
    """
    bill = await get_bill(db, user, bill_id)
    if bill.status not in PAYABLE:
        raise InvalidTransition(f"Bill is {bill.status}", details={"status": bill.status})
    branch = await get_or_404(db, Branch, bill.branch_id)
    if not branch.upi_vpa:
        raise ValidationFailed("UPI is not configured for this branch", code="upi_not_configured")

    amount = bill.balance_due
    params = {
        "pa": branch.upi_vpa,
        "pn": branch.upi_payee_name or branch.name,
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tr": bill.bill_number,
        "tn": f"Bill {bill.bill_number}",
    }
    url = "upi://pay?" + urlencode(params, quote_via=quote)
    return {"bill_id": bill.id, "bill_number": bill.bill_number, "amount": amount,
            "upi_intent_url": url, "qr_payload": url}


async def void_bill(
    db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID, payload: BillVoidRequest,
    background: Optional[BackgroundTasks] = None, redis_client=None,
) -> Bill:
    bill = await load_bill(db, bill_id, for_update=True)
    require_branch_access(user, bill.branch_id)
    if bill.status != BillStatus.UNPAID.value or bill.paid_amount > ZERO:
        raise Conflict("Only unpaid bills can be voided; paid bills need a refund", code="refund_required",
                       details={"status": bill.status})
    approver_id = await resolve_approver(db, user, bill.branch_id, payload.approver_pin, redis_client)

    bill.status = BillStatus.VOIDED.value
    bill.voided_by_id = user.id
    bill.void_reason = payload.reason
    order = await load_order(db, bill.order_id, for_update=True)
    order.status = OrderStatus.ACTIVE.value
    await set_table_status(db, order.table_id, TableStatus.OCCUPIED)

    record_audit(
        db, branch_id=bill.branch_id, action=AuditAction.BILL_VOID, actor_id=user.id, approver_id=approver_id,
        entity_type="bill", entity_id=bill.id, amount=bill.grand_total,
        details={"bill_number": bill.bill_number, "reason": payload.reason},
    )
    queue_event(db, bill.branch_id, Topic.BILLING, "bill.voided", bill.id, {"bill_number": bill.bill_number})
    queue_event(db, bill.branch_id, Topic.ORDERS, "order.updated", order.id, {"status": order.status})
    await commit(db)

    if background is not None:
        background.add_task(send_manager_push_alert, bill.branch_id, "Bill voided",
                            f"{bill.bill_number} voided by {user.full_name}: {payload.reason}",
                            {"bill_id": str(bill.id)})
    return await load_bill(db, bill.id)
