"""Hardware shops, stage H2: stock in base units, deliveries in any unit, adjustments, counts and the ledger."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.hardware import HardwareStock, HardwareStockMovement
from tests.factories import auth_headers, idem
from tests.modules.hardware_helpers import H, cable, category, make_shop, product
from tests.modules.test_rls import count, probe  # noqa: F401  (fixture)

D = Decimal


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


async def wire(client, shop, **extra):
    cat = await category(client, shop["owner"])
    return await cable(client, shop["owner"], cat["id"], **extra)


async def screws(client, shop, **extra):
    """Wood screws counted in whole pieces, bought by the box of 100."""
    cat = await category(client, shop["owner"], "Fasteners")
    return await product(client, shop["owner"], cat["id"], name="Wood Screw", size_spec="2 inch", base_unit="pcs", price="0.50",
                         units=[{"unit": "box", "factor": "100"}], **extra)


async def receive(client, user, lines, key=None, **extra):
    body = {"supplier_name": "Finolex", "invoice_number": "INV-1", "lines": lines, **extra}
    return await client.post(f"{H}/purchases", json=body, headers=idem(user, key))


async def stock_of(client, user, product_id, **params):
    r = await client.get(f"{H}/stock", params=params, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return next(row for row in r.json() if row["product_id"] == product_id)


async def assert_ledger_matches(db, branch_id):
    """Every stock row equals the sum of its ledger, and the last balance_after is the quantity."""
    levels = (await db.scalars(select(HardwareStock).where(HardwareStock.branch_id == branch_id)
                               .execution_options(populate_existing=True))).all()
    assert levels
    for level in levels:
        moves = (await db.scalars(select(HardwareStockMovement).where(HardwareStockMovement.product_id == level.product_id,
                                                                       HardwareStockMovement.branch_id == branch_id)
                                  .order_by(HardwareStockMovement.created_at, HardwareStockMovement.id))).all()
        assert sum((m.quantity for m in moves), D(0)) == level.quantity
        if moves:
            assert moves[-1].balance_after == level.quantity


# --- deliveries in any unit --------------------------------------------------------------------------------------------------


async def test_two_coils_add_180_metres_at_the_right_cost_per_metre(client, db, shop):
    made = await wire(client, shop)
    r = await receive(client, shop["manager"], [{"product_id": made["id"], "unit": "coil", "quantity": "2", "unit_cost": "3000.00"}])
    assert r.status_code == 201, r.text
    purchase = r.json()
    assert purchase["total_cost"] == "6000.00"
    [line] = purchase["lines"]
    assert (line["unit"], line["quantity"], line["unit_cost"], line["line_cost"]) == ("coil", 2, "3000.0000", "6000.00")
    assert (line["base_unit"], line["base_quantity"], line["base_unit_cost"]) == ("m", 180, "33.3333")

    row = await stock_of(client, shop["manager"], made["id"])
    assert row["quantity"] == 180 and row["base_unit"] == "m" and row["cost_price"] == "33.3333"
    assert row["stock_value"] == "5999.99"  # 180 x 33.3333: cost per metre is kept to 4 decimals, so a paisa is lost
    ledger = (await client.get(f"{H}/movements", params={"product_id": made["id"]}, headers=auth_headers(shop["manager"]))).json()
    assert [(e["kind"], e["quantity"], e["balance_after"], e["unit_cost"]) for e in ledger["items"]] == [
        ("purchase", 180, 180, "33.3333")]
    await assert_ledger_matches(db, shop["branch"].id)


async def test_mixed_units_in_one_delivery_and_fractions_of_a_unit(client, db, shop):
    made = await wire(client, shop)
    box = await screws(client, shop)
    r = await receive(client, shop["owner"], [
        {"product_id": made["id"], "unit": "bundle", "quantity": "3", "unit_cost": "400.00"},  # 30 m
        {"product_id": made["id"], "quantity": "12.5", "unit_cost": "41.00"},  # 12.5 m, no unit means the base unit
        {"product_id": made["id"], "unit": "coil", "quantity": "0.5"},  # 45 m, no cost given
        {"product_id": box["id"], "unit": "box", "quantity": "0.5", "unit_cost": "40.00"},  # 50 pcs
    ])
    assert r.status_code == 201, r.text
    assert r.json()["total_cost"] == "1732.50"  # 1200.00 + 512.50 + 0.00 + 20.00 (no cost on the half coil)
    assert (await stock_of(client, shop["owner"], made["id"]))["quantity"] == 87.5
    assert (await stock_of(client, shop["owner"], box["id"]))["quantity"] == 50
    # The cost of the last line that had one is the product's cost: 41.00 per metre.
    assert (await stock_of(client, shop["owner"], made["id"]))["cost_price"] == "41.0000"
    assert (await stock_of(client, shop["owner"], box["id"]))["cost_price"] == "0.4000"
    await assert_ledger_matches(db, shop["branch"].id)


async def test_a_unit_the_product_does_not_have_and_half_a_piece_are_refused(client, shop):
    box = await screws(client, shop)
    r = await receive(client, shop["owner"], [{"product_id": box["id"], "unit": "coil", "quantity": "1"}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "unit_not_available"
    # Half a box of 100 is fine; 0.005 of a box is half a screw.
    r = await receive(client, shop["owner"], [{"product_id": box["id"], "unit": "box", "quantity": "0.005"}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "whole_quantity_required"
    r = await receive(client, shop["owner"], [{"product_id": box["id"], "quantity": "1.5"}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "whole_quantity_required"
    assert (await stock_of(client, shop["owner"], box["id"]))["quantity"] == 0  # nothing was half-received


async def test_deliveries_are_listed_and_read_back(client, shop):
    made = await wire(client, shop)
    first = (await receive(client, shop["owner"], [{"product_id": made["id"], "unit": "coil", "quantity": 1, "unit_cost": "3000"}],
                           received_on="2026-11-01")).json()
    second = (await receive(client, shop["owner"], [{"product_id": made["id"], "quantity": 10}], received_on="2026-11-10")).json()
    r = await client.get(f"{H}/purchases", headers=auth_headers(shop["manager"]))
    assert [p["id"] for p in r.json()["items"]] == [second["id"], first["id"]] and r.json()["total"] == 2
    r = await client.get(f"{H}/purchases/{first['id']}", headers=auth_headers(shop["manager"]))
    assert r.status_code == 200 and r.json()["lines"][0]["unit"] == "coil" and r.json()["created_by"]


# --- adjustments and counts --------------------------------------------------------------------------------------------------


async def test_adjustment_and_count_in_base_units(client, db, shop):
    made = await wire(client, shop)
    await receive(client, shop["manager"], [{"product_id": made["id"], "unit": "coil", "quantity": 2, "unit_cost": "3000"}])
    r = await client.post(f"{H}/adjustments", json={"product_id": made["id"], "delta": "-2.5", "reason": "Cut and wasted"},
                          headers=idem(shop["manager"]))
    assert r.status_code == 200 and r.json()["quantity"] == 177.5
    r = await client.post(f"{H}/adjustments", json={"product_id": made["id"], "counted": "176", "reason": "Monthly count"},
                          headers=idem(shop["manager"]))
    assert r.status_code == 200 and r.json()["quantity"] == 176
    # A count that matches the record changes nothing and writes no ledger row.
    r = await client.post(f"{H}/adjustments", json={"product_id": made["id"], "counted": "176", "reason": "Recount"},
                          headers=idem(shop["manager"]))
    assert r.json()["quantity"] == 176
    ledger = (await client.get(f"{H}/movements", params={"product_id": made["id"]}, headers=auth_headers(shop["manager"]))).json()
    assert [(e["kind"], e["quantity"], e["balance_after"]) for e in ledger["items"]] == [
        ("count", -1.5, 176), ("adjustment", -2.5, 177.5), ("purchase", 180, 180)]
    assert ledger["items"][1]["reason"] == "Cut and wasted"
    assert (await client.get(f"{H}/movements", params={"kind": "purchase"}, headers=auth_headers(shop["owner"]))).json()["total"] == 1
    await assert_ledger_matches(db, shop["branch"].id)


async def test_counted_goods_must_be_whole_when_the_product_is_sold_whole(client, shop):
    box = await screws(client, shop)
    for body in ({"delta": "1.5"}, {"counted": "20.25"}):
        r = await client.post(f"{H}/adjustments", json={"product_id": box["id"], "reason": "found some", **body},
                              headers=idem(shop["owner"]))
        assert r.status_code == 422 and r.json()["error"]["code"] == "whole_quantity_required", body
    r = await client.post(f"{H}/adjustments", json={"product_id": box["id"], "counted": "20", "reason": "Counted the shelf"},
                          headers=idem(shop["owner"]))
    assert r.status_code == 200 and r.json()["quantity"] == 20


# --- reading, cost and roles -------------------------------------------------------------------------------------------------


async def test_cost_is_hidden_from_cashiers(client, shop):
    made = await wire(client, shop)
    await receive(client, shop["owner"], [{"product_id": made["id"], "unit": "coil", "quantity": 1, "unit_cost": "3000"}])
    manager_row = await stock_of(client, shop["manager"], made["id"])
    assert manager_row["cost_price"] == "33.3333" and manager_row["stock_value"] == "3000.00"
    cashier_row = await stock_of(client, shop["cashier"], made["id"])
    assert cashier_row["quantity"] == 90 and cashier_row["cost_price"] is None and cashier_row["stock_value"] is None
    # Deliveries and the ledger carry costs: managers only.
    cashier = auth_headers(shop["cashier"])
    assert (await client.get(f"{H}/purchases", headers=cashier)).status_code == 403
    assert (await client.get(f"{H}/movements", headers=cashier)).status_code == 403
    for method, url, body in [
        ("POST", f"{H}/purchases", {"lines": [{"product_id": made["id"], "quantity": 1}]}),
        ("POST", f"{H}/adjustments", {"product_id": made["id"], "delta": 1, "reason": "found one"}),
        ("PUT", f"{H}/stock/{made['id']}", {"low_stock_level": 2}),
    ]:
        assert (await client.request(method, url, json=body, headers=idem(shop["cashier"]))).status_code == 403


async def test_stock_list_includes_unstocked_products_low_stock_and_search(client, shop):
    made = await wire(client, shop)
    box = await screws(client, shop)
    await receive(client, shop["owner"], [{"product_id": made["id"], "quantity": 40}])
    r = await client.put(f"{H}/stock/{made['id']}", json={"low_stock_level": "50", "cost_price": "38.5"},
                         headers=auth_headers(shop["owner"]))
    assert r.status_code == 200, r.text
    assert r.json()["is_low"] is True and r.json()["cost_price"] == "38.5000" and r.json()["low_stock_level"] == 50
    rows = (await client.get(f"{H}/stock", headers=auth_headers(shop["cashier"]))).json()
    assert {(row["product_name"], row["quantity"]) for row in rows} == {("Copper Wire", 40), ("Wood Screw", 0)}
    assert next(row for row in rows if row["product_id"] == box["id"])["is_out"] is True
    assert {u["unit"] for u in next(row for row in rows if row["product_id"] == made["id"])["units"]} == {"coil", "bundle"}
    low = (await client.get(f"{H}/stock", params={"low_only": True}, headers=auth_headers(shop["owner"]))).json()
    assert {row["product_id"] for row in low} == {made["id"], box["id"]}
    await client.put(f"{H}/stock/{made['id']}", json={"low_stock_level": "10"}, headers=auth_headers(shop["owner"]))
    low = (await client.get(f"{H}/stock", params={"low_only": True}, headers=auth_headers(shop["owner"]))).json()
    assert {row["product_id"] for row in low} == {box["id"]}
    found = (await client.get(f"{H}/stock", params={"search": "screw 2"}, headers=auth_headers(shop["owner"]))).json()
    assert [row["product_id"] for row in found] == [box["id"]]
    found = (await client.get(f"{H}/stock", params={"search": "8901000000901"}, headers=auth_headers(shop["owner"]))).json()
    assert [row["product_id"] for row in found] == [made["id"]]  # a coil barcode finds the cable
    # A whole-piece product cannot have a fractional low-stock level.
    r = await client.put(f"{H}/stock/{box['id']}", json={"low_stock_level": "2.5"}, headers=auth_headers(shop["owner"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "whole_quantity_required"


async def test_the_counter_search_and_lookup_show_stock_for_a_branch(client, shop):
    made = await wire(client, shop)
    await receive(client, shop["owner"], [{"product_id": made["id"], "unit": "coil", "quantity": 1}])
    branch = {"branch_id": str(shop["branch"].id)}
    cashier = auth_headers(shop["cashier"])
    r = await client.get(f"{H}/catalog/search", params={"q": "copper", **branch}, headers=cashier)
    assert r.json()[0]["on_hand"] == 90
    r = await client.get(f"{H}/catalog/search", params={"q": "copper"}, headers=cashier)
    assert r.json()[0]["on_hand"] is None  # no branch named
    r = await client.get(f"{H}/products/lookup", params={"code": "8901000000901", **branch}, headers=cashier)
    assert r.json()["product"]["on_hand"] == 90 and r.json()["unit"]["unit"] == "coil"
    r = await client.get(f"{H}/products/{made['id']}", params=branch, headers=cashier)
    assert r.json()["on_hand"] == 90
    r = await client.get(f"{H}/products", params=branch, headers=cashier)
    assert r.json()["items"][0]["on_hand"] == 90


async def test_stock_is_per_branch(client, db, shop):
    from tests import factories
    second = await factories.make_branch(db, code="KF2", name="Second", org=shop["org"])
    made = await wire(client, shop)
    await receive(client, shop["owner"], [{"product_id": made["id"], "quantity": 30}], branch_id=str(second.id))
    assert (await stock_of(client, shop["owner"], made["id"], branch_id=str(second.id)))["quantity"] == 30
    assert (await stock_of(client, shop["owner"], made["id"]))["quantity"] == 0  # the owner's home branch
    # The cashier works in the first branch only.
    r = await client.get(f"{H}/stock", params={"branch_id": str(second.id)}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403
    r = await client.get(f"{H}/catalog/search", params={"branch_id": str(second.id)}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 403


# --- safety ------------------------------------------------------------------------------------------------------------------


async def test_idempotent_purchases_and_adjustments(client, db, shop):
    made = await wire(client, shop)
    line = [{"product_id": made["id"], "unit": "coil", "quantity": 1}]
    first = await receive(client, shop["owner"], line, key="delivery-1")
    again = await receive(client, shop["owner"], line, key="delivery-1")
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert again.headers.get("Idempotent-Replay") == "true"
    body = {"product_id": made["id"], "delta": "-1.25", "reason": "damaged"}
    for _ in range(2):
        r = await client.post(f"{H}/adjustments", json=body, headers=idem(shop["owner"], "adjustment-1"))
        assert r.status_code == 200, r.text
    assert (await stock_of(client, shop["owner"], made["id"]))["quantity"] == 88.75
    assert await db.scalar(select(func.count()).select_from(HardwareStockMovement)) == 2
    # Without a key the write is refused.
    r = await client.post(f"{H}/adjustments", json=body, headers=auth_headers(shop["owner"]))
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_key_required"


async def test_concurrent_deliveries_keep_the_ledger_consistent(client, db, shop):
    cat = await category(client, shop["owner"])
    ids = [(await product(client, shop["owner"], cat["id"], name=f"Item {n}", base_unit="m", price="10"))["id"] for n in range(3)]

    async def deliver(order):
        return await receive(client, shop["owner"], [{"product_id": i, "quantity": "2.5"} for i in order])

    results = await asyncio.gather(*(deliver(ids if n % 2 else list(reversed(ids))) for n in range(6)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    for i in ids:
        assert (await stock_of(client, shop["owner"], i))["quantity"] == 15
    await assert_ledger_matches(db, shop["branch"].id)


async def test_validation(client, shop):
    made = await wire(client, shop)
    pid = made["id"]
    bad = [
        ("POST", f"{H}/purchases", {"lines": []}),
        ("POST", f"{H}/purchases", {"lines": [{"product_id": pid, "quantity": 0}]}),
        ("POST", f"{H}/purchases", {"lines": [{"product_id": pid, "quantity": "-1"}]}),
        ("POST", f"{H}/purchases", {"lines": [{"product_id": pid, "quantity": "1.0001"}]}),
        ("POST", f"{H}/purchases", {"lines": [{"product_id": pid, "quantity": 1, "unit_cost": "-5"}]}),
        ("POST", f"{H}/purchases", {"lines": [{"product_id": pid, "quantity": 1, "unit": "bucket"}]}),
        ("POST", f"{H}/adjustments", {"product_id": pid, "reason": "no amount"}),
        ("POST", f"{H}/adjustments", {"product_id": pid, "delta": 1, "counted": 2, "reason": "both"}),
        ("POST", f"{H}/adjustments", {"product_id": pid, "delta": 0, "reason": "zero"}),
        ("POST", f"{H}/adjustments", {"product_id": pid, "delta": 1, "reason": ""}),
        ("POST", f"{H}/adjustments", {"product_id": pid, "counted": -1, "reason": "negative count"}),
        ("PUT", f"{H}/stock/{pid}", {"low_stock_level": -1}),
        ("PUT", f"{H}/stock/{pid}", {"cost_price": "-1"}),
    ]
    for method, url, body in bad:
        r = await client.request(method, url, json=body, headers=idem(shop["owner"]))
        assert r.status_code == 422, (body, r.text)


async def test_another_business_cannot_touch_the_stock(client, shop, other):
    made = await wire(client, shop)
    purchase = (await receive(client, shop["owner"], [{"product_id": made["id"], "quantity": 2}])).json()
    them = other["owner"]
    assert (await receive(client, them, [{"product_id": made["id"], "quantity": 1}])).status_code == 404
    r = await client.post(f"{H}/adjustments", json={"product_id": made["id"], "delta": 1, "reason": "theirs"}, headers=idem(them))
    assert r.status_code == 404
    assert (await client.put(f"{H}/stock/{made['id']}", json={"low_stock_level": 1}, headers=auth_headers(them))).status_code == 404
    assert (await client.get(f"{H}/purchases/{purchase['id']}", headers=auth_headers(them))).status_code == 404
    r = await client.get(f"{H}/stock", params={"branch_id": str(shop["branch"].id)}, headers=auth_headers(them))
    assert r.status_code == 403
    assert (await client.get(f"{H}/stock", headers=auth_headers(them))).json() == []


async def test_rls_covers_the_stock_tables(client, db, probe, shop, other):  # noqa: F811
    for s in (shop, other):
        made = await wire(client, s)
        await receive(client, s["owner"], [{"product_id": made["id"], "quantity": 1}])
    async with probe() as session:
        for table in ("hardware_stock", "hardware_purchases", "hardware_stock_movements"):
            everything = await count(session, table)
            await session.execute(text("RESET app.org_scope"))
            mine = await count(session, table, shop["org"].id)
            theirs = await count(session, table, other["org"].id)
            assert 0 < mine < everything and 0 < theirs < everything and mine + theirs == everything, table
            await session.execute(text("RESET app.org_scope"))
