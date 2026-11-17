"""
Dress shop returns, credit notes and store credit. Plan: docs/retail-modules/01-apparel.md sections 3.3 and 3.5.

A return is made against a paid sale. What comes back is valued from the sale's own stored lines (never recomputed), so the
returns of a line always add up to the line exactly. The customer gets cash (it leaves the drawer: the cash day subtracts it) or
store credit (a balance valid at every branch, spent later as a payment). An exchange is a return to store credit followed by a
new sale paid with that credit.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver, resolve_branch_id
from app.core.clock import business_date
from app.core.enums import (
    ApparelMovementKind,
    ApparelPaymentMode,
    ApparelRefundMode,
    AuditAction,
    BillStatus,
    StoreCreditStatus,
    Topic,
)
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.numbering import next_document_number
from app.core.pagination import PageParams, paginate
from app.models.apparel import ApparelPayment, ApparelReturn, ApparelReturnLine, ApparelSale, ApparelSaleLine, ApparelStoreCredit
from app.models.customers import Customer
from app.models.floor import Branch
from app.models.staff import StaffUser
from app.modules.apparel import stock as stocks
from app.modules.apparel.catalog import load_settings
from app.modules.apparel.sales import ENTITY, load_sale, tenders
from app.modules.business.service import bill_name, load_with_logo, logo_data_url
from app.modules.retailkit import receipt as rk
from app.modules.staff.service import get_open_shift
from app.schemas.apparel import ApparelReturnCreate

RETURN_LOADERS = (selectinload(ApparelReturn.lines),)
CLIENT_RETURN_INDEX = "uq_apparel_returns_branch_client_return"


async def load_return(db: AsyncSession, return_id: uuid.UUID) -> ApparelReturn:
    return await get_or_404(db, ApparelReturn, return_id, *RETURN_LOADERS, populate_existing=True)


async def get_return(db: AsyncSession, user: CurrentUser, return_id: uuid.UUID) -> ApparelReturn:
    found = await load_return(db, return_id)
    require_branch_access(user, found.branch_id)
    return found


async def list_returns(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID], *, sale_id: Optional[uuid.UUID],
    on_date: Optional[date],
) -> Tuple[List[ApparelReturn], int]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = (select(ApparelReturn).where(ApparelReturn.branch_id == branch_id).options(*RETURN_LOADERS)
            .order_by(ApparelReturn.created_at.desc(), ApparelReturn.id))
    if sale_id is not None:
        stmt = stmt.where(ApparelReturn.sale_id == sale_id)
    if on_date is not None:
        stmt = stmt.where(ApparelReturn.business_date == on_date)
    return await paginate(db, stmt, params)


async def _existing(db: AsyncSession, branch_id: uuid.UUID, client_return_id: str) -> Optional[ApparelReturn]:
    found = await db.scalar(select(ApparelReturn.id).where(ApparelReturn.branch_id == branch_id,
                                                           ApparelReturn.client_return_id == client_return_id))
    return await load_return(db, found) if found is not None else None


def _share(whole: Decimal, quantity: int, of: int, left: Decimal, last: bool) -> Decimal:
    """The part of a stored line value that `quantity` of its `of` pieces carry; the last pieces take what is left."""
    return left if last else min(left, q(whole * quantity / of))


async def create_return(
    db: AsyncSession, user: CurrentUser, payload: ApparelReturnCreate, *, redis_client: Any = None,
    background: Optional[BackgroundTasks] = None,
) -> ApparelReturn:
    sale = await load_sale(db, payload.sale_id, for_update=True)
    require_branch_access(user, sale.branch_id)
    branch_id = sale.branch_id
    if payload.client_return_id is not None:  # a return already stored is returned as it is
        existing = await _existing(db, branch_id, payload.client_return_id)
        if existing is not None:
            return existing
    if sale.status != BillStatus.PAID.value:
        raise Conflict("Only a paid sale can have goods returned against it", code="sale_not_paid", details={"status": sale.status})

    by_id: Dict[uuid.UUID, ApparelSaleLine] = {line.id: line for line in sale.lines}
    asked: Dict[uuid.UUID, Tuple[int, bool]] = {}
    for item in payload.lines:
        if item.sale_line_id not in by_id:
            raise NotFound("Sale line not found", code="sale_line_not_found", details={"sale_line_id": str(item.sale_line_id)})
        if item.sale_line_id in asked:
            raise ValidationFailed("A line may be listed once", code="duplicate_line", details={"sale_line_id": str(item.sale_line_id)})
        asked[item.sale_line_id] = (item.quantity, item.restock)
    for line_id, (quantity, _) in asked.items():
        line = by_id[line_id]
        if quantity > line.quantity - line.returned_quantity:
            raise Conflict("More pieces than are left to return on this line", code="return_exceeds_sold",
                           details={"sale_line_id": str(line_id), "returnable": line.quantity - line.returned_quantity})

    branch = await get_or_404(db, Branch, branch_id)
    settings = await load_settings(db, user.organization_id)
    today = business_date(branch.timezone)
    late = settings.return_days > 0 and (today - sale.business_date).days > settings.return_days
    approver_id = await resolve_approver(db, user, branch_id, payload.approver_pin, redis_client) if late else None

    # What earlier returns already took from each line, so the last pieces take exactly what is left.
    spent: Dict[uuid.UUID, Dict[str, Decimal]] = {}
    for row in (await db.execute(
        select(ApparelReturnLine.sale_line_id, func.coalesce(func.sum(ApparelReturnLine.amount), 0),
               func.coalesce(func.sum(ApparelReturnLine.taxable_value), 0), func.coalesce(func.sum(ApparelReturnLine.cgst), 0),
               func.coalesce(func.sum(ApparelReturnLine.sgst), 0), func.coalesce(func.sum(ApparelReturnLine.igst), 0))
        .where(ApparelReturnLine.sale_line_id.in_(asked)).group_by(ApparelReturnLine.sale_line_id)
    )).all():
        spent[row[0]] = {"amount": Decimal(row[1]), "taxable": Decimal(row[2]), "cgst": Decimal(row[3]), "sgst": Decimal(row[4]),
                         "igst": Decimal(row[5])}

    shift = await get_open_shift(db, user.id) if payload.refund_mode == ApparelRefundMode.CASH else None
    ret = ApparelReturn(
        id=uuid.uuid4(), branch_id=branch_id, return_number=await next_document_number(db, branch, "credit_note"), sale_id=sale.id,
        business_date=today, reason=payload.reason, refund_mode=payload.refund_mode.value, total_amount=ZERO, taxable_value=ZERO,
        tax_cgst=ZERO, tax_sgst=ZERO, tax_igst=ZERO, created_by_id=user.staff_id, approved_by_id=approver_id,
        shift_id=shift.id if shift else None, client_return_id=payload.client_return_id,
    )
    ret.lines = []
    back: Dict[uuid.UUID, int] = {}
    for line_id, (quantity, restock) in asked.items():
        line = by_id[line_id]
        done = spent.get(line_id, {"amount": ZERO, "taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO})
        last = quantity == line.quantity - line.returned_quantity
        values = {
            "amount": _share(line.line_total, quantity, line.quantity, line.line_total - done["amount"], last),
            "taxable": _share(line.taxable_value, quantity, line.quantity, line.taxable_value - done["taxable"], last),
            "cgst": _share(line.cgst, quantity, line.quantity, line.cgst - done["cgst"], last),
            "sgst": _share(line.sgst, quantity, line.quantity, line.sgst - done["sgst"], last),
            "igst": _share(line.igst, quantity, line.quantity, line.igst - done["igst"], last),
        }
        ret.lines.append(ApparelReturnLine(
            branch_id=branch_id, sale_line_id=line.id, quantity=quantity, amount=values["amount"], taxable_value=values["taxable"],
            cgst=values["cgst"], sgst=values["sgst"], igst=values["igst"], restock=restock,
        ))
        ret.total_amount += values["amount"]
        ret.taxable_value += values["taxable"]
        ret.tax_cgst += values["cgst"]
        ret.tax_sgst += values["sgst"]
        ret.tax_igst += values["igst"]
        line.returned_quantity += quantity
        if restock:
            back[line.variant_id] = back.get(line.variant_id, 0) + quantity
    if ret.total_amount <= ZERO:
        raise ValidationFailed("There is nothing to refund on these lines", code="nothing_to_refund")

    db.add(ret)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if payload.client_return_id is not None and CLIENT_RETURN_INDEX in str(exc.orig):  # the same return arrived twice at once
            existing = await _existing(db, branch_id, payload.client_return_id)
            if existing is not None:
                return existing
        raise

    if payload.refund_mode == ApparelRefundMode.STORE_CREDIT:
        db.add(ApparelStoreCredit(
            organization_id=user.organization_id, return_id=ret.id, code=ret.return_number, customer_id=sale.customer_id,
            amount=ret.total_amount, balance=ret.total_amount, status=StoreCreditStatus.OPEN.value,
        ))
    if back:
        levels = await stocks.lock_levels(db, branch_id, back)
        for variant_id, pieces in back.items():
            stocks.move(db, levels[variant_id], ApparelMovementKind.RETURN, pieces, user_id=user.staff_id, return_id=ret.id,
                        sale_id=sale.id, reason=f"Return {ret.return_number}")
        stocks.announce(db, branch_id, back)
    record_audit(db, branch_id=branch_id, action=AuditAction.SALE_RETURN, actor_id=user.staff_id, approver_id=approver_id,
                 entity_type=ENTITY, entity_id=sale.id, amount=ret.total_amount,
                 details={"sale_number": sale.sale_number, "return_number": ret.return_number, "refund_mode": ret.refund_mode,
                          "reason": payload.reason})
    queue_event(db, branch_id, Topic.BILLING, "apparel.return.created", ret.id,
                {"return_number": ret.return_number, "total_amount": str(ret.total_amount), "refund_mode": ret.refund_mode})
    await commit(db)
    if background is not None:
        from app.worker.tasks import send_manager_push_alert
        background.add_task(send_manager_push_alert, branch_id, "Sale return",
                            f"{ret.return_number}: ₹{ret.total_amount} {ret.refund_mode.replace('_', ' ')} ({sale.sale_number})",
                            {"return_id": str(ret.id)})
    return await load_return(db, ret.id)


# --- store credit --------------------------------------------------------------------------------------------------------------


async def get_store_credit(db: AsyncSession, user: CurrentUser, code: str, *, for_update: bool = False) -> ApparelStoreCredit:
    stmt = select(ApparelStoreCredit).where(ApparelStoreCredit.organization_id == user.organization_id,
                                            ApparelStoreCredit.code == code.strip())
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    credit = await db.scalar(stmt)
    if credit is None:
        raise NotFound("Store credit not found", code="store_credit_not_found", details={"code": code})
    return credit


async def pay_with_credit(
    db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID, *, code: str, amount: Decimal, idempotency_key: Optional[str],
) -> Tuple[ApparelPayment, ApparelSale]:
    """Spend store credit on a sale: min(credit balance, amount asked, what the sale still owes). Never gives change."""
    sale: Any = await tenders.load_payable(db, user, sale_id)  # the sale is locked before the credit, always in this order
    credit = await get_store_credit(db, user, code, for_update=True)
    if credit.status != StoreCreditStatus.OPEN.value or credit.balance <= ZERO:
        raise Conflict("This store credit has been used up or cancelled", code="store_credit_unavailable",
                       details={"status": credit.status})
    applied = min(credit.balance, amount, sale.balance_due)
    if applied <= ZERO:
        raise ValidationFailed("Nothing is owed on this sale", code="nothing_due")
    payment: Any = await tenders.add_payment(db, user, sale, mode=ApparelPaymentMode.CREDIT_NOTE.value, amount=applied,
                                             reference_id=credit.code, idempotency_key=idempotency_key, credit_note_id=credit.id)
    credit.balance = q(credit.balance - applied)
    if credit.balance == ZERO:
        credit.status = StoreCreditStatus.USED.value
    await tenders.settle_if_paid(db, sale)
    await tenders.flush_payment(db)
    await commit(db)
    sale = await load_sale(db, sale.id)
    return next(p for p in sale.payments if p.id == payment.id), sale


# --- the printed credit note ---------------------------------------------------------------------------------------------------


async def build_receipt(db: AsyncSession, user: CurrentUser, return_id: uuid.UUID) -> rk.RetailReceipt:
    ret = await get_return(db, user, return_id)
    sale = await load_sale(db, ret.sale_id)
    sale_lines = {line.id: line for line in sale.lines}
    branch = await get_or_404(db, Branch, ret.branch_id)
    business = await load_with_logo(db, branch.organization_id)
    customer = await db.get(Customer, sale.customer_id) if sale.customer_id else None
    cashier = await db.get(StaffUser, ret.created_by_id) if ret.created_by_id else None
    lines = []
    for item in ret.lines:
        source = sale_lines[item.sale_line_id]
        lines.append(rk.RetailLine(
            name=source.name_snapshot, amount=item.amount, quantity_text=str(item.quantity),
            detail=f"{source.size_snapshot} / {source.colour_snapshot}", hsn=source.hsn_snapshot, tax_rate=source.tax_rate))
    credit = await db.scalar(select(ApparelStoreCredit).where(ApparelStoreCredit.return_id == ret.id))
    refs = [f"Against: {sale.sale_number}", f"Reason: {ret.reason}"]
    if credit is not None:
        refs.append(f"Store credit: {credit.code}")
    return rk.RetailReceipt(
        title="CREDIT NOTE", number=ret.return_number, issued_at=ret.created_at.astimezone(ZoneInfo(branch.timezone)),
        branch_name=branch.name, business_name=bill_name(business), logo_data_url=logo_data_url(business), address=branch.address,
        phone=branch.phone, gstin=branch.gstin, cashier=cashier.full_name if cashier else None,
        customer_name=customer.name if customer else None, customer_phone=customer.phone if customer else None,
        customer_gstin=customer.gstin if customer else None, lines=lines, subtotal=ret.total_amount, discount=ZERO,
        taxable_value=ret.taxable_value, cgst=ret.tax_cgst, sgst=ret.tax_sgst, igst=ret.tax_igst, round_off=ZERO,
        grand_total=ret.total_amount,
        tax_summary=rk.tax_summary([(sale_lines[i.sale_line_id].tax_rate, i.taxable_value, i.cgst + i.sgst + i.igst)
                                    for i in ret.lines]),
        reference_lines=refs,
        footer_lines=[("Refunded in cash" if ret.refund_mode == ApparelRefundMode.CASH.value else "To store credit", rk.money(ret.total_amount))],
        closing="Keep this note to use your store credit.",
    )
