"""
The GST rate of a dress shop sale line (pure functions). Plan: docs/retail-modules/01-apparel.md section 3.1.

Garments are taxed by the value of the piece: at or below the slab the low rate applies, above it the high rate. The value is
the price per piece after every discount (the line's own and its share of the bill discount), with the tax taken out at the
low rate when prices include tax. The unrounded value is compared. Belts, bags and the like (`tax_mode = fixed`) have their own
rate.

The bill discount is spread over the lines the same way `retailkit.tax.compute_invoice` spreads it (in proportion to each
line's net), so the rates chosen here are the ones the invoice is then computed with.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from app.core.enums import ApparelTaxMode
from app.core.errors import ValidationFailed
from app.core.money import ZERO, q
from app.modules.retailkit.tax import LineIn, allocate

HUNDRED = Decimal(100)


@dataclass(frozen=True)
class SlabRule:
    low_rate: Decimal
    slab_price: Decimal
    high_rate: Decimal
    inclusive: bool  # prices include tax


@dataclass(frozen=True)
class SaleLineIn:
    quantity: int
    unit_price: Decimal
    line_discount: Decimal
    tax_mode: str  # ApparelTaxMode value
    fixed_rate: Optional[Decimal] = None

    @property
    def gross(self) -> Decimal:
        return q(self.unit_price * self.quantity)


def piece_value(net_per_piece: Decimal, rule: SlabRule) -> Decimal:
    """What a piece is worth for the slab test: its price after discounts, without the tax at the low rate."""
    if rule.inclusive:
        return net_per_piece * HUNDRED / (HUNDRED + rule.low_rate)
    return net_per_piece


def slab_rate(net_per_piece: Decimal, rule: SlabRule) -> Decimal:
    return rule.low_rate if piece_value(net_per_piece, rule) <= rule.slab_price else rule.high_rate


def line_rates(lines: Sequence[SaleLineIn], bill_discount: Decimal, rule: SlabRule) -> List[Decimal]:
    """The GST rate of every line, after spreading the bill discount over the lines."""
    if not lines:
        raise ValidationFailed("Nothing to sell", code="empty_invoice")
    nets = [line.gross - q(line.line_discount) for line in lines]
    for line, net in zip(lines, nets, strict=True):
        if line.quantity <= 0:
            raise ValidationFailed("Quantities are whole pieces above zero", code="invalid_quantity")
        if net < ZERO:
            raise ValidationFailed("A line discount must be between 0 and the line amount", code="invalid_discount")
    if q(bill_discount) < ZERO or q(bill_discount) > sum(nets, ZERO):
        raise ValidationFailed("The bill discount must be between 0 and what the lines come to", code="invalid_discount")
    shares = allocate(q(bill_discount), nets)
    rates = []
    for line, net, share in zip(lines, nets, shares, strict=True):
        if line.tax_mode == ApparelTaxMode.FIXED.value:
            if line.fixed_rate is None:
                raise ValidationFailed("A fixed-rate product has no GST rate", code="tax_rate_required")
            rates.append(line.fixed_rate)
        else:
            rates.append(slab_rate((net - share) / line.quantity, rule))
    return rates


def invoice_lines(lines: Sequence[SaleLineIn], bill_discount: Decimal, rule: SlabRule) -> List[LineIn]:
    """The lines as `retailkit.tax.compute_invoice` takes them, each at the rate chosen above."""
    rates = line_rates(lines, bill_discount, rule)
    return [LineIn(gross=line.gross, line_discount=q(line.line_discount), tax_rate=rate)
            for line, rate in zip(lines, rates, strict=True)]
