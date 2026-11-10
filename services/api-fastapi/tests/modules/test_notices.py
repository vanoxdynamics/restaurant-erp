"""Floor notices: the events a waiter's phone announces, and who hears about what."""

from datetime import timedelta

from app.core.clock import utcnow
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line, pay
from tests.modules.test_kitchen_module import SLIP, set_modules

URL = f"{API}/notices"


async def _since(client, user) -> str:
    first = await client.get(URL, headers=auth_headers(user))
    assert first.status_code == 200 and first.json()["notices"] == []  # the first ask only sets the clock
    return first.json()["now"]


async def _kinds(client, user, since) -> dict:
    r = await client.get(URL, params={"since": since}, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return {n["kind"]: n for n in r.json()["notices"]}


async def test_waiter_hears_about_their_own_orders(client, db, branch, waiter, captain, kitchen, cashier, manager, table, table2):
    since_waiter = await _since(client, waiter)
    since_captain = await _since(client, captain)
    dal = await factories.make_menu_item(db, name="Dal", price="200.00")
    paneer = await factories.make_menu_item(db, name="Paneer", price="300.00")

    mine = await create_order(client, waiter, table, items=[line(dal, 2)])
    theirs = await create_order(client, captain, table2, items=[line(dal)])
    my_kot = (await fire(client, waiter, mine["id"]))[0]
    their_kot = (await fire(client, captain, theirs["id"]))[0]
    for kot in (my_kot, their_kot):
        for status in ("preparing", "ready"):
            r = await client.patch(f"{API}/kots/{kot['id']}/status", json={"status": status}, headers=auth_headers(kitchen))
            assert r.status_code == 200, r.text

    # Paying the waiter's table; and the kitchen running out of paneer.
    b = await bill(client, cashier, mine["id"])
    assert (await pay(client, cashier, b["id"], b["grand_total"])).status_code == 200
    r = await client.patch(f"{API}/catalog/items/{paneer.id}/toggle-86", json={"is_available_86": False}, headers=auth_headers(kitchen))
    assert r.status_code == 200

    waiter_view = await _kinds(client, waiter, since_waiter)
    assert waiter_view["food_ready"]["title"] == "Ready · Table T1" and waiter_view["food_ready"]["detail"] == "2 × Dal"
    assert waiter_view["bill_paid"]["title"] == "Paid · Table T1"
    assert waiter_view["sold_out"]["title"] == "Paneer is sold out"
    r = await client.get(URL, params={"since": since_waiter}, headers=auth_headers(waiter))
    assert all("T2" not in n["title"] for n in r.json()["notices"])  # not the captain's table

    # The captain watches the whole floor.
    r = await client.get(URL, params={"since": since_captain}, headers=auth_headers(captain))
    ready_titles = {n["title"] for n in r.json()["notices"] if n["kind"] == "food_ready"}
    assert ready_titles == {"Ready · Table T1", "Ready · Table T2"}


async def test_cancel_answers_kitchen_voids_and_printed_slips(client, db, redis_client, branch, waiter, manager, cashier, kitchen, table, table2):
    since = await _since(client, waiter)
    dal = await factories.make_menu_item(db, name="Dal", price="200.00")

    # A cancel request the manager declines.
    order = await create_order(client, waiter, table, items=[line(dal)])
    kot = (await fire(client, waiter, order["id"]))[0]
    req = await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "guest changed mind"}, headers=auth_headers(waiter))
    assert req.status_code == 201, req.text
    r = await client.post(f"{API}/approvals/void-requests/{req.json()['id']}/reject", json={"note": "already cooking"},
                          headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    kinds = await _kinds(client, waiter, since)
    assert kinds["cancel_declined"]["detail"].endswith("already cooking")

    # Without the kitchen screen a KOT prints at the counter: the waiter is told to collect it.
    await set_modules(db, redis_client, branch.organization_id, SLIP)
    order2 = await create_order(client, waiter, table2, items=[line(dal)])
    slip = (await fire(client, waiter, order2["id"]))[0]
    assert (await client.post(f"{API}/kots/{slip['id']}/slip/printed", headers=idem(cashier))).status_code == 200
    kinds = await _kinds(client, waiter, since)
    assert kinds["kot_printed"]["title"].endswith("· Table T2")


async def test_old_events_are_not_replayed_and_kitchen_staff_have_no_notices(client, branch, waiter, kitchen):
    long_ago = (utcnow() - timedelta(days=2)).isoformat()
    assert (await client.get(URL, params={"since": long_ago}, headers=auth_headers(waiter))).status_code == 200
    assert (await client.get(URL, headers=auth_headers(kitchen))).status_code == 403
