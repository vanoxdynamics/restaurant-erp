import uuid

from sqlalchemy import select

from app.models import AuditLog, DiningTable, Order
from tests import factories
from tests.factories import auth_headers
from tests.flows import API, create_order, line


async def test_create_item_with_variants_and_modifiers(client, db, manager, waiter):
    category = await factories.make_category(db)
    group = await factories.make_modifier_group(db)
    body = {
        "category_id": str(category.id), "name": "Biryani", "base_price": "300.00", "station": "kitchen",
        "variants": [{"name": "Half", "price": "180.00"}, {"name": "Full", "price": "300.00"}],
        "modifier_group_ids": [str(group.id)],
    }
    assert (await client.post(f"{API}/catalog/items", json=body, headers=auth_headers(waiter))).status_code == 403
    r = await client.post(f"{API}/catalog/items", json=body, headers=auth_headers(manager))
    assert r.status_code == 201, r.text
    item = r.json()
    assert {v["name"] for v in item["variants"]} == {"Half", "Full"}
    assert item["modifier_groups"][0]["modifiers"]

    listed = await client.get(f"{API}/catalog/items", params={"category_id": str(category.id)}, headers=auth_headers(waiter))
    assert listed.status_code == 200 and len(listed.json()) == 1
    all_items = await client.get(f"{API}/catalog/items", headers=auth_headers(waiter))
    assert all_items.status_code == 200 and len(all_items.json()) == 1


async def test_toggle_86_and_filters(client, db, branch, kitchen, waiter, events):
    item = await factories.make_menu_item(db)
    assert (await client.patch(f"{API}/catalog/items/{item.id}/toggle-86", json={"is_available_86": False},
                               headers=auth_headers(waiter))).status_code == 403
    r = await client.patch(f"{API}/catalog/items/{item.id}/toggle-86", json={"is_available_86": False},
                           headers=auth_headers(kitchen))
    assert r.status_code == 200 and r.json()["is_available_86"] is False
    assert any(e["type"] == "item.86_changed" and e["branch_id"] == str(branch.id) for e in events)
    available = await client.get(f"{API}/catalog/items", params={"available_only": True}, headers=auth_headers(waiter))
    assert available.json() == []


async def test_menu_etag(client, db, waiter, manager):
    await factories.make_menu_item(db)
    first = await client.get(f"{API}/catalog/menu", headers=auth_headers(waiter))
    assert first.status_code == 200 and first.json()["categories"][0]["items"]
    etag = first.headers["etag"]
    cached = await client.get(f"{API}/catalog/menu", headers=auth_headers(waiter, **{"If-None-Match": etag}))
    assert cached.status_code == 304
    r = await client.post(f"{API}/catalog/categories", json={"name": "Drinks"}, headers=auth_headers(manager))
    assert r.status_code == 201
    changed = await client.get(f"{API}/catalog/menu", headers=auth_headers(waiter, **{"If-None-Match": etag}))
    assert changed.status_code == 200


async def test_price_change_does_not_touch_existing_orders(client, db, manager, waiter):
    item = await factories.make_menu_item(db, price="100.00")
    order = await create_order(client, waiter, items=[line(item, 2)])
    r = await client.patch(f"{API}/catalog/items/{item.id}", json={"base_price": "150.00"}, headers=auth_headers(manager))
    assert r.status_code == 200
    again = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert again.json()["items"][0]["unit_price_snapshot"] == "100.00"
    assert again.json()["subtotal"] == "200.00"


async def test_branch_floor_table_setup(client, db, owner, manager, waiter, branch, other_branch):
    r = await client.post(f"{API}/floor/branches", json={"name": "New", "code": "del1"}, headers=auth_headers(owner))
    assert r.status_code == 201 and r.json()["code"] == "DEL1"
    dup = await client.post(f"{API}/floor/branches", json={"name": "Dup", "code": "DEL1"}, headers=auth_headers(owner))
    assert dup.status_code == 409
    assert (await client.post(f"{API}/floor/branches", json={"name": "x", "code": "XX1"},
                              headers=auth_headers(manager))).status_code == 403

    listed = await client.get(f"{API}/floor/branches", headers=auth_headers(manager))
    assert [b["code"] for b in listed.json()] == ["BLR1"]

    floor = await client.post(f"{API}/floor/branches/{branch.id}/floors", json={"name": "Rooftop"}, headers=auth_headers(manager))
    assert floor.status_code == 201
    floor_id = floor.json()["id"]
    for number in ("T10", "T2"):
        t = await client.post(f"{API}/floor/floors/{floor_id}/tables", json={"table_number": number}, headers=auth_headers(manager))
        assert t.status_code == 201, t.text
    dup_table = await client.post(f"{API}/floor/floors/{floor_id}/tables", json={"table_number": "T2"}, headers=auth_headers(manager))
    assert dup_table.status_code == 409

    floors = await client.get(f"{API}/floor/branches/{branch.id}/floors", headers=auth_headers(waiter))
    assert [t["table_number"] for t in floors.json()[0]["tables"]] == ["T2", "T10"]
    hidden = await client.get(f"{API}/floor/branches/{other_branch.id}/floors", headers=auth_headers(waiter))
    assert hidden.status_code == 404


async def test_transfer_table(client, db, waiter, table, table2):
    order = await create_order(client, waiter, table)
    r = await client.post(f"{API}/floor/tables/transfer", headers=auth_headers(waiter),
                          json={"source_table_id": str(table.id), "target_table_id": str(table2.id), "reason": "AC"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "occupied"
    table_id, table2_id = table.id, table2.id
    assert (await db.get(DiningTable, table_id, populate_existing=True)).status == "vacant"
    assert (await db.get(Order, uuid.UUID(order["id"]), populate_existing=True)).table_id == table2_id
    assert await db.scalar(select(AuditLog).where(AuditLog.action_type == "table_transfer"))

    # Back onto an occupied table fails.

    await create_order(client, waiter, table)
    blocked = await client.post(f"{API}/floor/tables/transfer", headers=auth_headers(waiter),
                                json={"source_table_id": str(table2_id), "target_table_id": str(table_id)})
    assert blocked.status_code == 409


async def test_transfer_across_branches_rejected(client, db, waiter, table, other_branch):
    other_floor = await factories.make_floor(db, other_branch)
    other_table = await factories.make_table(db, other_floor, number="X1")
    await create_order(client, waiter, table)
    r = await client.post(f"{API}/floor/tables/transfer", headers=auth_headers(waiter),
                          json={"source_table_id": str(table.id), "target_table_id": str(other_table.id)})
    assert r.status_code == 409


async def test_merge_and_unmerge(client, db, captain, waiter, floor, table, table2):
    t3 = await factories.make_table(db, floor, number="T3")
    table_id, table2_id, t3_id = table.id, table2.id, t3.id
    r = await client.post(f"{API}/floor/tables/merge", headers=auth_headers(captain),
                          json={"primary_table_id": str(table.id), "secondary_table_ids": [str(table2.id)]})
    assert r.status_code == 200, r.text
    assert (await db.get(DiningTable, table2_id, populate_existing=True)).merged_into_table_id == table_id

    # Can't seat a new order on a merged-away table.
    blocked = await client.post(f"{API}/orders", headers=factories.idem(waiter),
                                json={"order_type": "dine_in", "table_id": str(table2_id)})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "table_merged"

    await create_order(client, waiter, await db.get(DiningTable, t3_id, populate_existing=True))
    busy = await client.post(f"{API}/floor/tables/merge", headers=auth_headers(captain),
                             json={"primary_table_id": str(table_id), "secondary_table_ids": [str(t3_id)]})
    assert busy.status_code == 409

    r = await client.post(f"{API}/floor/tables/{table_id}/unmerge", headers=auth_headers(captain))
    assert r.status_code == 200
    assert (await db.get(DiningTable, table2_id, populate_existing=True)).merged_into_table_id is None
