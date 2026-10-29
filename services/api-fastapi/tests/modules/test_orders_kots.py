import asyncio
import re
import uuid

from sqlalchemy import func, select

from app.models import KOT, AuditLog, DiningTable
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, create_order, fire, line


async def test_create_dine_in_order(client, db, waiter, table, events):
    item = await factories.make_menu_item(db, price="250.00")
    order = await create_order(client, waiter, table, items=[line(item, 2, notes="no onion")])
    assert re.fullmatch(r"ORD-BLR1-\d{8}-0001", order["order_number"])
    assert order["subtotal"] == "500.00" and order["version"] == 1
    assert order["items"][0]["name_snapshot"] == "Paneer Tikka"
    assert order["items"][0]["station_snapshot"] == "kitchen"
    assert (await db.get(DiningTable, table.id, populate_existing=True)).status == "occupied"
    assert {e["type"] for e in events} >= {"order.created", "table.status_changed"}

    second = await client.post(f"{API}/orders", headers=idem(waiter),
                               json={"order_type": "dine_in", "table_id": str(table.id)})
    assert second.status_code == 409 and second.json()["error"]["code"] == "table_not_vacant"


async def test_item_validation(client, db, waiter):
    eighty_sixed = await factories.make_menu_item(db, name="Gone", available=False)
    varianted = await factories.make_menu_item(db, name="Biryani", variants=[("Half", "180.00"), ("Full", "300.00")])
    other = await factories.make_menu_item(db, name="Other", variants=[("Large", "99.00")])
    group = await factories.make_modifier_group(db, max_selection=1)
    with_mods = await factories.make_menu_item(db, name="Curry", modifier_groups=[group])

    async def attempt(*lines):
        return await client.post(f"{API}/orders", headers=idem(waiter), json={"order_type": "takeaway", "items": list(lines)})

    assert (await attempt(line(eighty_sixed))).json()["error"]["code"] == "item_unavailable"
    assert (await attempt(line(varianted))).json()["error"]["code"] == "variant_required"
    assert (await attempt(line(varianted, variant_id=str(other.variants[0].id)))).json()["error"]["code"] == "variant_unavailable"
    two = [str(m.id) for m in group.modifiers]
    assert (await attempt(line(with_mods, modifier_ids=two))).json()["error"]["code"] == "invalid_modifiers"
    assert (await attempt(line(with_mods, modifier_ids=[str(uuid.uuid4())]))).json()["error"]["code"] == "invalid_modifiers"

    hot = next(m for m in group.modifiers if m.name == "Extra Hot")
    half = next(v for v in varianted.variants if v.name == "Half")
    ok = await attempt(line(varianted, 2, variant_id=str(half.id)), line(with_mods, modifier_ids=[str(hot.id)]))
    assert ok.status_code == 201, ok.text
    lines = {i["name_snapshot"]: i for i in ok.json()["items"]}
    assert lines["Biryani"]["variant_name_snapshot"] == "Half" and lines["Biryani"]["line_total"] == "360.00"
    assert lines["Curry"]["line_total"] == "270.00"  # 250 + 20 modifier
    assert ok.json()["subtotal"] == "630.00"


async def test_optimistic_locking(client, db, waiter):
    item = await factories.make_menu_item(db)
    order = await create_order(client, waiter)
    stale = await client.patch(f"{API}/orders/{order['id']}", json={"version": 99, "guest_count": 3},
                               headers=auth_headers(waiter))
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current_version"] == 1

    ok = await client.post(f"{API}/orders/{order['id']}/items", json={"version": 1, "items": [line(item)]},
                           headers=auth_headers(waiter))
    assert ok.status_code == 200 and ok.json()["version"] == 2
    again = await client.post(f"{API}/orders/{order['id']}/items", json={"version": 1, "items": [line(item)]},
                              headers=auth_headers(waiter))
    assert again.status_code == 409


async def test_fire_splits_by_station(client, db, waiter, table, events):
    food = await factories.make_menu_item(db, name="Dal", station="kitchen")
    naan = await factories.make_menu_item(db, name="Butter Naan", station="grill", price="90.00")
    order = await create_order(client, waiter, table, items=[line(food), line(naan, 2)])
    kots = await fire(client, waiter, order["id"])
    assert sorted(k["station"] for k in kots) == ["grill", "kitchen"]
    assert len({k["kot_number"] for k in kots}) == 2
    assert all(re.fullmatch(r"KOT-BLR1-\d{8}-000[12]", k["kot_number"]) for k in kots)
    grill = next(k for k in kots if k["station"] == "grill")
    assert grill["items"][0]["item_name"] == "Butter Naan" and grill["items"][0]["quantity"] == 2
    assert sum(1 for e in events if e["type"] == "kot.fired") == 2

    refreshed = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert {i["status"] for i in refreshed.json()["items"]} == {"fired"}
    nothing = await client.post(f"{API}/kots/fire", json={"order_id": order["id"]}, headers=idem(waiter))
    assert nothing.status_code == 409 and nothing.json()["error"]["code"] == "nothing_to_fire"


async def test_concurrent_fires_create_one_set(client, db, waiter):
    item = await factories.make_menu_item(db)
    order = await create_order(client, waiter, items=[line(item)])
    results = await asyncio.gather(*(
        client.post(f"{API}/kots/fire", json={"order_id": order["id"]}, headers=idem(waiter)) for _ in range(3)
    ))
    assert sorted(r.status_code for r in results) == [201, 409, 409]
    assert await db.scalar(select(func.count(KOT.id))) == 1


async def test_kot_status_flow(client, db, waiter, kitchen):
    item = await factories.make_menu_item(db)
    order = await create_order(client, waiter, items=[line(item)])
    kot = (await fire(client, waiter, order["id"]))[0]
    url = f"{API}/kots/{kot['id']}/status"
    assert (await client.patch(url, json={"status": "preparing"}, headers=auth_headers(waiter))).status_code == 403
    skip = await client.patch(url, json={"status": "served"}, headers=auth_headers(kitchen))
    assert skip.status_code == 409
    for status in ("preparing", "ready", "served"):
        r = await client.patch(url, json={"status": status}, headers=auth_headers(kitchen))
        assert r.status_code == 200 and r.json()["status"] == status
    o = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert o.json()["items"][0]["status"] == "served"

    board = await client.get(f"{API}/kots", headers=auth_headers(kitchen))
    assert board.json() == []  # served tickets leave the default board


async def test_void_kot_requires_manager_approval(client, db, waiter, manager):
    item = await factories.make_menu_item(db, price="200.00")
    keep = await factories.make_menu_item(db, name="Grill item", station="grill", price="50.00")
    order = await create_order(client, waiter, items=[line(item), line(keep)])
    kots = await fire(client, waiter, order["id"])
    kitchen_kot = next(k for k in kots if k["station"] == "kitchen")
    url = f"{API}/kots/{kitchen_kot['id']}/void"

    denied = await client.post(url, json={"void_reason": "guest left"}, headers=auth_headers(waiter))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "approval_required"
    wrong = await client.post(url, json={"void_reason": "guest left", "approver_pin": "0000"}, headers=auth_headers(waiter))
    assert wrong.status_code == 403

    ok = await client.post(url, json={"void_reason": "guest left", "approver_pin": "9001"}, headers=auth_headers(waiter))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "voided"
    audit = await db.scalar(select(AuditLog).where(AuditLog.action_type == "kot_void"))
    assert audit.actor_id == waiter.id and audit.approver_id == manager.id and str(audit.amount) == "200.00"

    o = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert o.json()["subtotal"] == "50.00"
    again = await client.post(url, json={"void_reason": "again"}, headers=auth_headers(manager))
    assert again.status_code == 409


async def test_cancel_unfired_and_fired_items(client, db, waiter, manager):
    a = await factories.make_menu_item(db, name="A", price="100.00")
    b = await factories.make_menu_item(db, name="B", price="60.00")
    order = await create_order(client, waiter, items=[line(a)])
    await fire(client, waiter, order["id"])
    order = (await client.post(f"{API}/orders/{order['id']}/items", json={"version": order["version"], "items": [line(b)]},
                               headers=auth_headers(waiter))).json()
    fired = next(i for i in order["items"] if i["name_snapshot"] == "A")
    pending = next(i for i in order["items"] if i["name_snapshot"] == "B")

    r = await client.post(f"{API}/orders/{order['id']}/items/{pending['id']}/cancel", json={"reason": "changed mind"},
                          headers=auth_headers(waiter))
    assert r.status_code == 200 and r.json()["subtotal"] == "100.00"
    r = await client.post(f"{API}/orders/{order['id']}/items/{fired['id']}/cancel", json={"reason": "wrong dish"},
                          headers=auth_headers(waiter))
    assert r.status_code == 403
    r = await client.post(f"{API}/orders/{order['id']}/items/{fired['id']}/cancel", json={"reason": "wrong dish"},
                          headers=auth_headers(manager))
    assert r.status_code == 200 and r.json()["subtotal"] == "0.00"
    kots = await client.get(f"{API}/kots", params={"order_id": order["id"]}, headers=auth_headers(manager))
    assert kots.json()[0]["status"] == "voided"


async def test_cancel_order_frees_table(client, db, waiter, manager, table):
    item = await factories.make_menu_item(db)
    order = await create_order(client, waiter, table, items=[line(item)])
    await fire(client, waiter, order["id"])
    r = await client.post(f"{API}/orders/{order['id']}/cancel", json={"version": 1, "reason": "walked out"},
                          headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert (await db.get(DiningTable, table.id, populate_existing=True)).status == "vacant"
    kots = await client.get(f"{API}/kots", params={"order_id": order["id"]}, headers=auth_headers(manager))
    assert kots.json()[0]["status"] == "voided"
    assert (await client.post(f"{API}/orders/{order['id']}/items", json={"version": r.json()["version"], "items": [line(item)]},
                              headers=auth_headers(waiter))).status_code == 409


async def test_order_listing_is_scoped(client, db, waiter, other_branch):
    await create_order(client, waiter)
    other_waiter = await factories.make_staff(db, other_branch, pin="4040")
    await create_order(client, other_waiter)
    r = await client.get(f"{API}/orders", headers=auth_headers(waiter))
    assert r.json()["total"] == 1
    forbidden = await client.get(f"{API}/orders", params={"branch_id": str(other_branch.id)}, headers=auth_headers(waiter))
    assert forbidden.status_code == 403
