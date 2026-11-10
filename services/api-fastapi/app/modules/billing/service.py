import re
import secrets
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver, resolve_branch_id
from app.core.business import KITCHEN_TICKETS
from app.core.clock import utcnow
from app.core.entitlements import get_entitlements
from app.core.modules import ModuleKey
from app.core.config import settings
from app.core.enums import (
    AuditAction,
    BillStatus,
    Fulfilment,
    KOTStatus,
    OrderItemStatus,
    OrderStatus,
    OrderType,
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
from app.models.kots import KOT
from app.models.floor import Branch, DiningTable
from app.models.orders import Order, OrderItem
from app.models.staff import StaffUser
from app.modules.billing import receipt as receipts
from app.modules.billing.tax import TaxLine, compute_bill
from app.modules.business.service import bill_name, load_with_logo, logo_data_url
from app.modules.kots.service import make_kots
from app.modules.orders.service import load_order, set_table_status, set_table_vacant
from app.modules.staff.service import get_open_shift
from app.schemas.billing import (
    BillCreate,
    BillVoidRequest,
    PaymentCreate,
    PaymentReverse,
    TerminalPaymentComplete,
    TerminalPaymentStart,
)
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
    status: Optional[BillStatus], on_date: Optional[date], order_id: Optional[uuid.UUID] = None,
) -> Tuple[List[Bill], int]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = select(Bill).where(Bill.branch_id == branch_id).options(*BILL_LOADERS).order_by(Bill.created_at.desc())
    if status is not None:
        stmt = stmt.where(Bill.status == status.value)
    if on_date is not None:
        stmt = stmt.where(Bill.business_date == on_date)
    if order_id is not None:
        stmt = stmt.where(Bill.order_id == order_id)
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
    unfired = [i for i in lines if i.status == OrderItemStatus.ORDERED.value]
    fired_kots = []
    if unfired:
        ent = await get_entitlements(db, user.organization_id, redis_client)
        if not ent.setting(KITCHEN_TICKETS):
            pass  # a shop without a kitchen: nothing is sent anywhere, items are billed as they are
        elif order.order_type == OrderType.COUNTER_SALE.value:
            # A café's counter sale: billing it also sends it to the kitchen, in one action at the counter.
            fired_kots = await make_kots(db, user, order, unfired, kds=ent.has_module(ModuleKey.KDS))
        else:
            raise Conflict("Fire all items to the kitchen before billing", code="unfired_items",
                           details={"order_item_ids": [str(i.id) for i in unfired]})

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
    if fired_kots:
        queue_event(db, order.branch_id, Topic.ORDERS, "order.items_fired", order.id)
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
    await _close_paper_kots(db, order.id)
    table = await set_table_vacant(db, order.table_id)
    queue_event(db, bill.branch_id, Topic.BILLING, "bill.settled", bill.id, {"bill_number": bill.bill_number})
    queue_event(db, bill.branch_id, Topic.ORDERS, "order.completed", order.id)
    if table is not None:
        queue_event(db, bill.branch_id, Topic.TABLES, "table.status_changed", table.id,
                    {"status": TableStatus.VACANT.value})
    if background is not None:
        background.add_task(deduct_bom_recipe_inventory, order.id)


async def _close_paper_kots(db: AsyncSession, order_id: uuid.UUID) -> None:
    """
    A paid order's printed KOTs are done. Paper KOTs have no kitchen stages and nobody marks them served, so they would
    otherwise stay open forever on lists of open tickets. KOTs never printed stay in the print queue untouched.
    """
    kots = (await db.scalars(
        select(KOT).where(KOT.order_id == order_id, KOT.fulfilment == Fulfilment.SLIP.value,
                          KOT.status.in_([KOTStatus.QUEUED.value, KOTStatus.PREPARING.value, KOTStatus.READY.value]),
                          KOT.print_count > 0)
        .options(selectinload(KOT.items)).with_for_update(of=KOT)
    )).all()
    for kot in kots:
        kot.status = KOTStatus.SERVED.value
        for item in kot.items:
            if item.status != KOTStatus.VOIDED.value:
                item.status = KOTStatus.SERVED.value


async def _new_payment(
    db: AsyncSession, user: CurrentUser, bill: Bill, payment: PaymentCreate, amount: Decimal,
    idempotency_key: Optional[str],
) -> Payment:
    # Cash is reconciled per business day (Z-report), so no open shift is required; one is
    # attached when the user happens to have it, which keeps per-shift variance working.
    shift = await get_open_shift(db, user.id)
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


def _refuse_while_terminal_pending(bill: Bill) -> None:
    """
    While a card/UPI terminal is charging this bill, nothing else may change what is owed: another tender could overpay it,
    and a void would orphan money the terminal may already have taken.
    """
    pending = next((p for p in bill.payments if p.status == PaymentStatus.PENDING.value), None)
    if pending is not None:
        raise Conflict("A card or UPI payment on this bill is not finished yet. Complete or resolve it first.",
                       code="terminal_payment_pending", details={"payment_id": str(pending.id)})


async def _load_payable(db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID) -> Bill:
    bill = await load_bill(db, bill_id, for_update=True)
    require_branch_access(user, bill.branch_id)
    if bill.status not in PAYABLE:
        raise InvalidTransition(f"Bill is {bill.status}", details={"status": bill.status})
    _refuse_while_terminal_pending(bill)
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
    _refuse_while_terminal_pending(bill)
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


# --- card/UPI terminal payments (Pine Labs) ----------------------------------------------------------------------------------

TERMINAL_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: staff may read it off a charge slip
TERMINAL_REF_LENGTH = 10  # Pine Labs' BillingRefNo allows at most 10 characters

# What we keep from a terminal's answer. Anything else (cardholder name, expiry, track data) is dropped, and the card number is
# stored masked whatever the device sends.
TERMINAL_DETAIL_KEYS = {
    "BillingRefNo", "ApprovalCode", "HostResponse", "CardNumber", "CardType", "InvoiceNumber", "BatchNumber", "TerminalId",
    "MerchantId", "RetrievalReferenceNumber", "AcquirerName", "AcquiringBankCode", "CardEntryMode", "TransactionType",
    "ResponseCode", "ResponseMsg", "Remark", "PlutusVersion", "PlutusTransactionLogID", "TransactionDate", "TransactionTime",
}


def _terminal_ref() -> str:
    return "".join(secrets.choice(TERMINAL_REF_ALPHABET) for _ in range(TERMINAL_REF_LENGTH))


def mask_card_number(value: str) -> str:
    """Keep only the last four digits of anything that looks like a card number."""
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return value
    return "*" * (len(digits) - 4) + digits[-4:]


def safe_terminal_details(details: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in details.items():
        if key not in TERMINAL_DETAIL_KEYS or value is None:
            continue
        text_value = str(value)[:100]
        safe[key] = mask_card_number(text_value) if key == "CardNumber" else text_value
    return safe


async def start_terminal_payment(
    db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID, payload: TerminalPaymentStart, idempotency_key: Optional[str],
) -> Tuple[Payment, Bill]:
    """
    Record a card/UPI payment as pending before the terminal is asked to charge it. It counts towards the bill only once the
    terminal approves it, but it exists from the start, so a crash between the charge and the answer can never lose it.
    """
    bill = await _load_payable(db, user, bill_id)
    if payload.amount > bill.balance_due:
        raise ValidationFailed("Payment exceeds the balance due", code="overpayment", details={"balance_due": str(bill.balance_due)})
    shift = await get_open_shift(db, user.id)
    record = Payment(
        bill_id=bill.id,
        payment_mode=payload.payment_mode.value,
        amount=payload.amount,
        status=PaymentStatus.PENDING.value,
        received_by_id=user.id,
        shift_id=shift.id if shift else None,
        idempotency_key=idempotency_key,
        provider=payload.provider,
        provider_meta={"billing_ref_no": _terminal_ref(), "started_by": str(user.id)},
    )
    bill.payments.append(record)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Duplicate payment", code="duplicate_payment") from exc
    queue_event(db, bill.branch_id, Topic.BILLING, "bill.payment_started", bill.id, {"payment_id": str(record.id)})
    await commit(db)
    bill = await load_bill(db, bill.id)
    return next(p for p in bill.payments if p.id == record.id), bill


async def _load_payment_and_bill(db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID) -> Tuple[Payment, Bill]:
    payment = await get_or_404(db, Payment, payment_id)
    bill = await load_bill(db, payment.bill_id, for_update=True)
    require_branch_access(user, bill.branch_id)
    return next(p for p in bill.payments if p.id == payment.id), bill


async def complete_terminal_payment(
    db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID, payload: TerminalPaymentComplete,
    background: Optional[BackgroundTasks] = None,
) -> Tuple[Payment, Bill]:
    """
    Record the terminal's answer. Approved: the payment counts and may settle the bill. Declined: it is kept as failed. Repeating
    the same answer is harmless (the app retries after a dropped connection); a different answer for a finished payment is refused.
    """
    payment, bill = await _load_payment_and_bill(db, user, payment_id)
    if payment.provider == "manual":
        raise InvalidTransition("This payment was not taken on a terminal", code="not_terminal_payment")
    approved = payload.outcome == "approved"
    final = PaymentStatus.SUCCESS.value if approved else PaymentStatus.FAILED.value
    if payment.status != PaymentStatus.PENDING.value:
        if payment.status == final:
            return payment, bill
        raise Conflict(f"This payment is already {payment.status}", code="payment_already_completed",
                       details={"status": payment.status})

    payment.status = final
    payment.provider_meta = {
        **(payment.provider_meta or {}),
        **safe_terminal_details(payload.details),
        "outcome": payload.outcome,
        "source": payload.source,
        "message": payload.message,
        "completed_by": str(user.id),
    }
    if approved:
        payment.reference_id = payload.approval_code
        bill.paid_amount = q(bill.paid_amount + payment.amount)
        await _settle_if_paid(db, bill, background)
    else:
        queue_event(db, bill.branch_id, Topic.BILLING, "bill.payment_failed", bill.id, {"payment_id": str(payment.id)})
    await commit(db)
    bill = await load_bill(db, bill.id)
    return next(p for p in bill.payments if p.id == payment.id), bill


async def reverse_terminal_payment(
    db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID, payload: PaymentReverse, redis_client=None,
) -> Tuple[Payment, Bill]:
    """
    A terminal payment was voided on the device (same day, before settlement): it stops counting and the bill reopens for the
    amount, so it can be collected again. Needs a manager and is audited.
    """
    payment, bill = await _load_payment_and_bill(db, user, payment_id)
    if payment.status == PaymentStatus.REVERSED.value:
        return payment, bill
    if payment.status != PaymentStatus.SUCCESS.value or payment.provider == "manual":
        raise InvalidTransition("Only approved card or UPI terminal payments can be reversed", code="not_reversible",
                                details={"status": payment.status, "provider": payment.provider})
    approver_id = await resolve_approver(db, user, bill.branch_id, payload.approver_pin, redis_client)
    if payload.check_only:
        return payment, bill

    payment.status = PaymentStatus.REVERSED.value
    payment.provider_meta = {
        **(payment.provider_meta or {}),
        "reversal": {**safe_terminal_details(payload.details), "reason": payload.reason, "by": str(user.id),
                     "approved_by": str(approver_id), "at": utcnow().isoformat()},
    }
    bill.paid_amount = q(bill.paid_amount - payment.amount)
    bill.status = BillStatus.UNPAID.value if bill.paid_amount <= ZERO else BillStatus.PARTIALLY_PAID.value
    bill.settled_at = None
    order = await load_order(db, bill.order_id, for_update=True)
    if order.status == OrderStatus.COMPLETED.value:
        order.status = OrderStatus.BILLED.value  # the guest may have left: the table is not re-occupied

    record_audit(
        db, branch_id=bill.branch_id, action=AuditAction.PAYMENT_REVERSE, actor_id=user.id, approver_id=approver_id,
        entity_type="payment", entity_id=payment.id, amount=payment.amount,
        details={"bill_number": bill.bill_number, "mode": payment.payment_mode, "provider": payment.provider,
                 "billing_ref_no": (payment.provider_meta or {}).get("billing_ref_no"), "reason": payload.reason},
    )
    queue_event(db, bill.branch_id, Topic.BILLING, "bill.payment_reversed", bill.id,
                {"payment_id": str(payment.id), "balance_due": str(bill.balance_due)})
    queue_event(db, bill.branch_id, Topic.ORDERS, "order.updated", order.id, {"status": order.status})
    await commit(db)
    bill = await load_bill(db, bill.id)
    return next(p for p in bill.payments if p.id == payment.id), bill


# --- printed customer bill ---------------------------------------------------------------------------------------------------


async def build_receipt(db: AsyncSession, user: CurrentUser, bill_id: uuid.UUID) -> receipts.Receipt:
    bill = await get_bill(db, user, bill_id)
    order = await get_or_404(db, Order, bill.order_id)
    branch = await get_or_404(db, Branch, bill.branch_id)
    table = await db.get(DiningTable, order.table_id) if order.table_id else None
    cashier = await db.get(StaffUser, user.id)
    items = (await db.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.status != OrderItemStatus.CANCELLED.value)
        .order_by(OrderItem.created_at)
    )).all()
    business = await load_with_logo(db, branch.organization_id)
    return receipts.Receipt(
        business_name=bill_name(business), logo_data_url=logo_data_url(business),
        branch_name=branch.name, branch_code=branch.code, address=branch.address, phone=branch.phone, gstin=branch.gstin,
        bill_number=bill.bill_number, order_number=order.order_number, order_type=order.order_type,
        table_number=table.table_number if table else None, guest_count=order.guest_count,
        billed_at=bill.created_at.astimezone(ZoneInfo(branch.timezone)),
        cashier=cashier.full_name if cashier else None,
        lines=[receipts.ReceiptLine(
            quantity=i.quantity,
            name=f"{i.name_snapshot} ({i.variant_name_snapshot})" if i.variant_name_snapshot else i.name_snapshot,
            amount=i.line_total, unit=i.unit_snapshot, unit_price=i.unit_price_snapshot, hsn=i.hsn_snapshot) for i in items],
        token=order.token_number,
        subtotal=bill.subtotal, discount=bill.discount_amount, discount_reason=bill.discount_reason,
        cgst=bill.tax_cgst, sgst=bill.tax_sgst, grand_total=bill.grand_total,
        payments=[receipts.ReceiptPayment(mode=p.payment_mode, amount=p.amount, reference=p.reference_id)
                  for p in bill.payments if p.status == PaymentStatus.SUCCESS.value],
        balance_due=bill.balance_due,
    )
