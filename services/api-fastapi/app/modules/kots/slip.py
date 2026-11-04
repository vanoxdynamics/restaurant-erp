"""
Kitchen slips: the paper (or print preview) the kitchen cooks from on plans without a kitchen screen.

A slip is just a rendering of a KOT, so voids, approvals and audit keep working. This module is pure (no database): it turns a
`Slip` into plain text, a printable HTML page, or ESC/POS bytes for thermal receipt printers.

Item names and notes are typed by staff and end up on paper, so they are treated as untrusted:
- control characters are stripped everywhere (raw ones in an ESC/POS stream could issue printer commands such as opening the
  cash drawer or changing settings),
- everything placed in HTML is escaped,
- text a printer's code page cannot represent (for example Indic scripts on a basic thermal printer) is replaced with '?' and
  reported in `warnings`; printing such names needs raster output, which is a later phase.
"""

import html
import textwrap
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

# Marker printed at the top of the slip (the most important line on it).
NEW, ADDITIONAL, REPRINT, VOID = "NEW", "ADDITIONAL", "REPRINT", "VOID"

# Characters per line on common paper widths (Font A).
COLUMNS = {58: 32, 80: 48}
ESCPOS_CODEPAGE = "cp858"  # Western European incl. accents and the euro sign; selected with ESC t 19


@dataclass(frozen=True)
class SlipLine:
    quantity: int
    name: str
    notes: Optional[str] = None
    cancelled: bool = False
    station: Optional[str] = None  # the line's prep station; a slip spanning several stations groups its lines by it


@dataclass(frozen=True)
class Slip:
    marker: str
    branch_name: str
    branch_code: str
    kot_number: str
    order_number: str
    station: str
    order_type: str  # dine_in | takeaway | delivery
    table_number: Optional[str]
    guest_count: int
    fired_at: datetime  # already in the branch's local time
    fired_by: Optional[str]
    round: int  # 1 for the first ticket of this station on the order, 2 for the next, ...
    print_count: int  # completed prints so far; the next one is a reprint when this is above zero
    lines: List[SlipLine] = field(default_factory=list)
    order_notes: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def marker_text(self) -> str:
        if self.marker == VOID:
            return "*** VOID - CANCELLED ***"
        if self.marker == REPRINT:
            return f"*** REPRINT {self.print_count} ***"
        if self.marker == ADDITIONAL:
            return f"** ADDITIONAL - ROUND {self.round} **"
        return "** NEW ORDER **"

    def order_type_label(self) -> str:
        return self.order_type.replace("_", " ").upper()

    def timestamp(self) -> str:
        return self.fired_at.strftime("%d %b %Y %H:%M")


# --- sanitising and layout ------------------------------------------------------------------------------------------------


def clean(text: Optional[str]) -> str:
    """Single-line, printable text: control characters (including newlines and ESC) become spaces."""
    if not text:
        return ""
    return " ".join("".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in text).split())


def _wrap(text: str, width: int, first: str = "", rest: str = "") -> List[str]:
    return textwrap.wrap(text, width=width, initial_indent=first, subsequent_indent=rest, break_long_words=True,
                         break_on_hyphens=False) or [first.rstrip()]


def _pair(left: str, right: str, width: int) -> List[str]:
    """`left` and `right` on one line (right-aligned) when they fit, otherwise on two lines."""
    if not right:
        return [left]
    if len(left) + len(right) + 1 > width:
        return [left, right]
    return [left + " " * (width - len(left) - len(right)) + right]


def _body_lines(slip: Slip, width: int) -> List[Tuple[str, str]]:
    """
    The item section as (kind, text) rows; kind is 'section', 'item', 'note' or 'cancelled'. When the lines come from more
    than one prep station (one slip for a whole table), each station's lines follow a 'section' heading.
    """
    stations = list(dict.fromkeys(line.station for line in slip.lines if line.station))
    grouped = len(stations) > 1
    ordered = sorted(slip.lines, key=lambda line: stations.index(line.station) if line.station in stations else len(stations))         if grouped else slip.lines
    rows: List[Tuple[str, str]] = []
    current: Optional[str] = None
    for line in ordered:
        if grouped and line.station != current:
            current = line.station
            rows.append(("section", f"[ {clean(current or 'other').upper()} ]"))
        name = clean(line.name)
        head = f"{line.quantity} x {name}"
        if line.cancelled:
            head = f"CANCELLED {head}"
        for text in _wrap(head, width, rest="    "):
            rows.append(("cancelled" if line.cancelled else "item", text))
        for part in (p for p in (clean(x) for x in (line.notes or "").split(";")) if p):
            for text in _wrap(part, width, first="  - ", rest="    "):
                rows.append(("note", text))
    return rows


def _marker_is_wide(slip: Slip) -> bool:
    """VOID and REPRINT banners print double-width on thermal paper, so they get half the characters per line."""
    return slip.marker in (VOID, REPRINT)


def header_rows(slip: Slip, width: int) -> List[Tuple[str, str]]:
    """
    Everything above the item list as (style, text): 'marker', 'big', 'normal'. Rows are wrapped to what fits: double-width
    styles ('big', and the marker of a VOID or REPRINT) fit half as many characters per line.
    """
    half = max(width // 2, 8)
    rows: List[Tuple[str, str]] = []
    rows += [("marker", t) for t in _wrap(slip.marker_text(), half if _marker_is_wide(slip) else width)]
    rows += [("normal", t) for t in _wrap(f"{clean(slip.branch_code)} {clean(slip.branch_name)}".strip(), width)]
    rows += [("big", t) for t in _wrap(clean(slip.station).upper(), half)]
    where = f"Table: {clean(slip.table_number)}" if slip.table_number else slip.order_type_label()
    guests = f"Guests: {slip.guest_count}"
    if len(where) + len(guests) + 1 > width:
        rows += [("normal", t) for t in _wrap(where, width, rest="  ")] + [("normal", guests)]
    else:
        rows += [("normal", t) for t in _pair(where, guests, width)]
    rows += [("normal", t) for t in _wrap(f"Order: {clean(slip.order_number)}", width, rest="       ")]
    rows += [("normal", t) for t in _wrap(f"KOT:   {clean(slip.kot_number)}", width, rest="       ")]
    by = f"By: {clean(slip.fired_by)}" if slip.fired_by else ""
    if len(by) > width:
        rows += [("normal", slip.timestamp())] + [("normal", t) for t in _wrap(by, width, rest="    ")]
    else:
        rows += [("normal", t) for t in _pair(slip.timestamp(), by, width)]
    return rows


# --- print rows -----------------------------------------------------------------------------------------------------------------

# One printed line for printers that take text line by line (e.g. the Pine Labs device's PrintData):
# (style, align, text) with style 'normal', 'bold' or 'big' (double width: already wrapped to half the line) and align 'left' or
# 'center'. Text is already cleaned and wrapped to the paper width.
PrintRow = Tuple[str, str, str]


def print_rows(slip: Slip, paper_mm: int = 80) -> List[PrintRow]:
    width = COLUMNS[paper_mm]
    rule: PrintRow = ("normal", "left", "-" * width)
    rows: List[PrintRow] = []
    for style, text in header_rows(slip, width):
        if style == "marker":
            rows.append(("big" if _marker_is_wide(slip) else "bold", "center", text))
        elif style == "big":
            rows.append(("big", "center", text))
        else:
            rows.append(("normal", "left", text))
    rows.append(rule)
    for kind, text in _body_lines(slip, width):
        if kind == "section":
            rows.append(("bold", "center", text))
        else:
            rows.append(("normal" if kind == "note" else "bold", "left", text))
    if slip.order_notes:
        rows.append(rule)
        rows += [("normal", "left", t) for t in _wrap(f"NOTE: {clean(slip.order_notes)}", width, rest="      ")]
    rows.append(rule)
    if slip.marker == VOID:
        rows.append(("bold", "center", "Take the original slip back"))
    elif slip.round > 1:
        rows.append(("bold", "center", f"Round {slip.round} for this order"))
    return rows


# --- plain text -----------------------------------------------------------------------------------------------------------------


def render_text(slip: Slip, paper_mm: int = 80) -> str:
    width = COLUMNS[paper_mm]
    rule = "-" * width
    out: List[str] = []
    for style, text in header_rows(slip, width):
        out.append(text.center(width) if style in ("marker", "big") else text)
    out.append(rule)
    out += [text for _, text in _body_lines(slip, width)]
    if slip.order_notes:
        out.append(rule)
        out += _wrap(f"NOTE: {clean(slip.order_notes)}", width, rest="      ")
    out.append(rule)
    if slip.marker == VOID:
        out.append("Take the original slip back".center(width))
    elif slip.round > 1:
        out.append(f"Round {slip.round} for this order".center(width))
    return "\n".join(out) + "\n"


# --- printable HTML -------------------------------------------------------------------------------------------------------------


def render_html(slip: Slip, paper_mm: int = 80) -> str:
    esc = html.escape
    width = COLUMNS[paper_mm]
    head = []
    for style, text in header_rows(slip, width):
        css = {"marker": "marker " + slip.marker.lower(), "big": "station", "normal": "meta"}[style]
        head.append(f'<div class="{css}">{esc(text)}</div>')
    body = []
    for kind, text in _body_lines(slip, width):
        css = {"section": "section", "item": "item", "note": "note", "cancelled": "item cancelled"}[kind]
        body.append(f'<div class="{css}">{esc(text)}</div>')
    notes = f'<hr><div class="meta">{esc("NOTE: " + clean(slip.order_notes))}</div>' if slip.order_notes else ""
    foot = ""
    if slip.marker == VOID:
        foot = '<div class="foot">Take the original slip back</div>'
    elif slip.round > 1:
        foot = f'<div class="foot">Round {slip.round} for this order</div>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(slip.kot_number)}</title>
<style>
@page {{ size: {paper_mm}mm auto; margin: 2mm; }}
html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
body {{ width: {paper_mm - 4}mm; font: 12px/1.35 'Courier New', Courier, monospace; }}
div {{ white-space: pre-wrap; word-break: break-word; }}
.marker {{ text-align: center; font-weight: 700; font-size: 15px; padding: 3px 0; border: 2px solid #000; margin-bottom: 4px; }}
.marker.void, .marker.reprint {{ background: #000; color: #fff; }}
.station {{ text-align: center; font-weight: 700; font-size: 22px; margin: 3px 0; }}
.section {{ font-weight: 700; font-size: 13px; margin-top: 6px; text-align: center; }}
.item {{ font-weight: 700; font-size: 14px; margin-top: 3px; }}
.item.cancelled {{ text-decoration: line-through; }}
.note {{ font-size: 12px; }}
.foot {{ text-align: center; font-weight: 700; margin-top: 4px; }}
hr {{ border: 0; border-top: 1px dashed #000; margin: 4px 0; }}
</style></head><body>
{chr(10).join(head)}
<hr>
{chr(10).join(body)}
{notes}
<hr>
{foot}
</body></html>
"""


# --- ESC/POS --------------------------------------------------------------------------------------------------------------------

ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
CODEPAGE = ESC + b"t" + bytes([19])  # PC858
ALIGN_LEFT, ALIGN_CENTER = ESC + b"a\x00", ESC + b"a\x01"
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
SIZE_NORMAL, SIZE_WIDE_TALL = GS + b"!\x00", GS + b"!\x11"
SIZE_TALL = GS + b"!\x01"
FEED_AND_CUT = b"\n\n\n" + GS + b"V" + bytes([66, 3])  # feed, then partial cut


def encode_text(text: str, warnings: Optional[List[str]] = None) -> bytes:
    """Encode for the printer's code page; characters it cannot print become '?' and are reported."""
    out = bytearray()
    lost = False
    for ch in text:
        try:
            out += ch.encode(ESCPOS_CODEPAGE)
        except UnicodeEncodeError:
            out += b"?"
            lost = True
    if lost and warnings is not None and "unprintable_characters" not in warnings:
        warnings.append("unprintable_characters")
    return bytes(out)


def render_escpos(slip: Slip, paper_mm: int = 80) -> Tuple[bytes, List[str]]:
    width = COLUMNS[paper_mm]
    warnings: List[str] = list(slip.warnings)
    out = bytearray(INIT + CODEPAGE)

    def put(text: str, *, align: bytes = ALIGN_LEFT, size: bytes = SIZE_NORMAL, bold: bool = False) -> None:
        out.extend(align + size + (BOLD_ON if bold else BOLD_OFF) + encode_text(text, warnings) + b"\n")

    rule = "-" * width
    for style, text in header_rows(slip, width):
        if style == "marker":
            put(text, align=ALIGN_CENTER, size=SIZE_WIDE_TALL if slip.marker in (VOID, REPRINT) else SIZE_TALL, bold=True)
        elif style == "big":
            put(text, align=ALIGN_CENTER, size=SIZE_WIDE_TALL, bold=True)
        else:
            put(text)
    put(rule)
    for kind, text in _body_lines(slip, width):
        if kind == "section":
            put(text, align=ALIGN_CENTER, bold=True)
        else:
            put(text, size=SIZE_TALL if kind != "note" else SIZE_NORMAL, bold=kind != "note")
    if slip.order_notes:
        put(rule)
        for text in _wrap(f"NOTE: {clean(slip.order_notes)}", width, rest="      "):
            put(text)
    put(rule)
    if slip.marker == VOID:
        put("Take the original slip back", align=ALIGN_CENTER, bold=True)
    elif slip.round > 1:
        put(f"Round {slip.round} for this order", align=ALIGN_CENTER, bold=True)
    out.extend(SIZE_NORMAL + BOLD_OFF + ALIGN_LEFT + FEED_AND_CUT)
    return bytes(out), warnings
