"""retailkit.tax: per-line GST for shop invoices (inclusive and exclusive prices, bill discounts, IGST, round-off)."""

from decimal import Decimal as D
from itertools import product

import pytest

from app.core.errors import ValidationFailed
from app.modules.retailkit.tax import LineIn, allocate, compute_invoice


def inv(lines, bill_discount="0", *, inclusive=True, interstate=False, round_to_rupee=False):
    return compute_invoice([LineIn(D(g), D(d), D(r)) for g, d, r in lines], D(bill_discount), inclusive=inclusive,
                           interstate=interstate, round_to_rupee=round_to_rupee)


def assert_sums(out):
    for field in ("taxable_value", "cgst", "sgst", "igst"):
        assert sum((getattr(line, field) for line in out.lines), D("0")) == getattr(out, field), field
    assert sum((line.discount for line in out.lines), D("0")) == out.discount
    assert sum((line.total for line in out.lines), D("0")) + out.round_off == out.grand_total
    for line in out.lines:
        assert line.total == line.taxable_value + line.cgst + line.sgst + line.igst


@pytest.mark.parametrize("gross,rate,taxable,cgst,sgst", [
    ("999.00", "5", "951.43", "23.79", "23.78"),
    ("2999.00", "18", "2541.53", "228.74", "228.73"),
    ("2625.00", "5", "2500.00", "62.50", "62.50"),
    ("105.00", "18", "88.98", "8.01", "8.01"),
    ("3500.00", "18", "2966.10", "266.95", "266.95"),
    ("75.00", "18", "63.56", "5.72", "5.72"),
])
def test_inclusive_single_lines(gross, rate, taxable, cgst, sgst):
    out = inv([(gross, "0", rate)])
    line = out.lines[0]
    assert (line.taxable_value, line.cgst, line.sgst, line.igst) == (D(taxable), D(cgst), D(sgst), D("0"))
    assert line.total == D(gross) and out.grand_total == D(gross)
    assert_sums(out)


def test_exclusive_prices_add_tax_on_top():
    out = inv([("1000.00", "0", "18"), ("100.00", "10.00", "5")], inclusive=False)
    a, b = out.lines
    assert (a.taxable_value, a.cgst, a.sgst, a.total) == (D("1000.00"), D("90.00"), D("90.00"), D("1180.00"))
    assert (b.taxable_value, b.cgst, b.sgst, b.total) == (D("90.00"), D("2.25"), D("2.25"), D("94.50"))
    assert out.subtotal == D("1100.00") and out.discount == D("10.00") and out.grand_total == D("1274.50")
    assert_sums(out)


def test_line_discount_then_tax():
    out = inv([("2999.00", "449.85", "5")])  # 15% off a kurta: net 2,549.15
    line = out.lines[0]
    assert (line.taxable_value, line.cgst, line.sgst) == (D("2427.76"), D("60.70"), D("60.69"))
    assert line.discount == D("449.85") and out.grand_total == D("2549.15")


def test_interstate_is_all_igst():
    line = inv([("3500.00", "0", "18")], interstate=True).lines[0]
    assert (line.taxable_value, line.cgst, line.sgst, line.igst) == (D("2966.10"), D("0"), D("0"), D("533.90"))


def test_bill_discount_spread_in_proportion_with_remainder_on_largest():
    out = inv([("100.00", "0", "5"), ("100.00", "0", "18"), ("100.00", "0", "12")], "10.00")
    assert [line.discount for line in out.lines] == [D("3.34"), D("3.33"), D("3.33")]
    assert out.discount == D("10.00") and out.grand_total == D("290.00")
    assert_sums(out)


def test_bill_discount_is_shared_on_net_after_line_discounts():
    out = inv([("200.00", "100.00", "5"), ("100.00", "0", "5")], "20.00")
    assert [line.discount for line in out.lines] == [D("110.00"), D("10.00")]


def test_round_to_rupee():
    out = inv([("999.40", "0", "5"), ("10.25", "0", "5")], round_to_rupee=True)
    assert out.grand_total == D("1010.00") and out.round_off == D("0.35")
    out = inv([("999.40", "0", "5")], round_to_rupee=True)
    assert out.grand_total == D("999.00") and out.round_off == D("-0.40")
    out = inv([("100.50", "0", "0")], round_to_rupee=True)
    assert out.grand_total == D("101.00") and out.round_off == D("0.50")  # half rounds up
    assert_sums(out)


def test_zero_rate_line():
    line = inv([("50.00", "0", "0")]).lines[0]
    assert (line.taxable_value, line.cgst, line.sgst, line.total) == (D("50.00"), D("0"), D("0"), D("50.00"))


def test_gross_is_rounded_to_paise():
    out = inv([("0.4995", "0", "0")])  # 0.333 x 1.50
    assert out.subtotal == D("0.50")


@pytest.mark.parametrize("inclusive,interstate", list(product([True, False], [True, False])))
def test_lines_always_sum_to_totals(inclusive, interstate):
    lines = [(f"{(i * 137) % 997 + 0.37:.2f}", f"{(i * 7) % 13:.2f}", rate)
             for i, rate in enumerate(["0", "3", "5", "12", "18", "28", "5", "18", "0.25", "40"], start=1)]
    for discount in ("0", "0.01", "33.33", "777.77"):
        out = inv(lines, discount, inclusive=inclusive, interstate=interstate, round_to_rupee=True)
        assert_sums(out)
        assert out.discount == sum((D(d) for _, d, _ in lines), D("0")) + D(discount)


def test_validation():
    with pytest.raises(ValidationFailed) as e:
        inv([])
    assert e.value.code == "empty_invoice"
    for bad in ([("10.00", "-1", "5")], [("10.00", "10.01", "5")], [("10.00", "0", "-5")]):
        with pytest.raises(ValidationFailed):
            inv(bad)
    with pytest.raises(ValidationFailed) as e:
        inv([("10.00", "5.00", "5")], "5.01")
    assert e.value.code == "invalid_discount"
    inv([("10.00", "5.00", "5")], "5.00")  # a discount up to everything is allowed
    with pytest.raises(TypeError):
        compute_invoice([LineIn(10.0, D("0"), D("5"))], D("0"), inclusive=True, interstate=False,  # type: ignore[arg-type]
                        round_to_rupee=False)


def test_allocate():
    assert allocate(D("10.00"), [D("1"), D("1"), D("1")]) == [D("3.34"), D("3.33"), D("3.33")]
    assert allocate(D("0"), [D("5"), D("5")]) == [D("0"), D("0")]
    assert allocate(D("5.00"), [D("0"), D("0")]) == [D("0"), D("0")]
    assert allocate(D("1.00"), [D("1"), D("3")]) == [D("0.25"), D("0.75")]
    assert sum(allocate(D("100.00"), [D("3"), D("3"), D("3"), D("7")]), D("0")) == D("100.00")
