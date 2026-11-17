"""
Dress shop labels and reports. Plan: docs/retail-modules/01-apparel.md sections 6 and 7.

The day report replaces the Z-report for dress shops and is computed live from the day's paid sales, successful payments and
returns; nothing is archived. Barcode labels are one printable HTML page sized for A4 sticker sheets.
"""

import html
import uuid
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, resolve_branch_id
from app.core.clock import business_date
from app.core.enums import BillStatus, PaymentStatus
from app.core.errors import NotFound, ValidationFailed
from app.core.lookups import get_or_404
from app.core.money import ZERO
from app.core.pagination import PageParams, paginate
from app.models.apparel import (
    ApparelPayment,
    ApparelProduct,
    ApparelReturn,
    ApparelSale,
    ApparelSaleLine,
    ApparelVariant,
)
from app.models.customers import Customer
from app.models.floor import Branch
from app.modules.apparel.sales import SALE_LOADERS
from app.modules.business.service import bill_name, load_with_logo
from app.modules.retailkit import barcode as bc
from app.modules.retailkit.receipt import money
from app.schemas.apparel import ApparelLabelsRequest

MAX_LABELS = 1000


def _d(value: object) -> Decimal:
    return Decimal(value or 0)  # type: ignore[arg-type]


# --- barcode labels ----------------------------------------------------------------------------------------------------------


async def labels_page(db: AsyncSession, user: CurrentUser, payload: ApparelLabelsRequest) -> str:
    wanted: Dict[uuid.UUID, int] = {}
    for item in payload.items:
        wanted[item.variant_id] = wanted.get(item.variant_id, 0) + item.copies
    if sum(wanted.values()) > MAX_LABELS:
        raise ValidationFailed(f"At most {MAX_LABELS} labels in one sheet", code="too_many_labels")
    rows = (await db.execute(
        select(ApparelVariant, ApparelProduct).join(ApparelProduct, ApparelProduct.id == ApparelVariant.product_id)
        .where(ApparelVariant.id.in_(wanted), ApparelVariant.organization_id == user.organization_id)
    )).tuples().all()
    found = {v.id: (v, p) for v, p in rows}
    missing = set(wanted) - set(found)
    if missing:
        raise NotFound("Variant not found", code="variant_not_found", details={"variant_ids": sorted(map(str, missing))})
    blank = sorted(str(i) for i, (v, _) in found.items() if not v.barcode)
    if blank:
        raise ValidationFailed("These variants have no barcode yet", code="variant_without_barcode", details={"variant_ids": blank})

    org = await load_with_logo(db, user.organization_id)
    shop = html.escape(bill_name(org))
    cells: List[str] = []
    for item in payload.items:
        variant, product = found[item.variant_id]
        code = variant.barcode or ""
        svg = bc.ean13_svg(code) if bc.is_ean13(code) else bc.code128_svg(code)
        if variant.mrp is not None and variant.mrp > variant.price:
            price = f'<span class="mrp">MRP {money(variant.mrp)}</span> <b>{money(variant.price)}</b>'
        else:
            price = f"<b>{money(variant.price)}</b>" if variant.mrp is None else f"MRP <b>{money(variant.mrp)}</b>"
        cell = (f'<div class="label"><div class="shop">{shop}</div><div class="name">{html.escape(product.name)}</div>'
                f'<div class="meta">{html.escape(variant.size)} / {html.escape(variant.colour)}</div>'
                f'<div class="price">{price}</div>{svg}</div>')
        cells.extend([cell] * item.copies)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Labels</title>
<style>
@page {{ size: A4; margin: 8mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; color: #000; }}
.sheet {{ display: grid; grid-template-columns: repeat({payload.columns}, 1fr); gap: 2mm; }}
.label {{ height: {payload.label_height_mm}mm; padding: 1.5mm 2mm; border: 0.2mm dashed #bbb; overflow: hidden; break-inside: avoid;
  display: flex; flex-direction: column; justify-content: space-between; }}
.shop {{ font-size: 7pt; text-transform: uppercase; letter-spacing: 0.3pt; }}
.name {{ font-size: 9pt; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.meta {{ font-size: 8pt; }}
.price {{ font-size: 9pt; }} .mrp {{ text-decoration: line-through; }}
.barcode {{ width: 100%; height: auto; max-height: 40%; }}
</style></head><body><div class="sheet">{"".join(cells)}</div></body></html>"""


# --- the day report ----------------------------------------------------------------------------------------------------------


async def day_report(db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID], on_date: Optional[date]) -> Dict[str, object]:
    branch_id = resolve_branch_id(user, branch_id)
    branch = await get_or_404(db, Branch, branch_id)
    day = on_date or business_date(branch.timezone)
    paid = (ApparelSale.branch_id == branch_id, ApparelSale.business_date == day, ApparelSale.status == BillStatus.PAID.value)

    s = (await db.execute(select(
        func.count(), func.coalesce(func.sum(ApparelSale.subtotal), 0), func.coalesce(func.sum(ApparelSale.discount_amount), 0),
        func.coalesce(func.sum(ApparelSale.taxable_value), 0), func.coalesce(func.sum(ApparelSale.tax_cgst), 0),
        func.coalesce(func.sum(ApparelSale.tax_sgst), 0), func.coalesce(func.sum(ApparelSale.tax_igst), 0),
        func.coalesce(func.sum(ApparelSale.round_off), 0), func.coalesce(func.sum(ApparelSale.grand_total), 0),
    ).where(*paid))).one()
    units = _d(await db.scalar(select(func.coalesce(func.sum(ApparelSaleLine.quantity), 0)).join(
        ApparelSale, ApparelSale.id == ApparelSaleLine.sale_id).where(*paid)))

    tax_rows = (await db.execute(
        select(ApparelSaleLine.tax_rate, func.sum(ApparelSaleLine.taxable_value),
               func.sum(ApparelSaleLine.cgst + ApparelSaleLine.sgst + ApparelSaleLine.igst))
        .join(ApparelSale, ApparelSale.id == ApparelSaleLine.sale_id).where(*paid)
        .group_by(ApparelSaleLine.tax_rate).order_by(ApparelSaleLine.tax_rate))).all()

    paid_on = func.date(func.timezone(branch.timezone, ApparelPayment.created_at))
    pay_rows = (await db.execute(
        select(ApparelPayment.payment_mode, func.count(), func.sum(ApparelPayment.amount))
        .where(ApparelPayment.branch_id == branch_id, ApparelPayment.status == PaymentStatus.SUCCESS.value, paid_on == day)
        .group_by(ApparelPayment.payment_mode).order_by(ApparelPayment.payment_mode))).all()

    ret_rows = (await db.execute(
        select(ApparelReturn.refund_mode, func.count(), func.sum(ApparelReturn.total_amount))
        .where(ApparelReturn.branch_id == branch_id, ApparelReturn.business_date == day)
        .group_by(ApparelReturn.refund_mode))).all()
    returned = {mode: (n, _d(total)) for mode, n, total in ret_rows}
    cash_in = next((_d(total) for mode, _, total in pay_rows if mode == "cash"), ZERO)
    cash_back = returned.get("cash", (0, ZERO))[1]

    top = (await db.execute(
        select(ApparelSaleLine.name_snapshot, func.sum(ApparelSaleLine.quantity), func.sum(ApparelSaleLine.line_total))
        .join(ApparelSale, ApparelSale.id == ApparelSaleLine.sale_id).where(*paid)
        .group_by(ApparelSaleLine.name_snapshot)
        .order_by(func.sum(ApparelSaleLine.line_total).desc(), ApparelSaleLine.name_snapshot).limit(10))).all()
    sizes = (await db.execute(
        select(ApparelSaleLine.size_snapshot, func.sum(ApparelSaleLine.quantity))
        .join(ApparelSale, ApparelSale.id == ApparelSaleLine.sale_id).where(*paid)
        .group_by(ApparelSaleLine.size_snapshot).order_by(func.sum(ApparelSaleLine.quantity).desc(), ApparelSaleLine.size_snapshot))).all()

    return {
        "branch_id": branch_id, "date": day,
        "sales": {"count": s[0], "units": int(units), "subtotal": _d(s[1]), "discount_amount": _d(s[2]), "taxable_value": _d(s[3]),
                  "tax_cgst": _d(s[4]), "tax_sgst": _d(s[5]), "tax_igst": _d(s[6]), "round_off": _d(s[7]), "grand_total": _d(s[8])},
        "tax_by_rate": [{"rate": _d(rate), "taxable_value": _d(t), "tax": _d(x)} for rate, t, x in tax_rows],
        "payments": [{"mode": m, "count": n, "amount": _d(a)} for m, n, a in pay_rows],
        "returns": {"count": sum(n for n, _ in returned.values()), "total": sum((t for _, t in returned.values()), ZERO),
                    "cash_total": cash_back, "store_credit_total": returned.get("store_credit", (0, ZERO))[1]},
        "net_cash": cash_in - cash_back,
        "top_products": [{"name": n, "units": int(u), "value": _d(v)} for n, u, v in top],
        "units_by_size": [{"size": z, "units": int(u)} for z, u in sizes],
    }


# --- sales by product and size -------------------------------------------------------------------------------------------------


async def sales_by_product(
    db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID], date_from: date, date_to: date,
) -> Dict[str, object]:
    if date_to < date_from:
        raise ValidationFailed("`to` is before `from`", code="invalid_range")
    branch_id = resolve_branch_id(user, branch_id)
    rows = (await db.execute(
        select(ApparelSaleLine.name_snapshot, ApparelSaleLine.size_snapshot, func.sum(ApparelSaleLine.quantity),
               func.sum(ApparelSaleLine.line_total))
        .join(ApparelSale, ApparelSale.id == ApparelSaleLine.sale_id)
        .where(ApparelSale.branch_id == branch_id, ApparelSale.status == BillStatus.PAID.value,
               ApparelSale.business_date >= date_from, ApparelSale.business_date <= date_to)
        .group_by(ApparelSaleLine.name_snapshot, ApparelSaleLine.size_snapshot))).all()
    products: Dict[str, Dict[str, object]] = {}
    for name, size, units, value in rows:
        entry = products.setdefault(name, {"name": name, "units": 0, "value": ZERO, "sizes": []})
        entry["units"] = int(entry["units"]) + int(units)  # type: ignore[call-overload]
        entry["value"] = entry["value"] + _d(value)  # type: ignore[operator]
        entry["sizes"].append({"size": size, "units": int(units), "value": _d(value)})  # type: ignore[union-attr]
    ordered: List[Dict[str, object]] = sorted(products.values(), key=lambda p: (-p["value"], p["name"]))  # type: ignore[operator]
    for entry in ordered:
        entry["sizes"].sort(key=lambda z: (-z["units"], z["size"]))  # type: ignore[union-attr]
    return {"branch_id": branch_id, "date_from": date_from, "date_to": date_to, "products": ordered,
            "units": sum(int(p["units"]) for p in ordered), "value": sum((p["value"] for p in ordered), ZERO)}  # type: ignore[misc]


# --- a customer's purchases ---------------------------------------------------------------------------------------------------


async def customer_sales(db: AsyncSession, user: CurrentUser, customer_id: uuid.UUID, params: PageParams) -> Tuple[List[ApparelSale], int]:
    customer = await db.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == user.organization_id))
    if customer is None:
        raise NotFound("Customer not found", code="customer_not_found")
    stmt = (select(ApparelSale).where(ApparelSale.customer_id == customer_id).options(*SALE_LOADERS)
            .order_by(ApparelSale.created_at.desc(), ApparelSale.id))
    if not user.is_owner:  # everyone else sees their own branch's sales of this customer
        stmt = stmt.where(ApparelSale.branch_id == user.branch_id)
    return await paginate(db, stmt, params)
