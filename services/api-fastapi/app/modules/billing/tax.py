"""
GST tax engine (pure functions).

Discounts are applied before tax (GST is charged on the post-discount taxable value) and
allocated across lines in proportion to their value. Each line is taxed at its own snapshot
rate; total tax is split equally into CGST and SGST (intra-state supply).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Sequence

from app.core.errors import ValidationFailed
from app.core.money import ZERO, q


@dataclass(frozen=True)
class TaxLine:
    line_total: Decimal
    tax_rate: Decimal


@dataclass(frozen=True)
class TaxBreakdown:
    subtotal: Decimal
    discount: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    grand_total: Decimal


def allocate_discount(lines: Sequence[TaxLine], discount: Decimal) -> List[Decimal]:
    """Split `discount` across lines proportionally; the rounding remainder goes to the largest line."""
    subtotal = sum((line.line_total for line in lines), ZERO)
    if discount == ZERO or subtotal == ZERO:
        return [ZERO for _ in lines]
    shares = [q(discount * line.line_total / subtotal) for line in lines]
    remainder = discount - sum(shares, ZERO)
    if remainder:
        largest = max(range(len(lines)), key=lambda i: lines[i].line_total)
        shares[largest] += remainder
    return shares


def compute_bill(lines: Sequence[TaxLine], discount: Decimal) -> TaxBreakdown:
    for value in [discount, *(x for line in lines for x in (line.line_total, line.tax_rate))]:
        if not isinstance(value, Decimal):
            raise TypeError("Tax engine accepts Decimal values only")
    if not lines:
        raise ValidationFailed("Nothing to bill", code="empty_bill")

    subtotal = q(sum((line.line_total for line in lines), ZERO))
    discount = q(discount)
    if discount < ZERO or discount > subtotal:
        raise ValidationFailed("Discount must be between 0 and the subtotal", code="invalid_discount")

    shares = allocate_discount(lines, discount)
    total_tax = sum(
        ((line.line_total - share) * line.tax_rate / Decimal(100) for line, share in zip(lines, shares, strict=True)),
        ZERO,
    )
    total_tax = q(total_tax)
    cgst = q(total_tax / 2)
    sgst = total_tax - cgst
    taxable_value = subtotal - discount
    return TaxBreakdown(
        subtotal=subtotal,
        discount=discount,
        taxable_value=taxable_value,
        cgst=cgst,
        sgst=sgst,
        grand_total=q(taxable_value + cgst + sgst),
    )
