"""Hardware shops: quantities in base units and the price of a line per unit sold (docs/retail-modules/02-hardware.md 3.1, 3.2)."""

from decimal import Decimal

import pytest

from app.core.enums import HardwarePriceTier
from app.core.errors import ValidationFailed
from app.modules.hardware import pricing
from app.modules.hardware.pricing import ProductPrices, UnitPrices

D = Decimal
RETAIL, WHOLESALE = HardwarePriceTier.RETAIL, HardwarePriceTier.WHOLESALE
CABLE = ProductPrices(price=D("42.0000"))  # per metre
COIL = UnitPrices(factor=D("90"), price=D("3500.00"))
SCREWS = ProductPrices(price=D("1.5000"))  # per piece
BOX = UnitPrices(factor=D("100"))


# --- quantities --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("quantity,factor,fraction,base", [
    ("2.5", "1", True, "2.500"),  # 2.5 m of cable
    ("1", "90", True, "90.000"),  # one coil
    ("0.5", "100", False, "50.000"),  # half a box of 100 screws
    ("3", "0.3048", True, "0.914"),  # 3 ft in metres, rounded to 3 decimals
    ("2", "12", False, "24.000"),  # two dozen
])
def test_base_quantity(quantity, factor, fraction, base):
    assert pricing.base_quantity(D(quantity), D(factor), allow_fraction=fraction) == D(base)


def test_half_a_screw_is_refused():
    with pytest.raises(ValidationFailed) as exc:
        pricing.base_quantity(D("0.005"), D("100"), allow_fraction=False)
    assert exc.value.code == "whole_quantity_required"
    with pytest.raises(ValidationFailed) as exc:
        pricing.base_quantity(D("1.5"), D("1"), allow_fraction=False)
    assert exc.value.code == "whole_quantity_required"


@pytest.mark.parametrize("quantity", ["0", "-1", "0.0001"])
def test_nothing_or_less_is_refused(quantity):
    with pytest.raises(ValidationFailed) as exc:
        pricing.base_quantity(D(quantity), D("1"), allow_fraction=True)
    assert exc.value.code == "invalid_quantity"


def test_quantities_must_be_decimal():
    with pytest.raises(TypeError):
        pricing.base_quantity(2.5, D("1"), allow_fraction=True)  # type: ignore[arg-type]


# --- retail and wholesale ----------------------------------------------------------------------------------------------------


def test_base_unit_is_the_product_price_to_paise():
    assert pricing.retail_price(CABLE, None) == D("42.00")
    assert pricing.retail_price(ProductPrices(price=D("0.0450")), None) == D("0.05")  # one screw of a box of 1,000 at 45.00


def test_unit_price_set_is_used_as_it_is():
    assert pricing.retail_price(CABLE, COIL) == D("3500.00")


def test_unit_price_missing_is_base_price_times_factor_rounded_half_up():
    assert pricing.retail_price(SCREWS, BOX) == D("150.00")
    assert pricing.retail_price(CABLE, UnitPrices(factor=D("0.3048"))) == D("12.80")  # 12.8016
    assert pricing.retail_price(ProductPrices(price=D("0.0450")), UnitPrices(factor=D("1000"))) == D("45.00")
    assert pricing.retail_price(ProductPrices(price=D("0.0125")), UnitPrices(factor=D("2"))) == D("0.03")  # 0.025 rounds up


def test_wholesale_set_on_the_unit_wins():
    product = ProductPrices(price=D("42"), wholesale_price=D("38"))
    assert pricing.wholesale_price(product, UnitPrices(factor=D("90"), price=D("3500"), wholesale_price=D("3200"))) == D("3200")


def test_wholesale_missing_on_the_unit_is_product_wholesale_times_factor_but_never_above_retail():
    product = ProductPrices(price=D("42"), wholesale_price=D("40"))
    assert pricing.wholesale_price(product, COIL) == D("3500.00")  # 40 x 90 = 3,600 would be above the coil's 3,500
    assert pricing.wholesale_price(product, UnitPrices(factor=D("90"), price=D("3700"))) == D("3600.00")
    assert pricing.wholesale_price(product, None) == D("40.00")


def test_wholesale_missing_everywhere_is_retail():
    assert pricing.wholesale_price(CABLE, None) == D("42.00")
    assert pricing.wholesale_price(CABLE, COIL) == D("3500.00")
    assert pricing.wholesale_price(SCREWS, BOX) == D("150.00")


def test_tier_price():
    product = ProductPrices(price=D("42"), wholesale_price=D("38"))
    assert pricing.tier_price(product, None, RETAIL) == D("42.00")
    assert pricing.tier_price(product, None, WHOLESALE) == D("38.00")


# --- floor and ceiling ---------------------------------------------------------------------------------------------------------


def test_unit_min_price_set_is_the_floor():
    unit = UnitPrices(factor=D("90"), price=D("3500"), min_price=D("3300"))
    assert pricing.floor_price(ProductPrices(price=D("42"), min_price=D("40")), unit, RETAIL) == D("3300")


def test_derived_floor_is_capped_at_the_resolved_price():
    product = ProductPrices(price=D("42"), wholesale_price=D("37"), min_price=D("40"))
    # 40 x 90 = 3,600 is above the coil's own price: selling a coil at 3,500 needs no PIN.
    assert pricing.floor_price(product, COIL, RETAIL) == D("3500.00")
    # A wholesale buyer at the wholesale price needs no PIN either.
    assert pricing.floor_price(product, None, WHOLESALE) == D("37.00")
    assert pricing.floor_price(product, None, RETAIL) == D("40.00")


def test_no_min_price_means_no_floor():
    assert pricing.floor_price(CABLE, COIL, RETAIL) is None


def test_ceiling_is_mrp_times_factor():
    product = ProductPrices(price=D("42"), mrp=D("45.5"))
    assert pricing.ceiling_price(product, None) == D("45.50")
    assert pricing.ceiling_price(product, COIL) == D("4095.00")
    assert pricing.ceiling_price(CABLE, COIL) is None


def test_line_price():
    product = ProductPrices(price=D("1.5"), wholesale_price=D("1.2"), min_price=D("1.1"), mrp=D("2"))
    assert pricing.line_price(product, BOX, WHOLESALE) == pricing.LinePrice(price=D("120.00"), floor=D("110.00"),
                                                                            ceiling=D("200.00"))
