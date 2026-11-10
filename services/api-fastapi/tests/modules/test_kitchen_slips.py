"""The kitchen slip API: formats, markers, print confirmation and reprints, voids, roles and isolation."""

import base64

import pytest
from sqlalchemy import select

from app.models import KOT, AuditLog
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, create_order, fire, line
from tests.modules.test_kitchen_module import SCREEN, SLIP, fired_ticket, set_modules


@pytest.fixture
async def go_slip(db, redis_client, branch):
    """Put the organization on a plan without the kitchen display."""
    await set_modules(db, redis_client, branch.organization_id, SLIP)


async def slip(client, user, kot_id, **params):
    return await client.get(f"{API}/kots/{kot_id}/slip", params=params, headers=auth_headers(user))


async def printed(client, user, kot_id, key=None):
    return await client.post(f"{API}/kots/{kot_id}/slip/printed", headers=idem(user, key))


async def add_round(client, user, order, item):
    r = await client.post(f"{API}/orders/{order['id']}/items", json={"version": order["version"], "items": [line(item)]}, headers=idem(user))
    assert r.status_code == 200, r.text
    return r.json()


# --- content ---------------------------------------------------------------------------------------------------------------------


async def test_json_slip_carries_what_the_kitchen_needs(client, db, branch, waiter, table):
    item = await factories.make_menu_item(db, name="Paneer Tikka", variants=[("Half", "180.00")])
    variant = (await client.get(f"{API}/catalog/items/{item.id}", headers=auth_headers(waiter))).json()["variants"][0]
    order = await create_order(client, waiter, table, items=[line(item, 2, variant_id=variant["id"], notes="no onion")],
                               guest_count=3, notes="Birthday table")
    [kot] = await fire(client, waiter, order["id"])

    r = await slip(client, waiter, kot["id"])
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["marker"], s["round"], s["print_count"], s["fulfilment"], s["status"]) == ("NEW", 1, 0, "screen", "queued")
    assert (s["branch_code"], s["table_number"], s["guest_count"], s["station"], s["order_type"]) == ("BLR1", "T1", 3, "kitchen", "dine_in")
    assert s["kot_number"] == kot["kot_number"] and s["order_number"] == order["order_number"] and s["fired_by"] == waiter.full_name
    assert s["order_notes"] == "Birthday table"
    assert s["lines"] == [{"quantity": 2, "name": "Paneer Tikka (Half)", "notes": "no onion", "cancelled": False, "station": "kitchen"}]
    assert s["warnings"] == [] and "** NEW ORDER **" in s["text"] and "2 x Paneer Tikka (Half)" in s["text"] and "  - no onion" in s["text"]
    # Times are the branch's local time (Asia/Kolkata), not UTC.
    assert s["fired_at"].endswith("+05:30")


async def test_text_html_and_printer_formats(client, db, waiter, table):
    _, kot = await fired_ticket(client, db, waiter, table, name="Soup <b>of</b> the day")

    text = await slip(client, waiter, kot["id"], format="text")
    assert text.status_code == 200 and text.headers["content-type"].startswith("text/plain") and "NEW ORDER" in text.text
    assert text.headers["cache-control"] == "no-store"

    page = await slip(client, waiter, kot["id"], format="html", width=58)
    assert page.headers["content-type"].startswith("text/html") and "size: 58mm auto" in page.text
    assert "Soup &lt;b&gt;of&lt;/b&gt; the day" in page.text and "<b>of</b>" not in page.text  # typed text is escaped
    assert "default-src 'none'" in page.headers["content-security-policy"] and page.headers["x-content-type-options"] == "nosniff"

    raw = await slip(client, waiter, kot["id"], format="escpos")
    assert raw.headers["content-type"] == "application/octet-stream" and raw.content.startswith(b"\x1b@") and raw.content.endswith(b"\x1dVB\x03")
    b64 = await slip(client, waiter, kot["id"], format="escpos_base64")
    assert base64.b64decode(b64.text) == raw.content and raw.headers["x-slip-warnings"] == ""

    narrow = await slip(client, waiter, kot["id"], format="escpos", width=58)
    assert narrow.content != raw.content and b"-" * 32 in narrow.content and b"-" * 48 not in narrow.content


async def test_bad_parameters_are_refused(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter)
    r = await slip(client, waiter, kot["id"], width=72)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_width"
    assert (await slip(client, waiter, kot["id"], format="pdf")).status_code == 422
    assert (await client.get(f"{API}/kots/00000000-0000-0000-0000-000000000000/slip", headers=auth_headers(waiter))).status_code == 404


async def test_unprintable_names_are_reported_not_silently_dropped(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter, name="पनीर टिक्का")
    s = (await slip(client, waiter, kot["id"])).json()
    assert s["warnings"] == ["unprintable_characters"]
    raw = await slip(client, waiter, kot["id"], format="escpos")
    assert raw.headers["x-slip-warnings"] == "unprintable_characters" and b"?" in raw.content


async def test_printer_commands_hidden_in_a_dish_name_never_reach_the_printer(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter, name="Soup\x1bp\x19 \x1dV\x42drawer")  # (NUL cannot be stored, so it never arrives this way)
    raw = (await slip(client, waiter, kot["id"], format="escpos")).content
    assert b"\x1bp" not in raw and raw.count(b"\x1dV") == 1  # no drawer pulse, and the only cut is the renderer's own
    assert "Soup" in (await slip(client, waiter, kot["id"])).json()["lines"][0]["name"]


# --- markers -----------------------------------------------------------------------------------------------------------------------


async def test_later_rounds_are_marked_additional(client, db, waiter, table):
    first_item = await factories.make_menu_item(db, name="Starter")
    order = await create_order(client, waiter, table, items=[line(first_item)])
    [first] = await fire(client, waiter, order["id"])
    second_item = await factories.make_menu_item(db, name="Main", category=await factories.make_category(db, name="Mains 2"))
    order = await add_round(client, waiter, order, second_item)
    [second] = await fire(client, waiter, order["id"])

    s1 = (await slip(client, waiter, first["id"])).json()
    s2 = (await slip(client, waiter, second["id"])).json()
    assert (s1["marker"], s1["round"]) == ("NEW", 1) and (s2["marker"], s2["round"]) == ("ADDITIONAL", 2)
    assert [ln["name"] for ln in s2["lines"]] == ["Main"]  # a later round lists only its own items
    assert "ADDITIONAL - ROUND 2" in s2["text"] and "Round 2 for this order" in s2["text"]


async def test_each_station_counts_its_own_rounds(client, db, waiter):
    kitchen_item = await factories.make_menu_item(db, name="Curry")
    grill_item = await factories.make_menu_item(db, name="Kebab", station="grill", category=await factories.make_category(db, name="Grill"))
    order = await create_order(client, waiter, items=[line(kitchen_item), line(grill_item)])
    kots = await fire(client, waiter, order["id"])
    assert len(kots) == 2
    for kot in kots:  # first ticket at each station is NEW, even though two tickets exist for the order
        s = (await slip(client, waiter, kot["id"])).json()
        assert (s["marker"], s["round"]) == ("NEW", 1)


async def test_on_a_slip_plan_each_send_is_one_slip_for_the_whole_table(client, db, go_slip, waiter, table):
    curry = await factories.make_menu_item(db, name="Curry")
    kebab = await factories.make_menu_item(db, name="Kebab", station="grill", category=await factories.make_category(db, name="Grill"))
    naan = await factories.make_menu_item(db, name="Naan")
    order = await create_order(client, waiter, table, items=[line(curry), line(kebab), line(naan, 2)])
    [kot] = await fire(client, waiter, order["id"])
    assert len(kot["items"]) == 3 and kot["fulfilment"] == "slip"

    s = (await slip(client, waiter, kot["id"])).json()
    assert s["marker"] == "NEW" and {(ln["name"], ln["station"]) for ln in s["lines"]} == {
        ("Curry", "kitchen"), ("Kebab", "grill"), ("Naan", "kitchen")}
    body = s["text"].split("-" * 48)[1]  # between the header rule and the next one
    assert body.index("[ KITCHEN ]") < body.index("1 x Curry") < body.index("2 x Naan") < body.index("[ GRILL ]") < body.index("1 x Kebab")
    page = (await slip(client, waiter, kot["id"], format="html")).text
    assert '<div class="section">[ GRILL ]</div>' in page

    # The next send is the next slip for the table, marked as a later round.
    order = (await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))).json()
    await add_round(client, waiter, order, kebab)
    [second] = await fire(client, waiter, order["id"])
    s2 = (await slip(client, waiter, second["id"])).json()
    assert (s2["marker"], s2["round"]) == ("ADDITIONAL", 2)
    assert "[ GRILL ]" not in s2["text"]  # one station only: no headings needed


async def test_slips_work_on_a_screen_plan_too(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter)  # default plan has the kitchen display
    r = await slip(client, waiter, kot["id"])
    assert r.status_code == 200 and r.json()["fulfilment"] == "screen"


# --- printing and reprints ---------------------------------------------------------------------------------------------------------


async def test_reading_a_slip_does_not_count_as_printing(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter)
    for _ in range(3):
        await slip(client, waiter, kot["id"])
        await slip(client, waiter, kot["id"], format="escpos")
    assert (await slip(client, waiter, kot["id"])).json()["marker"] == "NEW"
    assert (await db.scalar(select(KOT.print_count))) == 0


async def test_confirmed_prints_are_counted_and_later_ones_are_audited_reprints(client, db, waiter, manager, events):
    manager_id = manager.id  # read before the session is refreshed below
    _, kot = await fired_ticket(client, db, waiter)
    first = await printed(client, waiter, kot["id"])
    assert first.status_code == 200 and first.json()["print_count"] == 1 and first.json()["last_printed_at"]
    assert (await db.scalars(select(AuditLog).where(AuditLog.action_type == "kot_reprint"))).all() == []  # the original is not a reprint
    assert any(e["type"] == "kot.printed" for e in events)

    s = (await slip(client, waiter, kot["id"])).json()
    assert (s["marker"], s["print_count"]) == ("REPRINT", 1) and "REPRINT 1" in s["text"]

    second = await printed(client, manager, kot["id"])
    assert second.json()["print_count"] == 2
    db.expire_all()
    [audit] = (await db.scalars(select(AuditLog).where(AuditLog.action_type == "kot_reprint"))).all()
    assert audit.actor_id == manager_id and str(audit.entity_id) == kot["id"]
    assert audit.details["print_count"] == 2 and audit.details["kot_number"] == kot["kot_number"]
    row = await db.scalar(select(KOT).where(KOT.kot_number == kot["kot_number"]).execution_options(populate_existing=True))
    assert row.last_printed_by_id == manager_id and row.first_printed_at is not None and row.print_count == 2


async def test_a_retried_confirmation_is_not_a_second_print(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter)
    a = await printed(client, waiter, kot["id"], key="print-retry-0001")
    b = await printed(client, waiter, kot["id"], key="print-retry-0001")  # the network dropped the first answer
    assert a.status_code == b.status_code == 200 and b.headers.get("idempotent-replay") == "true"
    assert b.json()["print_count"] == 1
    assert (await client.post(f"{API}/kots/{kot['id']}/slip/printed", headers=auth_headers(waiter))).status_code == 400  # key required
    assert (await printed(client, waiter, kot["id"])).json()["print_count"] == 2  # a genuinely new confirmation is a reprint


async def test_every_fire_starts_unprinted_so_the_floor_can_see_what_still_needs_paper(client, db, go_slip, waiter):
    _, kot = await fired_ticket(client, db, waiter)
    assert kot["print_count"] == 0 and kot["fulfilment"] == "slip" and kot["last_printed_at"] is None
    await printed(client, waiter, kot["id"])
    listed = (await client.get(f"{API}/kots", headers=auth_headers(waiter))).json()
    assert [(k["id"], k["print_count"]) for k in listed] == [(kot["id"], 1)]


# --- voids ---------------------------------------------------------------------------------------------------------------------------


async def test_a_voided_ticket_prints_a_void_slip_with_every_line(client, db, waiter, manager):
    item_a = await factories.make_menu_item(db, name="Curry")
    item_b = await factories.make_menu_item(db, name="Dal")
    order = await create_order(client, waiter, items=[line(item_a, 2), line(item_b)])
    [kot] = await fire(client, waiter, order["id"])
    await printed(client, waiter, kot["id"])  # the original slip is already in the kitchen
    r = await client.post(f"{API}/kots/{kot['id']}/void", json={"void_reason": "guest left", "approver_pin": "9001"}, headers=auth_headers(waiter))
    assert r.status_code == 200, r.text

    s = (await slip(client, waiter, kot["id"])).json()
    assert s["marker"] == "VOID" and s["status"] == "voided"
    assert sorted(ln["name"] for ln in s["lines"]) == ["Curry", "Dal"] and not any(ln["cancelled"] for ln in s["lines"])
    assert "VOID - CANCELLED" in s["text"] and "Take the original slip back" in s["text"]
    page = await slip(client, waiter, kot["id"], format="html")
    assert 'class="marker void"' in page.text
    assert manager.id


async def test_a_cancelled_line_on_a_reprint_is_shown_as_cancelled(client, db, waiter, manager):  # manager: the approver PIN
    keep = await factories.make_menu_item(db, name="Keep")
    drop = await factories.make_menu_item(db, name="Drop")
    order = await create_order(client, waiter, items=[line(keep), line(drop)])
    [kot] = await fire(client, waiter, order["id"])
    drop_line = next(i for i in order["items"] if i["name_snapshot"] == "Drop")
    r = await client.post(f"{API}/orders/{order['id']}/items/{drop_line['id']}/cancel",
                          json={"version": order["version"], "reason": "changed mind", "approver_pin": "9001"}, headers=idem(waiter))
    assert r.status_code == 200, r.text
    s = (await slip(client, waiter, kot["id"])).json()
    assert {ln["name"]: ln["cancelled"] for ln in s["lines"]} == {"Keep": False, "Drop": True}
    assert "CANCELLED 1 x Drop" in s["text"]


# --- who may read and confirm ---------------------------------------------------------------------------------------------------------


async def test_roles_and_plans(client, db, redis_client, branch, waiter, cashier, manager, kitchen):
    _, kot = await fired_ticket(client, db, waiter)
    for user in (waiter, cashier, manager, kitchen):  # screen plan: everyone on the board, kitchen included
        assert (await slip(client, user, kot["id"])).status_code == 200
    await set_modules(db, redis_client, branch.organization_id, SLIP)
    for user in (waiter, cashier, manager):
        assert (await slip(client, user, kot["id"])).status_code == 200
        assert (await printed(client, user, kot["id"])).status_code == 200
    r = await slip(client, kitchen, kot["id"])  # no kitchen screen on this plan, so no kitchen account either
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    assert (await printed(client, kitchen, kot["id"])).status_code == 403
    await set_modules(db, redis_client, branch.organization_id, SCREEN)


async def test_other_organizations_cannot_read_or_confirm_a_slip(client, db, waiter):
    _, kot = await fired_ticket(client, db, waiter)
    other_org = await factories.make_org(db, slug="globex", name="Globex")
    other_branch = await factories.make_branch(db, code="DEL1", name="Delhi", org=other_org)
    stranger = await factories.make_staff(db, other_branch, role="owner", pin="8000", username="owner_b")
    assert (await slip(client, stranger, kot["id"])).status_code == 404
    assert (await printed(client, stranger, kot["id"])).status_code == 404
    assert (await client.get(f"{API}/kots/{kot['id']}/slip")).status_code == 401
    assert (await slip(client, waiter, kot["id"])).status_code == 200


async def test_slip_endpoints_follow_the_plan_both_ways(client, db, redis_client, branch, waiter):
    """The same ticket prints on a slip plan and on a screen plan: printing is not gated, only kitchen accounts are."""
    for modules in (SLIP, SCREEN):
        await set_modules(db, redis_client, branch.organization_id, modules)
        _, kot = await fired_ticket(client, db, waiter)
        assert (await slip(client, waiter, kot["id"], format="text")).status_code == 200
        assert kot["fulfilment"] == ("slip" if modules == SLIP else "screen")


# --- the counter's print station --------------------------------------------------------------------------------------------------


async def queue(client, user):
    r = await client.get(f"{API}/kots/print-queue", headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return [(k["kot_number"], k["status"]) for k in r.json()]


async def test_the_print_station_gets_new_slips_then_owed_void_notices(client, db, go_slip, waiter, cashier, manager):
    _, first = await fired_ticket(client, db, waiter, name="Curry")
    _, second = await fired_ticket(client, db, waiter, name="Dal")
    assert await queue(client, cashier) == [(first["kot_number"], "queued"), (second["kot_number"], "queued")]  # oldest first

    await printed(client, cashier, first["id"])
    assert await queue(client, cashier) == [(second["kot_number"], "queued")]

    # The printed one is cancelled: the kitchen is owed a VOID notice. The unprinted one is cancelled: nothing to take back.
    for kot in (first, second):
        r = await client.post(f"{API}/kots/{kot['id']}/void", json={"void_reason": "guest left", "approver_pin": "9001"},
                              headers=auth_headers(waiter))
        assert r.status_code == 200, r.text
    assert await queue(client, cashier) == [(first["kot_number"], "voided")]
    assert (await slip(client, cashier, first["id"])).json()["marker"] == "VOID"

    r = await printed(client, cashier, first["id"])
    assert r.json()["print_count"] == 1 and r.json()["void_printed_at"]  # a VOID notice is not a reprint of the order
    assert await queue(client, cashier) == []
    assert (await db.scalars(select(AuditLog).where(AuditLog.action_type == "kot_reprint"))).all() == []
    await printed(client, cashier, first["id"])  # a second VOID copy is a reprint, and audited
    [audit] = (await db.scalars(select(AuditLog).where(AuditLog.action_type == "kot_reprint"))).all()
    assert audit.details["void_notice"] is True
    assert manager.id


async def test_screen_tickets_never_reach_the_print_station(client, db, waiter, cashier):
    await fired_ticket(client, db, waiter)  # default plan: kitchen screen
    assert await queue(client, cashier) == []


async def test_slip_print_rows_fit_the_paper(client, db, go_slip, waiter, table):
    item = await factories.make_menu_item(db, name="Paneer Butter Masala with extra gravy and a very long name", station="grill",
                                          category=await factories.make_category(db, name="Grill"))
    other = await factories.make_menu_item(db, name="Jeera Rice")
    order = await create_order(client, waiter, table, items=[line(item, 2, notes="less oil; no onion"), line(other)])
    [kot] = await fire(client, waiter, order["id"])
    for width, cols in ((58, 32), (80, 48)):
        s = (await slip(client, waiter, kot["id"], width=width)).json()
        assert s["paper_mm"] == width
        styles = {(r["style"], r["align"]) for r in s["rows"]}
        assert ("big", "center") in styles and ("bold", "center") in styles  # station banner and section headings
        for row in s["rows"]:
            assert len(row["text"]) <= (cols // 2 if row["style"] == "big" else cols), row
        texts = [r["text"] for r in s["rows"]]
        assert "[ GRILL ]" in texts and "  - less oil" in texts and texts[0].strip() == "** NEW ORDER **"


async def test_paper_kots_are_not_sent_for_remote_approval(client, db, go_slip, waiter, table):
    """Without the kitchen screen there is nobody to ask: a paper KOT is cancelled at the till with a manager's PIN."""
    item = await factories.make_menu_item(db, name="Dal")
    order = await create_order(client, waiter, table, items=[line(item)])
    kot = (await fire(client, waiter, order["id"]))[0]
    r = await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "guest left"}, headers=auth_headers(waiter))
    assert r.status_code == 409 and r.json()["error"]["code"] == "manager_pin_required"
