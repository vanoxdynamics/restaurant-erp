"""
Quantities and prices of a hardware line (pure functions). Plan: docs/retail-modules/02-hardware.md sections 3.1 and 3.2.

A line names a product, a unit (its base unit or one of its alternate units) and a quantity. Stock moves in base units:
`base_quantity = quantity x factor`, rounded to 3 decimals. Every price here is per unit sold and rounded to paise (half up)
once it has been multiplied by the factor, e.g. 42.00/m x 0.3048 = 12.80 per ft.

    retail(u)    = unit.price, else q(product.price x factor)
    wholesale(u) = unit.wholesale_price, else min(q(product.wholesale_price x factor), retail(u)), else retail(u)
    floor(u, t)  = unit.min_price, else min(q(product.min_price x factor), price(u, t)), else none
    ceiling(u)   = q(product.mrp x factor), tax included
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.core.enums import HardwarePriceTier
from app.core.errors import ValidationFailed
from app.core.money import ZERO, q

ONE = Decimal(1)
MILLI = Decimal("0.001")


@dataclass(frozen=True)
class ProductPrices:
    """A product's own prices, per base unit."""
    price: Decimal
    wholesale_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    mrp: Optional[Decimal] = None


@dataclass(frozen=True)
class UnitPrices:
    """An alternate unit: how many base units it holds, and its own prices per this unit (null: worked out)."""
    factor: Decimal
    price: Optional[Decimal] = None
    wholesale_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None


@dataclass(frozen=True)
class LinePrice:
    """The price of one unit sold at a tier, the lowest a cashier may go alone (none: no floor) and the MRP ceiling."""
    price: Decimal
    floor: Optional[Decimal]
    ceiling: Optional[Decimal]


def base_quantity(quantity: Decimal, factor: Decimal, *, allow_fraction: bool) -> Decimal:
    """Base units in `quantity` of a unit holding `factor` of them. Half a box of 100 screws is fine; half a screw is not."""
    if not isinstance(quantity, Decimal) or not isinstance(factor, Decimal):
        raise TypeError("Quantities are Decimal")
    if quantity <= ZERO or factor <= ZERO:
        raise ValidationFailed("Quantity must be above zero", code="invalid_quantity")
    base = (quantity * factor).quantize(MILLI, rounding=ROUND_HALF_UP)
    if base <= ZERO:
        raise ValidationFailed("Quantity is too small", code="invalid_quantity")
    if not allow_fraction and base != base.to_integral_value():
        raise ValidationFailed("This product is sold in whole pieces only", code="whole_quantity_required",
                               details={"base_quantity": str(base)})
    return base


def retail_price(product: ProductPrices, unit: Optional[UnitPrices]) -> Decimal:
    if unit is None:
        return q(product.price)
    return unit.price if unit.price is not None else q(product.price * unit.factor)


def wholesale_price(product: ProductPrices, unit: Optional[UnitPrices]) -> Decimal:
    retail = retail_price(product, unit)
    if unit is not None and unit.wholesale_price is not None:
        return unit.wholesale_price
    if product.wholesale_price is not None:
        # A coil priced at 3,500.00 must not cost a wholesale buyer 40.00 x 90 = 3,600.00.
        return min(q(product.wholesale_price * (unit.factor if unit else ONE)), retail)
    return retail


def tier_price(product: ProductPrices, unit: Optional[UnitPrices], tier: HardwarePriceTier) -> Decimal:
    return wholesale_price(product, unit) if tier == HardwarePriceTier.WHOLESALE else retail_price(product, unit)


def floor_price(product: ProductPrices, unit: Optional[UnitPrices], tier: HardwarePriceTier) -> Optional[Decimal]:
    """Selling a unit at its own listed price (or a wholesale buyer at the wholesale price) never needs a PIN."""
    if unit is not None and unit.min_price is not None:
        return unit.min_price
    if product.min_price is not None:
        return min(q(product.min_price * (unit.factor if unit else ONE)), tier_price(product, unit, tier))
    return None


def ceiling_price(product: ProductPrices, unit: Optional[UnitPrices]) -> Optional[Decimal]:
    if product.mrp is None:
        return None
    return q(product.mrp * (unit.factor if unit else ONE))


def line_price(product: ProductPrices, unit: Optional[UnitPrices], tier: HardwarePriceTier) -> LinePrice:
    return LinePrice(price=tier_price(product, unit, tier), floor=floor_price(product, unit, tier),
                     ceiling=ceiling_price(product, unit))
