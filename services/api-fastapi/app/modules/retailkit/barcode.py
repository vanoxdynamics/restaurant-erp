"""
Barcodes for shop labels (pure, no dependencies): the EAN-13 check digit, and EAN-13 / Code 128 drawn as inline SVG.

The SVG is plain black bars on white, sized in "modules" (the narrowest bar) so the page can scale it; quiet zones are
included, because scanners need the white margin to find the code.
"""

import html
from typing import List

# EAN-13: left-hand "L" (odd parity) patterns; "R" is their complement and "G" (even parity) is R reversed.
_L = ("0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011")
_R = tuple("".join("1" if b == "0" else "0" for b in p) for p in _L)
_G = tuple(p[::-1] for p in _R)
# Which of L / G the six left digits use, chosen by the first digit (which is not drawn as bars).
_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")

# Code 128 bar/space widths for symbol values 0-106 (106 is the stop symbol, which has a final 2-wide bar).
_CODE128 = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
_START_B, _START_C, _CODE_B, _CODE_C, _STOP = 104, 105, 100, 99, 106


def ean13_check_digit(twelve: str) -> str:
    """The check digit that completes a 12-digit EAN-13 body."""
    if len(twelve) != 12 or not twelve.isdigit():
        raise ValueError("An EAN-13 body is exactly 12 digits")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(twelve))
    return str((10 - total % 10) % 10)


def is_ean13(code: str) -> bool:
    return len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == code[12]


def ean13_modules(code: str) -> str:
    """The 95 modules of an EAN-13 symbol as a string of 1 (bar) and 0 (space)."""
    if not is_ean13(code):
        raise ValueError("Not a valid EAN-13 code")
    parity = _PARITY[int(code[0])]
    left = "".join((_L if parity[i] == "L" else _G)[int(d)] for i, d in enumerate(code[1:7]))
    right = "".join(_R[int(d)] for d in code[7:])
    return "101" + left + "01010" + right + "101"


def code128_values(text: str) -> List[int]:
    """
    Symbol values for `text`, start and check symbol included, stop excluded. An even run of digits uses code set C (two
    digits per symbol, a shorter label); anything else uses code set B (printable ASCII).
    """
    if not text or any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError("Code 128 labels take printable ASCII only")
    values: List[int]
    if text.isdigit() and len(text) % 2 == 0:
        values = [_START_C] + [int(text[i:i + 2]) for i in range(0, len(text), 2)]
    else:
        values = [_START_B] + [ord(c) - 32 for c in text]
    check = (values[0] + sum(v * i for i, v in enumerate(values[1:], start=1))) % 103
    return values + [check]


def code128_modules(text: str) -> str:
    out = []
    for value in code128_values(text) + [_STOP]:
        for i, width in enumerate(_CODE128[value]):
            out.append(("1" if i % 2 == 0 else "0") * int(width))
    return "".join(out)


def _svg(modules: str, caption: str, *, quiet: int, height: int = 50, stretch: bool = False) -> str:
    """
    Bars as rectangles, one unit per module, with the human-readable text under them. With `stretch` the SVG holds the bars
    only and may be stretched to any box (bars keep their proportions across, so it still scans); the page prints the digits.
    """
    width = len(modules) + 2 * quiet
    rects = []
    i = 0
    while i < len(modules):
        if modules[i] == "1":
            j = i
            while j < len(modules) and modules[j] == "1":
                j += 1
            rects.append(f'<rect x="{quiet + i}" y="0" width="{j - i}" height="{height}"/>')
            i = j
        else:
            i += 1
    if stretch:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="none" class="barcode" '
            f'shape-rendering="crispEdges" role="img" aria-label="{html.escape(caption)}">'
            f'<rect width="{width}" height="{height}" fill="#fff"/><g fill="#000">{"".join(rects)}</g></svg>'
        )
    text_y = height + 9
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height + 11}" class="barcode" '
        f'shape-rendering="crispEdges" role="img" aria-label="{html.escape(caption)}">'
        f'<rect width="{width}" height="{height + 11}" fill="#fff"/><g fill="#000">{"".join(rects)}</g>'
        f'<text x="{width / 2}" y="{text_y}" font-family="monospace" font-size="9" text-anchor="middle" fill="#000">'
        f"{html.escape(caption)}</text></svg>"
    )


def ean13_svg(code: str, *, stretch: bool = False) -> str:
    return _svg(ean13_modules(code), code, quiet=11, stretch=stretch)


def code128_svg(text: str, *, stretch: bool = False) -> str:
    return _svg(code128_modules(text), text, quiet=10, stretch=stretch)
