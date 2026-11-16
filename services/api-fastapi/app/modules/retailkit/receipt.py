"""
Printed shop documents (tax invoice, credit note, estimate) for 58 mm and 80 mm thermal paper.

Pure (no database), built on the kitchen-slip print-row helpers like the restaurant bill (`app.modules.billing.receipt`), so the
apps print it with the same code: rows for line printers, plain text, or a printable HTML page. Amounts print without a
currency sign because thermal printers' code pages have no rupee symbol.

Compared with the restaurant bill it adds: a detail line under each item (size and colour, or quantity, unit and rate), an HSN
column, a tax summary by rate, IGST, round-off, "You saved" against MRP, and a document title.
"""

import html
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from app.core.money import ZERO
from app.modules.billing.receipt import money
from app.modules.kots.slip import COLUMNS, PrintRow, clean
from app.modules.kots.slip import _pair as pair
from app.modules.kots.slip import _wrap as wrap


@dataclass(frozen=True)
class RetailLine:
    name: str
    amount: Decimal  # what the line comes to (after its discount, tax included)
    quantity_text: str  # "2", "2.5 m", "1 coil"
    detail: Optional[str] = None  # "M / Blue", "2.5 m x 42.00"; printed under the name
    hsn: Optional[str] = None
    tax_rate: Optional[Decimal] = None
    mrp_total: Optional[Decimal] = None  # MRP x quantity, when there is an MRP: "You saved" counts the difference


@dataclass(frozen=True)
class RetailPayment:
    mode: str
    amount: Decimal
    reference: Optional[str] = None


@dataclass(frozen=True)
class RetailReceipt:
    title: str  # TAX INVOICE | CREDIT NOTE | ESTIMATE
    number: str
    issued_at: datetime  # already in the branch's local time
    branch_name: str
    lines: List[RetailLine]
    subtotal: Decimal
    discount: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    grand_total: Decimal
    business_name: Optional[str] = None
    logo_data_url: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    gstin: Optional[str] = None
    cashier: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_gstin: Optional[str] = None
    discount_reason: Optional[str] = None
    tax_summary: List[Tuple[Decimal, Decimal, Decimal]] = field(default_factory=list)  # (rate, taxable value, tax)
    payments: List[RetailPayment] = field(default_factory=list)
    balance_due: Decimal = ZERO
    reference_lines: List[str] = field(default_factory=list)  # under the number: "Against: BLR1/2627/000012", "Valid until ..."
    footer_lines: List[Tuple[str, str]] = field(default_factory=list)  # label/amount pairs after payments: "On account"
    closing: str = "Thank you. Please visit again."


def tax_summary(lines: List[Tuple[Decimal, Decimal, Decimal]]) -> List[Tuple[Decimal, Decimal, Decimal]]:
    """(rate, taxable, tax) per line -> one row per rate, lowest rate first."""
    by_rate: Dict[Decimal, Tuple[Decimal, Decimal]] = {}
    for rate, taxable, tax in lines:
        t, x = by_rate.get(rate, (ZERO, ZERO))
        by_rate[rate] = (t + taxable, x + tax)
    return [(rate, t, x) for rate, (t, x) in sorted(by_rate.items())]


def _rate_text(rate: Decimal) -> str:
    text = f"{rate:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def _amount_lines(left: str, amount: str, width: int) -> List[str]:
    room = max(width - len(amount) - 1, 8)
    parts = wrap(left, room, rest="    ")
    return [pair(parts[0], amount, width)[0]] + parts[1:]


def rows(receipt: RetailReceipt, paper_mm: int = 80) -> List[PrintRow]:
    width = COLUMNS[paper_mm]
    rule: PrintRow = ("normal", "left", "-" * width)
    out: List[PrintRow] = []

    def left(style: str, texts: List[str]) -> None:
        out.extend((style, "left", t) for t in texts)

    def centre(style: str, text: str) -> None:
        out.extend((style, "center", t) for t in wrap(clean(text), width))

    title = receipt.business_name or receipt.branch_name
    out += [("big", "center", t) for t in wrap(clean(title).upper(), max(width // 2, 8))]
    if receipt.business_name and clean(receipt.branch_name).lower() != clean(receipt.business_name).lower():
        centre("normal", receipt.branch_name)
    if receipt.address:
        centre("normal", receipt.address)
    if receipt.phone:
        centre("normal", f"Ph: {receipt.phone}")
    if receipt.gstin:
        centre("normal", f"GSTIN: {receipt.gstin}")
    out.append(rule)
    out.append(("bold", "center", receipt.title))
    left("normal", pair(f"No: {clean(receipt.number)}", receipt.issued_at.strftime("%d %b %Y %H:%M"), width))
    for ref in receipt.reference_lines:
        left("normal", wrap(clean(ref), width))
    if receipt.cashier:
        left("normal", wrap(f"Cashier: {clean(receipt.cashier)}", width))
    if receipt.customer_name or receipt.customer_phone:
        out.append(rule)
        if receipt.customer_name:
            left("normal", wrap(f"Customer: {clean(receipt.customer_name)}", width, rest="    "))
        if receipt.customer_phone:
            left("normal", wrap(f"Ph: {clean(receipt.customer_phone)}", width))
        if receipt.customer_gstin:
            left("normal", wrap(f"GSTIN: {clean(receipt.customer_gstin)}", width))
    out.append(rule)

    saved = ZERO
    for line in receipt.lines:
        left("normal", _amount_lines(f"{clean(line.quantity_text)} x {clean(line.name)}", money(line.amount), width))
        detail = [clean(line.detail)] if line.detail else []
        if line.hsn:
            detail.append(f"HSN {clean(line.hsn)}")
        if line.tax_rate is not None:
            detail.append(f"GST {_rate_text(line.tax_rate)}")
        if line.mrp_total is not None and line.mrp_total > line.amount:
            detail.append(f"MRP {money(line.mrp_total)}")
            saved += line.mrp_total - line.amount
        if detail:
            left("normal", wrap("    " + "  ".join(detail), width, rest="    "))
    out.append(rule)

    left("normal", pair("Subtotal", money(receipt.subtotal), width))
    if receipt.discount > 0:
        label = f"Discount ({clean(receipt.discount_reason)})" if receipt.discount_reason else "Discount"
        left("normal", _amount_lines(label, "-" + money(receipt.discount), width))
    left("normal", pair("Taxable value", money(receipt.taxable_value), width))
    if receipt.igst > 0:
        left("normal", pair("IGST", money(receipt.igst), width))
    else:
        left("normal", pair("CGST", money(receipt.cgst), width))
        left("normal", pair("SGST", money(receipt.sgst), width))
    if receipt.round_off != 0:
        sign = "-" if receipt.round_off < 0 else ""
        left("normal", pair("Round off", sign + money(abs(receipt.round_off)), width))
    left("bold", pair("TOTAL", money(receipt.grand_total), width))

    if len(receipt.tax_summary) > 0:
        out.append(rule)
        left("normal", pair("GST rate  Taxable", "Tax", width))
        for rate, taxable, tax in receipt.tax_summary:
            left("normal", pair(f"{_rate_text(rate):<8}  {money(taxable)}", money(tax), width))

    if receipt.payments:
        out.append(rule)
        for p in receipt.payments:
            label = f"Paid {p.mode.replace('_', ' ').upper()}" + (f" {clean(p.reference)}" if p.reference else "")
            left("normal", _amount_lines(label, money(p.amount), width))
    for label, amount in receipt.footer_lines:
        left("normal", _amount_lines(clean(label), amount, width))
    if receipt.balance_due > 0:
        left("bold", pair("Balance due", money(receipt.balance_due), width))
    if saved > 0:
        out.append(rule)
        centre("bold", f"You saved {money(saved)}")
    out.append(rule)
    if receipt.closing:
        centre("normal", receipt.closing)
    return out


def render_text(receipt: RetailReceipt, paper_mm: int = 80) -> str:
    width = COLUMNS[paper_mm]
    return "\n".join(text.center(width) if align == "center" else text for _, align, text in rows(receipt, paper_mm)) + "\n"


def render_html(receipt: RetailReceipt, paper_mm: int = 80) -> str:
    esc = html.escape
    body = "\n".join(f'<div class="{style} {align}">{esc(text)}</div>' for style, align, text in rows(receipt, paper_mm))
    if receipt.logo_data_url and receipt.logo_data_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
        body = f'<img class="logo" src="{esc(receipt.logo_data_url)}" alt="">\n' + body
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(receipt.number)}</title>
<style>
@page {{ size: {paper_mm}mm auto; margin: 2mm; }}
html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
body {{ width: {paper_mm - 4}mm; font: 12px/1.35 'Courier New', Courier, monospace; }}
div {{ white-space: pre-wrap; word-break: break-word; }}
.center {{ text-align: center; }}
.bold {{ font-weight: 700; }}
.big {{ font-weight: 700; font-size: 20px; }}
.logo {{ display: block; margin: 0 auto 2mm; max-width: 60%; max-height: 30mm; filter: grayscale(1); }}
</style></head><body>
{body}
</body></html>
"""
