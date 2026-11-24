"""
Dress shop sales: one request makes a numbered tax invoice (no separate order and bill), takes the pieces out of stock, and is
then paid with the shared tender rules (`retailkit.tenders`). Plan: docs/retail-modules/01-apparel.md sections 3.2-3.4.
"""

import uuid
from datetime import date, datetime
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
from app.core.clock import business_date, utcnow
from app.core.config import settings as app_settings
from app.core.enums import ApparelMovementKind, AuditAction, BillStatus, PaymentStatus, Topic
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.core.numbering import next_document_number
from app.core.pagination import PageParams, paginate
from app.models.apparel import ApparelPayment, ApparelProduct, ApparelReturn, ApparelSale, ApparelSaleLine, ApparelVariant
from app.models.customers import Customer
from app.models.floor import Branch
from app.models.staff import StaffUser
from app.modules.apparel import stock as stocks
from app.modules.apparel.catalog import load_settings
from app.modules.apparel.tax import SaleLineIn, SlabRule, invoice_lines
from app.modules.business.service import bill_name, load_with_logo, logo_data_url
from app.modules.retailkit import receipt as rk
from app.modules.retailkit.tax import compute_invoice
from app.modules.retailkit.tenders import Tenders
from app.schemas.apparel import ApparelSaleCreate, ApparelVoid
from app.worker.tasks import send_manager_push_alert

ENTITY = "apparel_sale"
SALE_LOADERS = (selectinload(ApparelSale.payments), selectinload(ApparelSale.lines))
CLIENT_SALE_INDEX = "uq_apparel_sales_branch_client_sale"


async def _on_settled(db: AsyncSession, sale: Any) -> None:
    # The pieces left stock when the sale was made; settling only announces it.
    queue_event(db, sale.branch_id, Topic.BILLING, "apparel.sale.settled", sale.id, {"sale_number": sale.sale_number})


class _SaleTenders(Tenders[ApparelSale, ApparelPayment]):
    """The kit's tenders, loading the sale with its lines too (every answer is the full sale)."""

    async def load(self, db: AsyncSession, sale_id: uuid.UUID, *, for_update: bool = False) -> ApparelSale:
        return await load_sale(db, sale_id, for_update=for_update)


tenders = _SaleTenders(
    ApparelSale, ApparelPayment, entity_type=ENTITY, event_prefix="apparel.sale", on_settled=_on_settled,
)


async def load_sale(db: AsyncSession, sale_id: uuid.UUID, *, for_update: bool = False) -> ApparelSale:
    return await get_or_404(db, ApparelSale, sale_id, *SALE_LOADERS, for_update=for_update, populate_existing=True)


async def get_sale(db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID) -> ApparelSale:
    sale = await load_sale(db, sale_id)
    require_branch_access(user, sale.branch_id)
    return sale


async def list_sales(
    db: AsyncSession, user: CurrentUser, params: PageParams, branch_id: Optional[uuid.UUID], *, on_date: Optional[date],
    status: Optional[BillStatus], customer_id: Optional[uuid.UUID], search: Optional[str], phone: Optional[str] = None,
) -> Tuple[List[ApparelSale], int]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = (select(ApparelSale).where(ApparelSale.branch_id == branch_id).options(*SALE_LOADERS)
            .order_by(ApparelSale.created_at.desc(), ApparelSale.id))
    if on_date is not None:
        stmt = stmt.where(ApparelSale.business_date == on_date)
    if status is not None:
        stmt = stmt.where(ApparelSale.status == status.value)
    if customer_id is not None:
        stmt = stmt.where(ApparelSale.customer_id == customer_id)
    if search and search.strip():
        stmt = stmt.where(ApparelSale.sale_number.ilike(f"%{search.strip().replace('%', '')}%"))
    if phone and phone.strip():
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits:
            stmt = stmt.where(ApparelSale.customer_id.in_(
                select(Customer.id).where(Customer.organization_id == user.organization_id, Customer.phone.ilike(f"%{digits}%"))))
    return await paginate(db, stmt, params)


async def _customer(db: AsyncSession, user: CurrentUser, customer_id: Optional[uuid.UUID]) -> Optional[Customer]:
    if customer_id is None:
        return None
    customer = await db.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == user.organization_id))
    if customer is None or not customer.is_active:
        raise ValidationFailed("Customer not found", code="customer_not_found", details={"customer_id": str(customer_id)})
    return customer


async def _salesperson(db: AsyncSession, user: CurrentUser, branch_id: uuid.UUID, staff_id: Optional[uuid.UUID]) -> None:
    if staff_id is None:
        return
    staff = await db.scalar(select(StaffUser).where(StaffUser.id == staff_id, StaffUser.organization_id == user.organization_id))
    if staff is None or not staff.is_active or staff.branch_id not in (branch_id, None):
        raise ValidationFailed("Salesperson not found", code="salesperson_not_found", details={"salesperson_id": str(staff_id)})


async def _existing(db: AsyncSession, branch_id: uuid.UUID, client_sale_id: str) -> Optional[ApparelSale]:
    found = await db.scalar(select(ApparelSale.id).where(ApparelSale.branch_id == branch_id,
                                                         ApparelSale.client_sale_id == client_sale_id))
    return await load_sale(db, found) if found is not None else None


async def create_sale(
    db: AsyncSession, user: CurrentUser, payload: ApparelSaleCreate, *, redis_client: Any = None,
    background: Optional[BackgroundTasks] = None,
) -> ApparelSale:
    """The whole cart becomes a numbered invoice in one transaction; its pieces leave stock at once."""
    branch_id = resolve_branch_id(user, payload.branch_id)
    if payload.client_sale_id is not None:  # a sale already stored is returned as it is
        existing = await _existing(db, branch_id, payload.client_sale_id)
        if existing is not None:
            return existing
    branch = await get_or_404(db, Branch, branch_id)
    settings = await load_settings(db, user.organization_id)
    customer = await _customer(db, user, payload.customer_id)
    await _salesperson(db, user, branch_id, payload.salesperson_id)

    variant_ids = {line.variant_id for line in payload.lines}
    found = {v.id: (v, p) for v, p in (await db.execute(
        select(ApparelVariant, ApparelProduct).join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .where(ApparelVariant.id.in_(variant_ids), ApparelVariant.organization_id == user.organization_id)
    )).tuples().all()}
    missing = variant_ids - set(found)
    if missing:
        raise NotFound("Variant not found", code="variant_not_found", details={"variant_ids": sorted(map(str, missing))})
    inactive = sorted(str(i) for i, (v, p) in found.items() if not (v.is_active and p.is_active))
    if inactive:
        raise ValidationFailed("This item is no longer sold", code="variant_inactive", details={"variant_ids": inactive})

    # Prices: the variant's own unless the request names one. Never above MRP; any other price needs a manager.
    overrides: List[Tuple[ApparelVariant, Decimal]] = []
    prices: List[Decimal] = []
    for line in payload.lines:
        variant, _ = found[line.variant_id]
        price = line.unit_price if line.unit_price is not None else variant.price
        if variant.mrp is not None and price > variant.mrp:
            raise ValidationFailed("The price may not exceed the MRP", code="price_above_mrp",
                                   details={"variant_id": str(variant.id), "mrp": str(variant.mrp)})
        if price != variant.price:
            overrides.append((variant, price))
        prices.append(price)

    rule = SlabRule(low_rate=settings.low_rate, slab_price=settings.slab_price, high_rate=settings.high_rate,
                    inclusive=settings.prices_include_tax)
    ins = [SaleLineIn(quantity=line.quantity, unit_price=price, line_discount=line.line_discount,
                      tax_mode=found[line.variant_id][1].tax_mode, fixed_rate=found[line.variant_id][1].tax_rate)
           for line, price in zip(payload.lines, prices, strict=True)]
    tax_lines = invoice_lines(ins, payload.bill_discount, rule)
    out = compute_invoice(tax_lines, payload.bill_discount, inclusive=settings.prices_include_tax, interstate=False,
                          round_to_rupee=settings.round_to_rupee)

    high_discount = out.subtotal > ZERO and out.discount / out.subtotal * 100 > app_settings.DISCOUNT_APPROVAL_THRESHOLD_PERCENT
    approver_id = None
    if overrides or high_discount:
        approver_id = await resolve_approver(db, user, branch_id, payload.approver_pin, redis_client)

    sale = ApparelSale(
        id=uuid.uuid4(), branch_id=branch_id, sale_number=await next_document_number(db, branch, "bill"),
        business_date=business_date(branch.timezone), customer_id=customer.id if customer else None,
        salesperson_id=payload.salesperson_id, created_by_id=user.staff_id, subtotal=out.subtotal,
        discount_amount=out.discount, discount_reason=payload.discount_reason,
        discount_approved_by_id=approver_id if high_discount else None, taxable_value=out.taxable_value,
        tax_cgst=out.cgst, tax_sgst=out.sgst, tax_igst=out.igst, round_off=out.round_off, grand_total=out.grand_total,
        paid_amount=ZERO, status=BillStatus.UNPAID.value, client_sale_id=payload.client_sale_id, notes=payload.notes,
    )
    sale.payments = []
    sale.lines = []
    for line, price, tax_in, tax_out in zip(payload.lines, prices, tax_lines, out.lines, strict=True):
        variant, product = found[line.variant_id]
        sale.lines.append(ApparelSaleLine(
            branch_id=branch_id, variant_id=variant.id, name_snapshot=product.name, size_snapshot=variant.size,
            colour_snapshot=variant.colour, hsn_snapshot=product.hsn_code, barcode_snapshot=variant.barcode,
            quantity=line.quantity, mrp_snapshot=variant.mrp, unit_price=price, line_discount=q(line.line_discount),
            discount=tax_out.discount, tax_rate=tax_in.tax_rate, taxable_value=tax_out.taxable_value, cgst=tax_out.cgst,
            sgst=tax_out.sgst, igst=tax_out.igst, line_total=tax_out.total, returned_quantity=0,
        ))
    if sale.grand_total == ZERO:  # given away entirely: nothing to collect
        sale.status = BillStatus.PAID.value
        sale.settled_at = utcnow()
    db.add(sale)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if payload.client_sale_id is not None and CLIENT_SALE_INDEX in str(exc.orig):  # the same sale arrived twice at once
            existing = await _existing(db, branch_id, payload.client_sale_id)
            if existing is not None:
                return existing
        raise

    sold: Dict[uuid.UUID, int] = {}
    for line in payload.lines:
        sold[line.variant_id] = sold.get(line.variant_id, 0) + line.quantity
    levels = await stocks.lock_levels(db, branch_id, sold)
    for variant_id, pieces in sold.items():
        stocks.move(db, levels[variant_id], ApparelMovementKind.SALE, -pieces, user_id=user.staff_id, sale_id=sale.id)

    for variant, price in overrides:
        record_audit(db, branch_id=branch_id, action=AuditAction.PRICE_OVERRIDE, actor_id=user.staff_id,
                     approver_id=approver_id, entity_type=ENTITY, entity_id=sale.id, amount=price,
                     details={"sale_number": sale.sale_number, "variant_id": str(variant.id), "list_price": variant.price,
                              "price": price})
    if high_discount:
        record_audit(db, branch_id=branch_id, action=AuditAction.HIGH_DISCOUNT, actor_id=user.staff_id,
                     approver_id=approver_id, entity_type=ENTITY, entity_id=sale.id, amount=out.discount,
                     details={"sale_number": sale.sale_number, "subtotal": out.subtotal, "reason": payload.discount_reason})
    queue_event(db, branch_id, Topic.BILLING, "apparel.sale.created", sale.id,
                {"sale_number": sale.sale_number, "grand_total": str(sale.grand_total)})
    stocks.announce(db, branch_id, sold)
    await commit(db)

    if high_discount and background is not None:
        background.add_task(send_manager_push_alert, branch_id, "High discount",
                            f"{sale.sale_number}: ₹{out.discount} off by {user.full_name}", {"sale_id": str(sale.id)})
    return await load_sale(db, sale.id)


async def void_sale(
    db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID, payload: ApparelVoid, *, redis_client: Any = None,
    background: Optional[BackgroundTasks] = None,
) -> ApparelSale:
    """
    Only an unpaid sale with nothing paid can be voided; its pieces go back. A paid sale needs a return.

    A sale no payment was ever tried on (cancelled at the counter before paying) only needs the cashier's confirmation: no
    money has moved. Once any payment was attempted (a declined card, a reversed payment) a manager must approve, since money
    may have changed hands. Every void is audited and sent to the managers either way.
    """
    sale = await load_sale(db, sale_id, for_update=True)
    require_branch_access(user, sale.branch_id)
    if sale.status != BillStatus.UNPAID.value or sale.paid_amount > ZERO:
        raise Conflict("Only unpaid sales can be voided; a paid sale is undone with a return", code="refund_required",
                       details={"status": sale.status})
    Tenders.refuse_while_terminal_pending(sale)
    untouched = len(sale.payments) == 0
    if untouched and not payload.approver_pin:
        approver_id = user.id if user.is_manager else None
    else:
        approver_id = await resolve_approver(db, user, sale.branch_id, payload.approver_pin, redis_client)
    reason = payload.reason or "Cancelled before payment"
    sale.status = BillStatus.VOIDED.value
    sale.voided_by_id = user.staff_id
    sale.void_reason = reason

    back: Dict[uuid.UUID, int] = {}
    for line in sale.lines:
        back[line.variant_id] = back.get(line.variant_id, 0) + line.quantity
    levels = await stocks.lock_levels(db, sale.branch_id, back)
    for variant_id, pieces in back.items():
        stocks.move(db, levels[variant_id], ApparelMovementKind.SALE_VOID, pieces, user_id=user.staff_id, sale_id=sale.id)

    record_audit(db, branch_id=sale.branch_id, action=AuditAction.BILL_VOID, actor_id=user.staff_id, approver_id=approver_id,
                 entity_type=ENTITY, entity_id=sale.id, amount=sale.grand_total,
                 details={"sale_number": sale.sale_number, "reason": reason})
    queue_event(db, sale.branch_id, Topic.BILLING, "apparel.sale.voided", sale.id, {"sale_number": sale.sale_number})
    stocks.announce(db, sale.branch_id, back)
    await commit(db)
    if background is not None:
        background.add_task(send_manager_push_alert, sale.branch_id, "Sale voided",
                            f"{sale.sale_number} voided by {user.full_name}: {reason}", {"sale_id": str(sale.id)})
    return await load_sale(db, sale.id)


# --- the printed invoice -----------------------------------------------------------------------------------------------------


def _qty(n: int) -> str:
    return str(n)


async def build_receipt(db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID) -> rk.RetailReceipt:
    sale = await get_sale(db, user, sale_id)
    branch = await get_or_404(db, Branch, sale.branch_id)
    business = await load_with_logo(db, branch.organization_id)
    customer = await db.get(Customer, sale.customer_id) if sale.customer_id else None
    cashier = await db.get(StaffUser, sale.created_by_id) if sale.created_by_id else None
    lines = [rk.RetailLine(
        name=line.name_snapshot, amount=line.line_total, quantity_text=_qty(line.quantity),
        detail=f"{line.size_snapshot} / {line.colour_snapshot}" + (f" @ {rk.money(line.unit_price)}" if line.quantity > 1 else ""),
        hsn=line.hsn_snapshot, tax_rate=line.tax_rate,
        mrp_total=q(line.mrp_snapshot * line.quantity) if line.mrp_snapshot is not None else None,
    ) for line in sale.lines]
    return rk.RetailReceipt(
        title="TAX INVOICE" if branch.gstin else "BILL", number=sale.sale_number,
        issued_at=_local(sale.created_at, branch.timezone), branch_name=branch.name, business_name=bill_name(business),
        logo_data_url=logo_data_url(business), address=branch.address, phone=branch.phone, gstin=branch.gstin,
        cashier=cashier.full_name if cashier else None, customer_name=customer.name if customer else None,
        customer_phone=customer.phone if customer else None, customer_gstin=customer.gstin if customer else None,
        lines=lines, subtotal=sale.subtotal, discount=sale.discount_amount, discount_reason=sale.discount_reason,
        taxable_value=sale.taxable_value, cgst=sale.tax_cgst, sgst=sale.tax_sgst, igst=sale.tax_igst,
        round_off=sale.round_off, grand_total=sale.grand_total,
        tax_summary=rk.tax_summary([(line.tax_rate, line.taxable_value, line.cgst + line.sgst + line.igst)
                                    for line in sale.lines]),
        payments=[rk.RetailPayment(mode=p.payment_mode, amount=p.amount, reference=p.reference_id)
                  for p in sale.payments if p.status == PaymentStatus.SUCCESS.value],
        balance_due=sale.balance_due if sale.status != BillStatus.VOIDED.value else ZERO,
        reference_lines=["VOIDED"] if sale.status == BillStatus.VOIDED.value else [],
    )


def _local(moment: datetime, tz: str) -> datetime:
    return moment.astimezone(ZoneInfo(tz))


# --- the cash day ------------------------------------------------------------------------------------------------------------


async def cash_source(db: AsyncSession, *, branch_id: uuid.UUID, since: Optional[datetime],
                      shift_id: Optional[uuid.UUID]) -> Decimal:
    """Net cash the dress shop took: successful cash payments minus cash refunds paid out on returns."""
    taken = select(func.coalesce(func.sum(ApparelPayment.amount), 0)).where(
        ApparelPayment.payment_mode == "cash", ApparelPayment.status == PaymentStatus.SUCCESS.value)
    refunded = select(func.coalesce(func.sum(ApparelReturn.total_amount), 0)).where(ApparelReturn.refund_mode == "cash")
    if shift_id is not None:
        taken = taken.where(ApparelPayment.shift_id == shift_id)
        refunded = refunded.where(ApparelReturn.shift_id == shift_id)
    else:
        taken = taken.where(ApparelPayment.branch_id == branch_id)
        refunded = refunded.where(ApparelReturn.branch_id == branch_id)
        if since is not None:
            taken = taken.where(ApparelPayment.created_at >= since)
            refunded = refunded.where(ApparelReturn.created_at >= since)
    return Decimal(await db.scalar(taken) or 0) - Decimal(await db.scalar(refunded) or 0)
