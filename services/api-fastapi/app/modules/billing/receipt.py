"""
The customer bill printed at the counter: a rendering of a bill, like a kitchen slip is a rendering of a KOT.

Pure (no database). It produces print rows for line printers (the Pine Labs device), plain text, and a printable HTML page, for
58 mm or 80 mm paper. Names typed by staff are cleaned the same way as on kitchen slips. Amounts print without a currency sign
because thermal printers' code pages have no rupee symbol.
"""

import html
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from app.modules.kots.slip import COLUMNS, PrintRow, clean
from app.modules.kots.slip import _pair as pair
from app.modules.kots.slip import _wrap as wrap


@dataclass(frozen=True)
class ReceiptLine:
    quantity: int
    name: str
    amount: Decimal


@dataclass(frozen=True)
class ReceiptPayment:
    mode: str  # cash | card | upi
    amount: Decimal
    reference: Optional[str] = None


@dataclass(frozen=True)
class Receipt:
    branch_name: str
    branch_code: str
    address: Optional[str]
    phone: Optional[str]
    gstin: Optional[str]
    bill_number: str
    order_number: str
    order_type: str  # dine_in | takeaway | delivery
    table_number: Optional[str]
    guest_count: int
    billed_at: datetime  # already in the branch's local time
    cashier: Optional[str]
    lines: List[ReceiptLine]
    subtotal: Decimal
    discount: Decimal
    discount_reason: Optional[str]
    cgst: Decimal
    sgst: Decimal
    grand_total: Decimal
    payments: List[ReceiptPayment] = field(default_factory=list)
    balance_due: Decimal = Decimal("0.00")


def money(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _amount_lines(left: str, amount: str, width: int) -> List[str]:
    """`left` wrapped into the space before `amount`, with the amount on the first line."""
    room = max(width - len(amount) - 1, 8)
    parts = wrap(left, room, rest="    ")
    return [pair(parts[0], amount, width)[0]] + parts[1:]


def receipt_rows(receipt: Receipt, paper_mm: int = 80) -> List[PrintRow]:
    width = COLUMNS[paper_mm]
    rule: PrintRow = ("normal", "left", "-" * width)
    centre = lambda style, text: [(style, "center", t) for t in wrap(clean(text), width)]  # noqa: E731
    rows: List[PrintRow] = []

    rows += [("big", "center", t) for t in wrap(clean(receipt.branch_name).upper(), max(width // 2, 8))]
    if receipt.address:
        rows += centre("normal", receipt.address)
    if receipt.phone:
        rows += centre("normal", f"Ph: {receipt.phone}")
    if receipt.gstin:
        rows += centre("normal", f"GSTIN: {receipt.gstin}")
    rows.append(rule)
    rows.append(("bold", "center", "TAX INVOICE" if receipt.gstin else "BILL"))
    rows += [("normal", "left", t) for t in pair(f"Bill: {clean(receipt.bill_number)}", receipt.billed_at.strftime("%d %b %Y %H:%M"), width)]
    where = f"Table: {clean(receipt.table_number)}" if receipt.table_number else receipt.order_type.replace("_", " ").upper()
    rows += [("normal", "left", t) for t in pair(where, f"Guests: {receipt.guest_count}" if receipt.table_number else "", width)]
    rows += [("normal", "left", t) for t in wrap(f"Order: {clean(receipt.order_number)}", width)]
    if receipt.cashier:
        rows += [("normal", "left", t) for t in wrap(f"Cashier: {clean(receipt.cashier)}", width)]
    rows.append(rule)

    for line in receipt.lines:
        rows += [("normal", "left", t) for t in _amount_lines(f"{line.quantity} x {clean(line.name)}", money(line.amount), width)]
    rows.append(rule)

    rows += [("normal", "left", t) for t in pair("Subtotal", money(receipt.subtotal), width)]
    if receipt.discount > 0:
        label = f"Discount ({clean(receipt.discount_reason)})" if receipt.discount_reason else "Discount"
        rows += [("normal", "left", t) for t in _amount_lines(label, "-" + money(receipt.discount), width)]
    rows += [("normal", "left", t) for t in pair("CGST", money(receipt.cgst), width)]
    rows += [("normal", "left", t) for t in pair("SGST", money(receipt.sgst), width)]
    rows += [("bold", "left", t) for t in pair("TOTAL", money(receipt.grand_total), width)]

    if receipt.payments:
        rows.append(rule)
        for p in receipt.payments:
            label = f"Paid {p.mode.upper()}" + (f" {clean(p.reference)}" if p.reference else "")
            rows += [("normal", "left", t) for t in _amount_lines(label, money(p.amount), width)]
    if receipt.balance_due > 0:
        rows += [("bold", "left", t) for t in pair("Balance due", money(receipt.balance_due), width)]
    rows.append(rule)
    rows.append(("normal", "center", "Thank you. Please visit again."))
    return rows


def render_text(receipt: Receipt, paper_mm: int = 80) -> str:
    width = COLUMNS[paper_mm]
    return "\n".join(text.center(width) if align == "center" else text
                     for _, align, text in receipt_rows(receipt, paper_mm)) + "\n"


def render_html(receipt: Receipt, paper_mm: int = 80) -> str:
    esc = html.escape
    body = "\n".join(f'<div class="{style} {align}">{esc(text)}</div>' for style, align, text in receipt_rows(receipt, paper_mm))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(receipt.bill_number)}</title>
<style>
@page {{ size: {paper_mm}mm auto; margin: 2mm; }}
html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
body {{ width: {paper_mm - 4}mm; font: 12px/1.35 'Courier New', Courier, monospace; }}
div {{ white-space: pre-wrap; word-break: break-word; }}
.center {{ text-align: center; }}
.bold {{ font-weight: 700; }}
.big {{ font-weight: 700; font-size: 20px; }}
</style></head><body>
{body}
</body></html>
"""
