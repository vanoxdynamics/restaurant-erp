"""Kitchen slip rendering: layout on both paper widths, markers, and hostile text (names and notes are staff-typed)."""

from datetime import datetime, timezone

import pytest

from app.modules.kots import slip as s

WHEN = datetime(2026, 11, 5, 14, 32, tzinfo=timezone.utc)


def make(marker=s.NEW, lines=None, **kw):
    base = dict(
        marker=marker, branch_name="Indiranagar", branch_code="BLR1", kot_number="KOT-BLR1-20261105-0007",
        order_number="ORD-BLR1-20261105-0003", station="kitchen", order_type="dine_in", table_number="T12", guest_count=4,
        fired_at=WHEN, fired_by="Priya Waiter", round=1, print_count=0, order_notes=None,
        lines=lines if lines is not None else [s.SlipLine(2, "Paneer Tikka (Half)", "Extra Hot; no onion")],
    )
    base.update(kw)
    return s.Slip(**base)


RICH = make(
    marker=s.ADDITIONAL, round=2, order_notes="Birthday table, bring candle with dessert",
    lines=[s.SlipLine(2, "Paneer Tikka (Half)", "Extra Hot; no onion, less oil please for the elderly guest"),
           s.SlipLine(1, "Hyderabadi Chicken Dum Biryani Family Pack (Full)"), s.SlipLine(3, "Butter Naan", None, True)],
)

GOLDEN_80 = """\
           ** ADDITIONAL - ROUND 2 **
BLR1 Indiranagar
                    KITCHEN
Table: T12                             Guests: 4
Order: ORD-BLR1-20261105-0003
KOT:   KOT-BLR1-20261105-0007
05 Nov 2026 14:32               By: Priya Waiter
------------------------------------------------
2 x Paneer Tikka (Half)
  - Extra Hot
  - no onion, less oil please for the elderly
    guest
1 x Hyderabadi Chicken Dum Biryani Family Pack
    (Full)
CANCELLED 3 x Butter Naan
------------------------------------------------
NOTE: Birthday table, bring candle with dessert
------------------------------------------------
             Round 2 for this order
"""

GOLDEN_58 = """\
   ** ADDITIONAL - ROUND 2 **
BLR1 Indiranagar
            KITCHEN
Table: T12             Guests: 4
Order: ORD-BLR1-20261105-0003
KOT:   KOT-BLR1-20261105-0007
05 Nov 2026 14:32
By: Priya Waiter
--------------------------------
2 x Paneer Tikka (Half)
  - Extra Hot
  - no onion, less oil please
    for the elderly guest
1 x Hyderabadi Chicken Dum
    Biryani Family Pack (Full)
CANCELLED 3 x Butter Naan
--------------------------------
NOTE: Birthday table, bring
      candle with dessert
--------------------------------
     Round 2 for this order
"""


def _lines(text):
    """Trailing spaces from centring carry no meaning (and editors strip them), so compare line by line without them."""
    return [line.rstrip() for line in text.splitlines()]


def test_golden_layout_on_both_paper_widths():
    assert _lines(s.render_text(RICH, 80)) == _lines(GOLDEN_80)
    assert _lines(s.render_text(RICH, 58)) == _lines(GOLDEN_58)


@pytest.mark.parametrize("paper", [58, 80])
def test_no_line_is_wider_than_the_paper(paper):
    long = make(lines=[s.SlipLine(12, "Supercalifragilisticexpialidocious-" * 4, "x" * 120 + "; " + "word " * 40), s.SlipLine(1, "ü" * 90)],
                order_notes="n" * 200, station="a-very-long-station-name-here", table_number="Rooftop-Terrace-Corner-Table-17")
    for text in s.render_text(long, paper).splitlines():
        assert len(text) <= s.COLUMNS[paper], text
    for _, text in s.header_rows(long, s.COLUMNS[paper]) + s._body_lines(long, s.COLUMNS[paper]):
        assert len(text) <= s.COLUMNS[paper], text


def test_markers():
    assert "NEW ORDER" in s.render_text(make(s.NEW))
    assert "ROUND 3" in s.render_text(make(s.ADDITIONAL, round=3))
    assert "REPRINT 2" in s.render_text(make(s.REPRINT, print_count=2))
    void = s.render_text(make(s.VOID))
    assert "VOID - CANCELLED" in void and "Take the original slip back" in void
    assert "Round" not in s.render_text(make(s.NEW))  # the round footer only appears from round 2


def test_takeaway_and_delivery_orders_show_the_order_type_instead_of_a_table():
    text = s.render_text(make(order_type="takeaway", table_number=None))
    assert "TAKEAWAY" in text and "Table:" not in text


def test_modifiers_and_notes_are_split_into_lines():
    text = s.render_text(make(lines=[s.SlipLine(1, "Biryani", "Boneless; Extra raita;  ; spicy")]))
    assert text.count("  - ") == 3 and "  - Boneless" in text and "  - spicy" in text


# --- hostile text ------------------------------------------------------------------------------------------------------------------


def test_control_characters_are_stripped():
    assert s.clean("a\x1bpb\nc\r\td\x07e f\x7f") == "a pb c d e f"
    assert s.clean(None) == "" and s.clean("   ") == ""


def test_escpos_cannot_be_injected_through_names_or_notes():
    """ESC p pulses the cash drawer, ESC @ resets the printer, GS V cuts: none may come from typed text."""
    attack = "Soup\x1bp\x00\x19\x1b@\x1dV\x00\x1b!\x30\x10\x14\x1b=\x01"
    safe, _ = s.render_escpos(make(lines=[s.SlipLine(1, "Soup")]))
    hostile, _ = s.render_escpos(make(lines=[s.SlipLine(1, attack, attack)], order_notes=attack, fired_by=attack, table_number=attack))
    for command in (b"\x1bp", b"\x1b!", b"\x1b=", b"\x10\x14"):
        assert command not in hostile, command
    # The only INIT and cut in the stream are the ones the renderer wrote itself.
    assert hostile.count(s.INIT) == 1 and hostile.count(s.GS + b"V") == 1
    assert hostile.startswith(s.INIT) and hostile.endswith(s.FEED_AND_CUT)
    assert safe.count(s.INIT) == 1


def test_html_escapes_everything_typed_by_staff():
    evil = '<script>alert(1)</script> "><img src=x onerror=alert(2)> &amp;'
    page = s.render_html(make(lines=[s.SlipLine(1, evil, evil)], order_notes=evil, fired_by=evil, table_number=evil))
    assert "<script" not in page.lower() and "<img" not in page.lower()
    assert "&lt;script&gt;" in page and "&quot;&gt;&lt;img" in page and "&amp;amp;" in page


def test_html_is_a_print_ready_page_for_the_paper_width():
    for paper in (58, 80):
        page = s.render_html(RICH, paper)
        assert page.startswith("<!doctype html>") and f"size: {paper}mm auto" in page
        assert "line-through" in page and "** ADDITIONAL - ROUND 2 **" in page
    assert 'class="marker void"' in s.render_html(make(s.VOID)) and "Take the original slip back" in s.render_html(make(s.VOID))


# --- ESC/POS encoding --------------------------------------------------------------------------------------------------------------


def test_escpos_structure_and_widths():
    data80, warnings = s.render_escpos(RICH, 80)
    data58, _ = s.render_escpos(RICH, 58)
    assert warnings == []
    for data in (data80, data58):
        assert data.startswith(s.INIT + s.CODEPAGE) and data.endswith(s.FEED_AND_CUT)
        assert s.SIZE_WIDE_TALL in data and s.BOLD_ON in data and data.count(b"\n") > 15
    assert b"-" * 48 in data80 and b"-" * 48 not in data58 and b"-" * 32 in data58


def test_accented_latin_prints_but_indic_text_is_reported_not_silently_lost():
    accented, warnings = s.render_escpos(make(lines=[s.SlipLine(1, "Crème brûlée €12 Jalapeño")]))
    assert warnings == [] and "Crème brûlée".encode("cp858") in accented
    indic, warnings = s.render_escpos(make(lines=[s.SlipLine(1, "पनीर टिक्का")]))
    assert warnings == ["unprintable_characters"] and b"?" in indic
    assert s.encode_text("abc") == b"abc" and s.encode_text("a中b") == b"a?b"


def test_marker_banner_is_bigger_for_void_and_reprint():
    void, _ = s.render_escpos(make(s.VOID))
    new, _ = s.render_escpos(make(s.NEW))
    assert void.index(s.SIZE_WIDE_TALL) < void.index(b"VOID") and s.SIZE_TALL in new
