"""
GST for a shop invoice (pure functions).

Each line is taxed on its own and the invoice totals are the sums of the lines, so the values stored on sale lines always add
up to the invoice. Prices may include tax (the usual shop price tag) or not. A bill-level discount is spread over the lines in
proportion to what each line is worth after its own discount, before tax is worked out.

    inclusive:  taxable = q(net * 100 / (100 + rate)),  tax = net - taxable
    exclusive:  taxable = net,                           tax = q(taxable * rate / 100)
    intra-state: cgst = q(tax / 2), sgst = tax - cgst;  interstate: igst = tax
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Sequence

from app.core.errors import ValidationFailed
from app.core.money import ZERO, q

HUNDRED = Decimal(100)
RUPEE = Decimal(1)


@dataclass(frozen=True)
class LineIn:
    gross: Decimal  # quantity x unit price, before any discount, as entered (tax-inclusive or not); rounded to paise
    line_discount: Decimal  # discount on this line only
    tax_rate: Decimal  # percent


@dataclass(frozen=True)
class LineOut:
    discount: Decimal  # line discount + this line's share of the bill discount
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    total: Decimal  # taxable_value + taxes


@dataclass(frozen=True)
class InvoiceOut:
    lines: List[LineOut]
    subtotal: Decimal  # sum of gross
    discount: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal  # grand_total minus the sum of line totals; may be negative
    grand_total: Decimal


def _require_decimal(*values: object) -> None:
    for value in values:
        if not isinstance(value, Decimal):
            raise TypeError(f"Tax maths accepts Decimal values only, got {type(value).__name__}")


def allocate(amount: Decimal, weights: Sequence[Decimal]) -> List[Decimal]:
    """
    Split `amount` across `weights` in proportion, each share rounded to paise; the rounding remainder goes to the largest
    weight (the first of equals), so the shares always add up to `amount` exactly.
    """
    _require_decimal(amount, *weights)
    total = sum(weights, ZERO)
    if amount == ZERO or total == ZERO:
        return [ZERO for _ in weights]
    shares = [q(amount * w / total) for w in weights]
    remainder = amount - sum(shares, ZERO)
    if remainder:
        largest = max(range(len(weights)), key=lambda i: weights[i])
        shares[largest] += remainder
    return shares


def _line_tax(net: Decimal, rate: Decimal, inclusive: bool) -> tuple:
    if inclusive:
        taxable = q(net * HUNDRED / (HUNDRED + rate))
        return taxable, net - taxable
    return net, q(net * rate / HUNDRED)


def compute_invoice(
    lines: Sequence[LineIn], bill_discount: Decimal, *, inclusive: bool, interstate: bool, round_to_rupee: bool,
) -> InvoiceOut:
    if not lines:
        raise ValidationFailed("Nothing to bill", code="empty_invoice")
    _require_decimal(bill_discount, *(v for line in lines for v in (line.gross, line.line_discount, line.tax_rate)))

    grosses = [q(line.gross) for line in lines]
    line_discounts = [q(line.line_discount) for line in lines]
    for line, gross, line_discount in zip(lines, grosses, line_discounts, strict=True):
        if gross < ZERO:
            raise ValidationFailed("A line cannot be worth less than nothing", code="invalid_amount")
        if line.tax_rate < ZERO or line.tax_rate > HUNDRED:
            raise ValidationFailed("Tax rate must be between 0 and 100", code="invalid_tax_rate")
        if line_discount < ZERO or line_discount > gross:
            raise ValidationFailed("A line discount must be between 0 and the line amount", code="invalid_discount")

    nets = [g - d for g, d in zip(grosses, line_discounts, strict=True)]
    bill_discount = q(bill_discount)
    if bill_discount < ZERO or bill_discount > sum(nets, ZERO):
        raise ValidationFailed("The bill discount must be between 0 and what the lines come to", code="invalid_discount")
    shares = allocate(bill_discount, nets)

    out: List[LineOut] = []
    for line, net, line_discount, share in zip(lines, nets, line_discounts, shares, strict=True):
        after = net - share
        taxable, tax = _line_tax(after, line.tax_rate, inclusive)
        if interstate:
            cgst, sgst, igst = ZERO, ZERO, tax
        else:
            cgst = q(tax / 2)
            cgst, sgst, igst = cgst, tax - cgst, ZERO
        out.append(LineOut(discount=line_discount + share, taxable_value=taxable, cgst=cgst, sgst=sgst, igst=igst,
                           total=taxable + cgst + sgst + igst))

    exact = sum((line.total for line in out), ZERO)
    grand_total = exact.quantize(RUPEE, rounding=ROUND_HALF_UP).quantize(Decimal("0.01")) if round_to_rupee else exact
    return InvoiceOut(
        lines=out,
        subtotal=sum(grosses, ZERO),
        discount=sum((line.discount for line in out), ZERO),
        taxable_value=sum((line.taxable_value for line in out), ZERO),
        cgst=sum((line.cgst for line in out), ZERO),
        sgst=sum((line.sgst for line in out), ZERO),
        igst=sum((line.igst for line in out), ZERO),
        round_off=grand_total - exact,
        grand_total=grand_total,
    )
