"""Dress shop GST: the rate of a line follows the value of the piece (plan 01-apparel.md section 3.1, worked examples)."""

from decimal import Decimal as D

import pytest

from app.core.errors import ValidationFailed
from app.modules.apparel.tax import SaleLineIn, SlabRule, invoice_lines, line_rates, piece_value
from app.modules.retailkit.tax import compute_invoice

DEFAULT = SlabRule(low_rate=D("5.00"), slab_price=D("2500.00"), high_rate=D("18.00"), inclusive=True)


def sale(*lines, bill_discount="0", rule=DEFAULT):
    """(quantity, unit price, line discount[, fixed rate]) tuples -> the computed invoice, as the sale service will do it."""
    ins = [SaleLineIn(quantity=n, unit_price=D(p), line_discount=D(d),
                      tax_mode="fixed" if rest else "slab", fixed_rate=D(rest[0]) if rest else None)
           for n, p, d, *rest in lines]
    return compute_invoice(invoice_lines(ins, D(bill_discount), rule), D(bill_discount), inclusive=rule.inclusive,
                           interstate=False, round_to_rupee=False)


# The plan's table, row by row: (quantity, unit price, line discount) -> rate, taxable, CGST, SGST.
WORKED = [
    ("Shirt at 999.00", (1, "999.00", "0"), "5.00", "951.43", "23.79", "23.78"),
    ("Kurta at 2,999.00", (1, "2999.00", "0"), "18.00", "2541.53", "228.74", "228.73"),
    ("Kurta at 2,999.00 with a 15% line discount (net 2,549.15)", (1, "2999.00", "449.85"), "5.00", "2427.76", "60.70", "60.69"),
    ("Piece at exactly 2,625.00", (1, "2625.00", "0"), "5.00", "2500.00", "62.50", "62.50"),
    ("Piece at 2,625.01", (1, "2625.01", "0"), "18.00", "2224.58", "200.22", "200.21"),
    ("Two shirts at 1,400.00 each on one line", (2, "1400.00", "0"), "5.00", "2666.67", "66.67", "66.66"),
]


@pytest.mark.parametrize("label,line,rate,taxable,cgst,sgst", WORKED, ids=[w[0] for w in WORKED])
def test_worked_examples(label, line, rate, taxable, cgst, sgst):
    out = sale(line)
    assert line_rates([SaleLineIn(line[0], D(line[1]), D(line[2]), "slab")], D("0"), DEFAULT) == [D(rate)]
    got = out.lines[0]
    assert (got.taxable_value, got.cgst, got.sgst, got.igst) == (D(taxable), D(cgst), D(sgst), D("0")), label


def test_the_slab_compares_the_unrounded_value():
    assert piece_value(D("2625.00"), DEFAULT) == D("2500")
    assert piece_value(D("2625.01"), DEFAULT) > D("2500")
    # 3 pieces at 2,625.01 with 0.02 off the line: 2,625.0033 a piece, worth 2,500.0032 -> above the slab (18%), although it
    # would round to 2,500.00 (5%).
    assert piece_value(D("7875.01") / 3, DEFAULT) > D("2500")
    assert line_rates([SaleLineIn(3, D("2625.01"), D("0.02"), "slab")], D("0"), DEFAULT) == [D("18.00")]
    assert line_rates([SaleLineIn(3, D("2625.01"), D("0.03"), "slab")], D("0"), DEFAULT) == [D("5.00")]  # exactly 2,625.00


def test_a_bill_discount_can_move_a_line_below_the_slab():
    # Kurta 2,999 and shirt 999 with 500 off the bill: the kurta's share is 375.06, leaving 2,623.94 a piece, worth 2,498.99
    # without tax -> at or below the slab -> 5%.
    out = sale((1, "2999.00", "0"), (1, "999.00", "0"), bill_discount="500.00")
    assert [line.discount for line in out.lines] == [D("375.06"), D("124.94")]
    assert line_rates([SaleLineIn(1, D("2999.00"), D("0"), "slab"), SaleLineIn(1, D("999.00"), D("0"), "slab")],
                      D("500.00"), DEFAULT) == [D("5.00"), D("5.00")]
    kurta = out.lines[0]
    assert kurta.taxable_value == D("2498.99") and (kurta.cgst, kurta.sgst) == (D("62.48"), D("62.47"))
    # Without the discount the kurta is 18%.
    assert line_rates([SaleLineIn(1, D("2999.00"), D("0"), "slab")], D("0"), DEFAULT) == [D("18.00")]


def test_fixed_rate_products_ignore_the_slab():
    out = sale((1, "4999.00", "0", "12.00"), (1, "299.00", "0", "18.00"))
    assert [line_rates([SaleLineIn(1, D("4999.00"), D("0"), "fixed", D("12.00"))], D("0"), DEFAULT)] == [[D("12.00")]]
    belt = out.lines[1]
    assert belt.taxable_value == D("253.39") and belt.cgst == D("22.81") and belt.sgst == D("22.80")


def test_prices_without_tax():
    rule = SlabRule(low_rate=D("5.00"), slab_price=D("2500.00"), high_rate=D("18.00"), inclusive=False)
    assert line_rates([SaleLineIn(1, D("2500.00"), D("0"), "slab")], D("0"), rule) == [D("5.00")]
    assert line_rates([SaleLineIn(1, D("2500.01"), D("0"), "slab")], D("0"), rule) == [D("18.00")]
    out = sale((1, "1000.00", "0"), rule=rule)
    assert out.lines[0].taxable_value == D("1000.00") and out.grand_total == D("1050.00")


def test_changed_settings_change_the_rates():
    rule = SlabRule(low_rate=D("5.00"), slab_price=D("1000.00"), high_rate=D("12.00"), inclusive=True)
    assert line_rates([SaleLineIn(1, D("1050.00"), D("0"), "slab"), SaleLineIn(1, D("1050.01"), D("0"), "slab")],
                      D("0"), rule) == [D("5.00"), D("12.00")]


def test_ten_mixed_lines_with_a_bill_discount_add_up():
    lines = [(i % 3 + 1, f"{499 + i * 337}.00", f"{i * 11}.00") for i in range(9)] + [(1, "1499.00", "0", "18.00")]
    out = sale(*lines, bill_discount="777.77")
    for field in ("taxable_value", "cgst", "sgst", "discount"):
        assert sum((getattr(line, field) for line in out.lines), D("0")) == getattr(out, field)
    assert out.grand_total == sum((line.total for line in out.lines), D("0"))
    assert {line_rate for line_rate in line_rates(
        [SaleLineIn(n, D(p), D(d), "fixed" if r else "slab", D(r[0]) if r else None) for n, p, d, *r in lines],
        D("777.77"), DEFAULT)} == {D("5.00"), D("18.00")}


def test_validation():
    for bad in ([], [SaleLineIn(0, D("10.00"), D("0"), "slab")], [SaleLineIn(1, D("10.00"), D("11.00"), "slab")],
                [SaleLineIn(1, D("10.00"), D("0"), "fixed")]):
        with pytest.raises(ValidationFailed):
            line_rates(bad, D("0"), DEFAULT)
    with pytest.raises(ValidationFailed):
        line_rates([SaleLineIn(1, D("10.00"), D("0"), "slab")], D("10.01"), DEFAULT)
