"""
End-to-end service on each plan: one table from first order to closed bill, chaining firing, slips, reprints, voids,
serving and payment the way a shift actually runs, plus a plan switch in the middle of service.
"""

from sqlalchemy import select

from app.models import AuditLog
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line, pay
from tests.modules.test_kitchen_module import SCREEN, SLIP, go, set_status  # noqa: F401  (go is a fixture)
from tests.modules.test_kitchen_slips import add_round, printed, slip


async def test_a_full_table_service_on_the_slip_plan(client, db, go, waiter, manager, kitchen, table):
    await go(SLIP)
    curry = await factories.make_menu_item(db, name="Curry", price="200.00")
    kebab = await factories.make_menu_item(db, name="Kebab", price="300.00", station="grill",
                                           category=await factories.make_category(db, name="Grill"))
    naan = await factories.make_menu_item(db, name="Naan", price="50.00")

    # Round 1: one slip for the whole table, both stations on it, not printed yet.
    order = await create_order(client, waiter, table, items=[line(curry, 2), line(kebab)], guest_count=2)
    kots = await fire(client, waiter, order["id"])
    [first] = kots
    assert (first["fulfilment"], first["status"], first["print_count"], len(first["items"])) == ("slip", "queued", 0, 2)

    # The kitchen has no screen on this plan.
    assert (await client.get(f"{API}/kots", headers=auth_headers(kitchen))).status_code == 403

    # Print it; the printer jams, so the waiter reprints it.
    s = await slip(client, waiter, first["id"], format="text")
    assert s.status_code == 200 and "NEW ORDER" in s.text and "[ GRILL ]" in s.text and "[ KITCHEN ]" in s.text
    assert (await printed(client, waiter, first["id"])).json()["print_count"] == 1
    assert (await slip(client, waiter, first["id"])).json()["marker"] == "REPRINT"
    assert (await printed(client, waiter, first["id"])).json()["print_count"] == 2
    assert (await slip(client, waiter, first["id"], format="escpos")).status_code == 200

    # Round 2: naan, marked ADDITIONAL at the kitchen station.
    order = (await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))).json()
    await add_round(client, waiter, order, naan)
    [naan_kot] = await fire(client, waiter, order["id"])
    assert (await slip(client, waiter, naan_kot["id"])).json()["marker"] == "ADDITIONAL"
    await printed(client, waiter, naan_kot["id"])

    # Guest cancels the naan: needs the manager PIN, and a VOID slip tells the kitchen to stop.
    r = await client.post(f"{API}/kots/{naan_kot['id']}/void", json={"void_reason": "guest changed mind"}, headers=auth_headers(waiter))
    assert r.status_code == 403
    r = await client.post(f"{API}/kots/{naan_kot['id']}/void", json={"void_reason": "guest changed mind", "approver_pin": "9001"},
                          headers=auth_headers(waiter))
    assert r.status_code == 200, r.text
    v = (await slip(client, waiter, naan_kot["id"])).json()
    assert v["marker"] == "VOID" and "Take the original slip back" in v["text"]

    # Kitchen stages do not exist on paper; the waiter marks food served straight from queued.
    assert (await set_status(client, waiter, first["id"], "preparing")).status_code == 403
    for k in kots:
        r = await set_status(client, waiter, k["id"], "served")
        assert r.status_code == 200 and r.json()["status"] == "served", r.text

    # The waiter bills and takes cash without a cashier; the voided naan is not charged.
    b = await bill(client, waiter, order["id"])
    assert b["subtotal"] == "700.00", b
    r = await pay(client, waiter, b["id"], b["grand_total"])
    assert r.status_code == 200, r.text
    assert r.json()["bill"]["status"] == "paid"
    done = (await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))).json()
    assert done["status"] == "completed"

    # But voiding the paid bill is still not a waiter's call.
    r = await client.post(f"{API}/billing/bills/{b['id']}/void", json={"reason": "x", "approver_pin": "9001"}, headers=auth_headers(waiter))
    assert r.status_code == 403

    # Every reprint left a trace.
    reprints = (await db.scalars(select(AuditLog).where(AuditLog.action_type == "kot_reprint"))).all()
    assert [str(a.entity_id) for a in reprints] == [first["id"]]
    assert manager.id


async def test_switching_plans_in_the_middle_of_service(client, db, go, waiter, kitchen, cashier, table, table2):
    dish = await factories.make_menu_item(db, name="Dish")

    # Table 1 is fired on paper, then the restaurant upgrades to the kitchen screen mid-service.
    await go(SLIP)
    paper_order = await create_order(client, waiter, table, items=[line(dish)])
    [paper] = await fire(client, waiter, paper_order["id"])
    await printed(client, waiter, paper["id"])
    await go(SCREEN)

    # Table 2 goes to the screen; the paper ticket never shows up there (it would be cooked twice).
    screen_order = await create_order(client, waiter, table2, items=[line(dish)])
    [screen] = await fire(client, waiter, screen_order["id"])
    assert screen["fulfilment"] == "screen"
    board = [k["id"] for k in (await client.get(f"{API}/kots", headers=auth_headers(kitchen))).json()]
    assert board == [screen["id"]]

    # The kitchen runs its ticket through the stages; the paper one is still closed by the floor.
    for st in ("preparing", "ready"):
        assert (await set_status(client, kitchen, screen["id"], st)).status_code == 200
    assert (await set_status(client, waiter, screen["id"], "served")).status_code == 200
    assert (await set_status(client, waiter, paper["id"], "served")).status_code == 200

    # Billing is back with the cashier: the waiter lost it the moment the plan changed.
    assert (await client.post(f"{API}/billing/bills", json={"order_id": paper_order["id"]}, headers=idem(waiter))).status_code == 403
    for o in (paper_order, screen_order):
        b = await bill(client, cashier, o["id"])
        assert (await pay(client, cashier, b["id"], b["grand_total"])).status_code == 200
