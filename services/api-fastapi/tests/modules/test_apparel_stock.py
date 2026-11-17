"""Dress shops, stage A2: stock per variant and branch, deliveries, adjustments, counts, the ledger, opening stock on import."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text

from app.models.apparel import ApparelStock, ApparelStockMovement
from tests.factories import auth_headers, idem
from tests.modules.apparel_helpers import A, category, make_shop, product
from tests.modules.test_rls import count, probe  # noqa: F401  (fixture)


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


async def style(client, shop, **kw):
    cat = await category(client, shop["owner"], kw.pop("category_name", "Shirts"))
    return await product(client, shop["owner"], cat["id"], **kw)


async def receive(client, user, lines, key=None, **extra):
    body = {"supplier_name": "Arvind Mills", "invoice_number": "INV-1", "lines": lines, **extra}
    return await client.post(f"{A}/purchases", json=body, headers=idem(user, key))


async def stock_of(client, user, variant_id, **params):
    r = await client.get(f"{A}/stock", params=params, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return next(row for row in r.json() if row["variant_id"] == variant_id)


async def assert_ledger_matches(db, branch_id):
    """Every stock row equals the sum of its ledger, and the last balance_after is the quantity."""
    levels = (await db.scalars(select(ApparelStock).where(ApparelStock.branch_id == branch_id)
                               .execution_options(populate_existing=True))).all()
    for level in levels:
        moves = (await db.scalars(select(ApparelStockMovement).where(ApparelStockMovement.variant_id == level.variant_id,
                                                                      ApparelStockMovement.branch_id == branch_id)
                                  .order_by(ApparelStockMovement.created_at, ApparelStockMovement.id))).all()
        assert sum(m.quantity for m in moves) == level.quantity
        if moves:
            assert moves[-1].balance_after == level.quantity


async def test_delivery_adjustment_and_count_give_the_right_balances_and_ledger(client, db, shop):
    made = await style(client, shop, sizes=["S", "M"])
    s, m = made["variants"]
    r = await receive(client, shop["manager"], [{"variant_id": s["id"], "quantity": 10, "unit_cost": "420.00"},
                                                {"variant_id": m["id"], "quantity": 6}])
    assert r.status_code == 201, r.text
    purchase = r.json()
    assert purchase["total_cost"] == "4200.00" and len(purchase["lines"]) == 2
    assert {line["size"]: line["quantity"] for line in purchase["lines"]} == {"S": 10, "M": 6}
    assert (await stock_of(client, shop["owner"], s["id"]))["quantity"] == 10

    r = await client.post(f"{A}/adjustments", json={"variant_id": s["id"], "delta": -2, "reason": "Two damaged in the rain"},
                          headers=idem(shop["manager"]))
    assert r.status_code == 200 and r.json()["quantity"] == 8
    r = await client.post(f"{A}/adjustments", json={"variant_id": s["id"], "counted": 7, "reason": "Monthly count"},
                          headers=idem(shop["manager"]))
    assert r.status_code == 200 and r.json()["quantity"] == 7
    # A count that matches the record changes nothing and writes no ledger row.
    r = await client.post(f"{A}/adjustments", json={"variant_id": s["id"], "counted": 7, "reason": "Recount"},
                          headers=idem(shop["manager"]))
    assert r.json()["quantity"] == 7

    r = await client.get(f"{A}/movements", params={"variant_id": s["id"]}, headers=auth_headers(shop["manager"]))
    ledger = r.json()["items"]
    assert [(e["kind"], e["quantity"], e["balance_after"]) for e in ledger] == [
        ("count", -1, 7), ("adjustment", -2, 8), ("purchase", 10, 10)]
    assert ledger[2]["unit_cost"] == "420.00" and ledger[1]["reason"] == "Two damaged in the rain"
    kinds = (await client.get(f"{A}/movements", params={"kind": "purchase"}, headers=auth_headers(shop["owner"]))).json()
    assert kinds["total"] == 2
    await assert_ledger_matches(db, shop["branch"].id)


async def test_cost_is_hidden_from_cashiers(client, shop):
    made = await style(client, shop, sizes=["M"])
    v = made["variants"][0]
    await receive(client, shop["owner"], [{"variant_id": v["id"], "quantity": 4, "unit_cost": "300.00"}])
    manager_row = await stock_of(client, shop["manager"], v["id"])
    assert manager_row["cost_price"] == "300.00" and manager_row["stock_value"] == "1200.00"
    cashier_row = await stock_of(client, shop["cashier"], v["id"])
    assert cashier_row["quantity"] == 4 and cashier_row["cost_price"] is None and cashier_row["stock_value"] is None
    # Deliveries and the ledger carry costs: managers only.
    cashier = auth_headers(shop["cashier"])
    assert (await client.get(f"{A}/purchases", headers=cashier)).status_code == 403
    assert (await client.get(f"{A}/movements", headers=cashier)).status_code == 403
    for method, url, body in [
        ("POST", f"{A}/purchases", {"lines": [{"variant_id": v["id"], "quantity": 1}]}),
        ("POST", f"{A}/adjustments", {"variant_id": v["id"], "delta": 1, "reason": "found one"}),
        ("PUT", f"{A}/stock/{v['id']}", {"low_stock_level": 2}),
    ]:
        assert (await client.request(method, url, json=body, headers=idem(shop["cashier"]))).status_code == 403


async def test_stock_list_includes_unstocked_variants_and_filters(client, shop):
    shirt = await style(client, shop, name="Oxford Shirt", sizes=["S", "M"])
    s, m = shirt["variants"]
    await receive(client, shop["owner"], [{"variant_id": s["id"], "quantity": 5}])
    r = await client.put(f"{A}/stock/{s['id']}", json={"low_stock_level": 5, "cost_price": "410.00"},
                         headers=auth_headers(shop["owner"]))
    assert r.status_code == 200 and r.json()["is_low"] is True and r.json()["cost_price"] == "410.00"
    rows = (await client.get(f"{A}/stock", headers=auth_headers(shop["cashier"]))).json()
    assert {(row["size"], row["quantity"]) for row in rows} == {("S", 5), ("M", 0)}
    assert next(row for row in rows if row["size"] == "M")["is_out"] is True
    low = (await client.get(f"{A}/stock", params={"low_only": True}, headers=auth_headers(shop["owner"]))).json()
    assert {row["size"] for row in low} == {"S", "M"}
    found = (await client.get(f"{A}/stock", params={"search": s["barcode"]}, headers=auth_headers(shop["owner"]))).json()
    assert [row["variant_id"] for row in found] == [s["id"]]
    # The catalogue shows the same stock.
    r = await client.get(f"{A}/products/{shirt['id']}", params={"branch_id": str(shop["branch"].id)},
                         headers=auth_headers(shop["owner"]))
    assert {v["size"]: v["on_hand"] for v in r.json()["variants"]} == {"S": 5, "M": 0}
    r = await client.get(f"{A}/variants/lookup", params={"code": s["barcode"], "branch_id": str(shop["branch"].id)},
                         headers=auth_headers(shop["cashier"]))
    assert r.json()["variant"]["on_hand"] == 5


async def test_stock_is_per_branch(client, db, shop):
    from tests import factories
    second = await factories.make_branch(db, code="KF2", name="Second", org=shop["org"])
    made = await style(client, shop, sizes=["M"])
    v = made["variants"][0]
    await receive(client, shop["owner"], [{"variant_id": v["id"], "quantity": 3}], branch_id=str(second.id))
    assert (await stock_of(client, shop["owner"], v["id"], branch_id=str(second.id)))["quantity"] == 3
    assert (await stock_of(client, shop["owner"], v["id"]))["quantity"] == 0  # the owner's home branch
    # The cashier works in the first branch only.
    r = await client.get(f"{A}/stock", params={"branch_id": str(second.id)}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403


async def test_idempotent_purchases_and_adjustments(client, db, shop):
    made = await style(client, shop, sizes=["M"])
    v = made["variants"][0]
    first = await receive(client, shop["owner"], [{"variant_id": v["id"], "quantity": 5}], key="delivery-1")
    again = await receive(client, shop["owner"], [{"variant_id": v["id"], "quantity": 5}], key="delivery-1")
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert again.headers.get("Idempotent-Replay") == "true"
    body = {"variant_id": v["id"], "delta": -1, "reason": "damaged"}
    for _ in range(2):
        r = await client.post(f"{A}/adjustments", json=body, headers=idem(shop["owner"], "adjustment-1"))
        assert r.status_code == 200, r.text
    assert (await stock_of(client, shop["owner"], v["id"]))["quantity"] == 4
    assert await db.scalar(select(func.count()).select_from(ApparelStockMovement)) == 2
    # Without a key the write is refused.
    r = await client.post(f"{A}/adjustments", json=body, headers=auth_headers(shop["owner"]))
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_key_required"


async def test_concurrent_moves_keep_the_ledger_consistent(client, db, shop):
    made = await style(client, shop, sizes=["S", "M", "L"])
    ids = [v["id"] for v in made["variants"]]

    async def deliver(order):
        return await receive(client, shop["owner"], [{"variant_id": i, "quantity": 2} for i in order])

    results = await asyncio.gather(*(deliver(ids if n % 2 else list(reversed(ids))) for n in range(6)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    for i in ids:
        assert (await stock_of(client, shop["owner"], i))["quantity"] == 12
    await assert_ledger_matches(db, shop["branch"].id)


async def test_validation(client, shop):
    made = await style(client, shop, sizes=["M"])
    v = made["variants"][0]
    bad = [
        ("POST", f"{A}/purchases", {"lines": []}),
        ("POST", f"{A}/purchases", {"lines": [{"variant_id": v["id"], "quantity": 0}]}),
        ("POST", f"{A}/purchases", {"lines": [{"variant_id": v["id"], "quantity": 1.5}]}),
        ("POST", f"{A}/adjustments", {"variant_id": v["id"], "reason": "no amount"}),
        ("POST", f"{A}/adjustments", {"variant_id": v["id"], "delta": 1, "counted": 2, "reason": "both"}),
        ("POST", f"{A}/adjustments", {"variant_id": v["id"], "delta": 0, "reason": "zero"}),
        ("POST", f"{A}/adjustments", {"variant_id": v["id"], "delta": 1, "reason": ""}),
        ("POST", f"{A}/adjustments", {"variant_id": v["id"], "counted": -1, "reason": "negative count"}),
    ]
    for method, url, body in bad:
        r = await client.request(method, url, json=body, headers=idem(shop["owner"]))
        assert r.status_code == 422, (body, r.text)


async def test_another_business_cannot_touch_the_stock(client, shop, other):
    made = await style(client, shop, sizes=["M"])
    v = made["variants"][0]
    purchase = (await receive(client, shop["owner"], [{"variant_id": v["id"], "quantity": 2}])).json()
    them = other["owner"]
    assert (await receive(client, them, [{"variant_id": v["id"], "quantity": 1}])).status_code == 404
    r = await client.post(f"{A}/adjustments", json={"variant_id": v["id"], "delta": 1, "reason": "theirs"}, headers=idem(them))
    assert r.status_code == 404
    assert (await client.put(f"{A}/stock/{v['id']}", json={"low_stock_level": 1}, headers=auth_headers(them))).status_code == 404
    assert (await client.get(f"{A}/purchases/{purchase['id']}", headers=auth_headers(them))).status_code == 404
    r = await client.get(f"{A}/stock", params={"branch_id": str(shop["branch"].id)}, headers=auth_headers(them))
    assert r.status_code == 403
    assert (await client.get(f"{A}/stock", headers=auth_headers(them))).json() == []


async def test_rls_covers_the_stock_tables(client, db, probe, shop, other):  # noqa: F811
    for s in (shop, other):
        made = await style(client, s, sizes=["M"])
        await receive(client, s["owner"], [{"variant_id": made["variants"][0]["id"], "quantity": 1}])
    async with probe() as session:
        for table in ("apparel_stock", "apparel_purchases", "apparel_stock_movements"):
            everything = await count(session, table)
            await session.execute(text("RESET app.org_scope"))
            mine = await count(session, table, shop["org"].id)
            theirs = await count(session, table, other["org"].id)
            assert 0 < mine < everything and 0 < theirs < everything and mine + theirs == everything, table
            await session.execute(text("RESET app.org_scope"))


# --- opening stock on import -------------------------------------------------------------------------------------------------

HEADER = "style_code,name,category,size,colour,price,opening_stock"


async def test_import_sets_opening_stock_for_new_variants_only(client, db, shop):
    owner = auth_headers(shop["owner"])
    branch = str(shop["branch"].id)
    csv = f"{HEADER}\nOX-1,Oxford Shirt,Shirts,S,Blue,999,5\nOX-1,Oxford Shirt,Shirts,M,Blue,999,\n"
    r = await client.post(f"{A}/import", json={"csv": csv, "dry_run": True}, headers=owner)
    assert r.json()["errors"][0]["errors"] == ["opening_stock needs a branch: choose the branch the stock is in"]

    r = await client.post(f"{A}/import", json={"csv": csv, "branch_id": branch}, headers=owner)
    assert r.json()["errors"] == [] and r.json()["variants_created"] == 2
    rows = {row["size"]: row for row in (await client.get(f"{A}/stock", headers=owner)).json()}
    assert rows["S"]["quantity"] == 5 and rows["M"]["quantity"] == 0
    ledger = (await client.get(f"{A}/movements", headers=owner)).json()["items"]
    assert [(e["kind"], e["quantity"], e["reason"]) for e in ledger] == [("count", 5, "Opening stock (import)")]

    # Importing again with stock does not touch the variants that already exist.
    csv2 = f"{HEADER}\nOX-1,Oxford Shirt,Shirts,S,Blue,999,50\nOX-1,Oxford Shirt,Shirts,L,Blue,999,3\n"
    r = await client.post(f"{A}/import", json={"csv": csv2, "branch_id": branch}, headers=owner)
    assert r.json()["variants_created"] == 1 and r.json()["variants_updated"] == 1
    rows = {row["size"]: row for row in (await client.get(f"{A}/stock", headers=owner)).json()}
    assert rows["S"]["quantity"] == 5 and rows["L"]["quantity"] == 3
    # A dry run saves no stock.
    csv3 = f"{HEADER}\nNEW-1,New Tee,Tees,M,Red,499,9\n"
    r = await client.post(f"{A}/import", json={"csv": csv3, "branch_id": branch, "dry_run": True}, headers=owner)
    assert r.json()["variants_created"] == 1
    assert await db.scalar(select(func.count()).select_from(ApparelStockMovement)) == 2
    await assert_ledger_matches(db, shop["branch"].id)


async def test_import_stock_must_be_a_whole_number(client, shop):
    csv = f"{HEADER}\n,Tee,Tees,M,Red,499,2.5\n,Tee,Tees,L,Red,499,-1\n"
    r = await client.post(f"{A}/import", json={"csv": csv, "branch_id": str(shop["branch"].id)},
                          headers=auth_headers(shop["owner"]))
    assert [e["row"] for e in r.json()["errors"]] == [2, 3]


async def test_unknown_variant_is_404(client, shop):
    r = await receive(client, shop["owner"], [{"variant_id": str(uuid.uuid4()), "quantity": 1}])
    assert r.status_code == 404 and r.json()["error"]["code"] == "variant_not_found"
