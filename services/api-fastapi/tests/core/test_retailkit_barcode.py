"""retailkit.barcode: EAN-13 check digits and the bar patterns drawn on labels."""

import re

import pytest

from app.modules.retailkit.barcode import (
    _CODE128,
    code128_modules,
    code128_svg,
    code128_values,
    ean13_check_digit,
    ean13_modules,
    ean13_svg,
    is_ean13,
)


@pytest.mark.parametrize("code", ["4006381333931", "5901234123457", "9780306406157"])
def test_known_ean13_check_digits(code):
    assert ean13_check_digit(code[:12]) == code[12]
    assert is_ean13(code)


def test_check_digit_rejects_bad_input():
    for bad in ("12345", "abcdefghijkl", "1234567890123"):
        with pytest.raises(ValueError):
            ean13_check_digit(bad)
    assert not is_ean13("4006381333932")


def test_ean13_structure():
    modules = ean13_modules("4006381333931")
    assert len(modules) == 95
    assert modules[:3] == "101" and modules[45:50] == "01010" and modules[-3:] == "101"
    # First digit 4 -> parity LGLLGG: the first 0 in L, the second 0 in G.
    assert modules[3:10] == "0001101" and modules[10:17] == "0100111"
    # The right half starts with 3, in R.
    assert modules[50:57] == "1000010"


def decode_code128(modules: str) -> list:
    """Read symbol values back from the modules (a minimal scanner) to check the patterns round-trip."""
    runs = [len(m.group()) for m in re.finditer(r"1+|0+", modules)]
    widths = {p: v for v, p in enumerate(_CODE128)}
    values, i = [], 0
    while i < len(runs):
        size = 7 if "".join(map(str, runs[i:i + 7])) == _CODE128[106] else 6
        values.append(widths["".join(map(str, runs[i:i + size]))])
        i += size
    return values


def test_code128_table_is_sound():
    assert len(_CODE128) == 107 and len(set(_CODE128)) == 107
    assert all(sum(map(int, p)) == 11 for p in _CODE128[:106]) and sum(map(int, _CODE128[106])) == 13
    # Every symbol starts with a bar and has three bars and three spaces of width 1-4.
    assert all(len(p) == 6 and set(p) <= set("1234") for p in _CODE128[:106])


@pytest.mark.parametrize("text,start", [("ABC-12", 104), ("123456", 105), ("12345", 104), ("SKU 9", 104)])
def test_code128_round_trip_and_checksum(text, start):
    values = code128_values(text)
    assert values[0] == start
    assert values[-1] == (values[0] + sum(v * i for i, v in enumerate(values[1:-1], start=1))) % 103
    assert decode_code128(code128_modules(text)) == values + [106]


def test_code128_known_value():
    # Code set B "PJJ123C": values P=48 J=42 J=42 1=17 2=18 3=19 C=35 after start 104;
    # 104 + 48*1 + 42*2 + 42*3 + 17*4 + 18*5 + 19*6 + 35*7 = 879, and 879 mod 103 = 55.
    assert code128_values("PJJ123C") == [104, 48, 42, 42, 17, 18, 19, 35, 55]


def test_code128_rejects_non_ascii():
    with pytest.raises(ValueError):
        code128_values("")
    with pytest.raises(ValueError):
        code128_values("kurta₹")


def test_svgs_are_well_formed_and_escaped():
    svg = ean13_svg("4006381333931")
    assert svg.startswith("<svg") and svg.endswith("</svg>") and "4006381333931" in svg
    svg = code128_svg("<A&B>")
    assert "&lt;A&amp;B&gt;" in svg and "<A&B>" not in svg
    bars = len(re.findall(r"1+", code128_modules("<A&B>")))
    assert svg.count("<rect") == bars + 1  # one per bar, plus the white background
