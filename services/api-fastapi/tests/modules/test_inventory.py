"""
The `inventory` module: stock on hand per branch, purchases, adjustments and counts, the stock ledger, and paid bills taking
their items out of stock.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models import StockLevel, StockMovement
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, line, pay
from tests.modules.test_business_categories import counter_sale, make_item, set_category
from tests.modules.test_kitchen_module import set_modules

INV = f"{API}/inventory"


async def receive(client, user, lines, **extra):
    return await client.post(f"{INV}/purchases", json={"lines": lines, **extra}, headers=idem(user))


async def adjust(client, user, item, **body):
    return await client.post(f"{INV}/adjustments", json={"item_id": str(item.id), "reason": "stock check", **body},
                             headers=idem(user))


async def stock(client, user, **params) -> dict:
    r = await client.get(f"{INV}/stock", params=params, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return {row["name"]: row for row in r.json()}


async def sell(client, cashier, items) -> dict:
    order = await counter_sale(client, cashier, items)
    b = await bill(client, cashier, order["id"])
    r = await pay(client, cashier, b["id"], b["grand_total"])
    assert r.status_code == 200 and r.json()["bill"]["status"] == "paid", r.text
    return order


async def shop(db, redis_client, branch):
    await set_category(db, redis_client, branch.organization_id, "grocery")
    soap = await make_item(db, name="Soap", price="35.00", barcode="8901030000001")
    onion = await make_item(db, name="Onion", price="40.00", unit="kg", sold_by_weight=True)
    return soap, onion


# --- the module --------------------------------------------------------------------------------------------------------------


async def test_inventory_needs_the_module(client, db, redis_client, branch, manager):
    await set_modules(db, redis_client, branch.organization_id, ("pos",))
    r = await client.get(f"{INV}/stock", headers=auth_headers(manager))
    assert r.status_code == 403 and r.json()["error"]["details"] == {"module": "inventory"}
    await set_modules(db, redis_client, branch.organization_id, ("pos", "inventory"))
    assert (await client.get(f"{INV}/stock", headers=auth_headers(manager))).json() == []


async def test_only_managers_change_stock_and_cashiers_do_not_see_cost(client, db, redis_client, branch, manager, cashier):
    soap, _ = await shop(db, redis_client, branch)
    body = [{"item_id": str(soap.id), "quantity": 10, "unit_cost": "22.50"}]
    assert (await receive(client, cashier, body)).status_code == 403
    assert (await adjust(client, cashier, soap, delta=1)).status_code == 403
    assert (await client.get(f"{INV}/movements", headers=auth_headers(cashier))).status_code == 403
    assert (await receive(client, manager, body)).status_code == 201

    seen = (await stock(client, cashier))["Soap"]
    assert seen["quantity"] == 10 and seen["cost_price"] is None and seen["stock_value"] is None
    assert (await stock(client, manager))["Soap"]["stock_value"] == "225.00"


# --- purchases ---------------------------------------------------------------------------------------------------------------


async def test_a_purchase_adds_stock_and_sets_the_cost(client, db, redis_client, branch, manager):
    soap, onion = await shop(db, redis_client, branch)
    r = await receive(client, manager, [
        {"item_id": str(soap.id), "quantity": 48, "unit_cost": "22.50"},
        {"item_id": str(onion.id), "quantity": "25.500", "unit_cost": "18.00"},
    ], supplier_name="Sharma Traders", invoice_number="ST/118")
    assert r.status_code == 201, r.text
    purchase = r.json()
    assert purchase["total_cost"] == "1539.00" and purchase["supplier_name"] == "Sharma Traders"
    assert purchase["created_by"] == "Test Manager"
    assert {row["name"]: (row["quantity"], row["line_cost"]) for row in purchase["lines"]} == {
        "Soap": (48, "1080.00"), "Onion": (25.5, "459.00")}

    rows = await stock(client, manager)
    assert rows["Soap"]["quantity"] == 48 and rows["Soap"]["cost_price"] == "22.50" and rows["Soap"]["barcode"] == "8901030000001"
    assert rows["Onion"]["quantity"] == 25.5 and rows["Onion"]["unit"] == "kg"

    listed = (await client.get(f"{INV}/purchases", headers=auth_headers(manager))).json()
    assert listed["total"] == 1 and len(listed["items"][0]["lines"]) == 2
    one = await client.get(f"{INV}/purchases/{purchase['id']}", headers=auth_headers(manager))
    assert one.json()["invoice_number"] == "ST/118"


async def test_a_retried_purchase_is_not_received_twice(client, db, redis_client, branch, manager):
    soap, _ = await shop(db, redis_client, branch)
    body = {"lines": [{"item_id": str(soap.id), "quantity": 12}]}
    headers = idem(manager, "delivery-0001")
    first = await client.post(f"{INV}/purchases", json=body, headers=headers)
    again = await client.post(f"{INV}/purchases", json=body, headers=headers)
    assert first.status_code == again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert (await stock(client, manager))["Soap"]["quantity"] == 12
    assert (await client.post(f"{INV}/purchases", json=body, headers=auth_headers(manager))).status_code == 400  # no key


async def test_purchase_validation(client, db, redis_client, branch, manager):
    soap, _ = await shop(db, redis_client, branch)
    r = await receive(client, manager, [{"item_id": str(soap.id), "quantity": "1.5"}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "whole_quantity_required"
    assert (await receive(client, manager, [{"item_id": str(soap.id), "quantity": 0}])).status_code == 422
    assert (await receive(client, manager, [])).status_code == 422
    other = await factories.make_org(db, slug="other", name="Other")
    theirs = await factories.make_menu_item(db, category=await factories.make_category(db, org=other), name="Theirs")
    assert (await receive(client, manager, [{"item_id": str(theirs.id), "quantity": 1}])).status_code == 404
    assert await stock(client, manager) == {}


# --- sales -------------------------------------------------------------------------------------------------------------------


async def test_a_paid_sale_takes_tracked_items_out_of_stock(client, db, redis_client, branch, manager, cashier, events):
    soap, onion = await shop(db, redis_client, branch)
    rice = await make_item(db, name="Rice", price="60.00")  # not tracked
    await receive(client, manager, [{"item_id": str(soap.id), "quantity": 10}, {"item_id": str(onion.id), "quantity": 5}])

    order = await counter_sale(client, cashier, [line(soap, 2), line(onion, "0.750"), line(onion, "1.250"), line(rice, 1)])
    b = await bill(client, cashier, order["id"])
    assert (await stock(client, manager))["Soap"]["quantity"] == 10  # billed but not paid: still on the shelf
    await pay(client, cashier, b["id"], b["grand_total"])

    rows = await stock(client, manager)
    assert rows["Soap"]["quantity"] == 8 and rows["Onion"]["quantity"] == 3 and "Rice" not in rows
    sales = (await client.get(f"{INV}/movements", params={"kind": "sale"}, headers=auth_headers(manager))).json()
    assert {(m["name"], m["quantity"], m["balance_after"]) for m in sales["items"]} == {("Soap", -2, 8), ("Onion", -2, 3)}
    assert all(m["order_id"] == order["id"] for m in sales["items"])
    assert "stock.changed" in [e["type"] for e in events]


async def test_cancelled_lines_stay_in_stock_and_stock_may_go_negative(client, db, redis_client, branch, manager, cashier):
    soap, _ = await shop(db, redis_client, branch)
    await receive(client, manager, [{"item_id": str(soap.id), "quantity": 1}])
    await sell(client, cashier, [line(soap, 3)])  # the count on record was behind the shelf: the sale still goes through
    row = (await stock(client, manager))["Soap"]
    assert row["quantity"] == -2 and row["is_out"] is True


async def test_a_restaurant_order_deducts_too_and_untracked_orgs_write_nothing(client, db, branch, manager, cashier, waiter, table):
    from tests.flows import fire
    dal = await factories.make_menu_item(db, name="Dal", price="200.00")
    order = await create_order(client, waiter, table, items=[line(dal, 2)])
    await fire(client, waiter, order["id"])
    b = await bill(client, cashier, order["id"])
    await pay(client, cashier, b["id"], b["grand_total"])
    assert (await db.scalars(select(StockMovement))).all() == []  # nothing tracked: no ledger rows, no stock levels
    assert (await db.scalars(select(StockLevel))).all() == []

    await adjust(client, manager, dal, counted=20)
    order = await create_order(client, waiter, table, items=[line(dal, 3)])
    await fire(client, waiter, order["id"])
    b = await bill(client, cashier, order["id"])
    await pay(client, cashier, b["id"], b["grand_total"])
    assert (await stock(client, manager))["Dal"]["quantity"] == 17


async def test_a_reversed_and_recollected_card_payment_deducts_once(client, db, redis_client, branch, manager, cashier):
    soap, _ = await shop(db, redis_client, branch)
    await receive(client, manager, [{"item_id": str(soap.id), "quantity": 10}])
    order = await counter_sale(client, cashier, [line(soap, 4)])
    b = await bill(client, cashier, order["id"])

    async def card() -> str:
        r = await client.post(f"{API}/billing/bills/{b['id']}/terminal-payments",
                              json={"payment_mode": "card", "amount": b["grand_total"], "provider": "pinelabs"}, headers=idem(cashier))
        assert r.status_code == 201, r.text
        payment_id = r.json()["payment"]["id"]
        r = await client.post(f"{API}/billing/payments/{payment_id}/complete",
                              json={"outcome": "approved", "approval_code": "A1", "source": "terminal"}, headers=auth_headers(cashier))
        assert r.status_code == 200 and r.json()["bill"]["status"] == "paid", r.text
        return payment_id

    payment_id = await card()
    assert (await stock(client, manager))["Soap"]["quantity"] == 6
    r = await client.post(f"{API}/billing/payments/{payment_id}/reverse",
                          json={"reason": "wrong card", "approver_pin": "9001"}, headers=auth_headers(cashier))
    assert r.status_code == 200 and r.json()["bill"]["status"] == "unpaid", r.text
    await card()
    assert (await stock(client, manager))["Soap"]["quantity"] == 6


# --- adjustments, counts and alerts ------------------------------------------------------------------------------------------


async def test_adjustments_and_counts_write_the_ledger(client, db, redis_client, branch, manager):
    soap, _ = await shop(db, redis_client, branch)
    r = await adjust(client, manager, soap, counted=20)  # starts tracking
    assert r.status_code == 200 and r.json()["quantity"] == 20 and r.json()["is_tracked"] is True
    r = await client.post(f"{INV}/adjustments", json={"item_id": str(soap.id), "delta": -3, "reason": "damaged in transit"},
                          headers=idem(manager))
    assert r.json()["quantity"] == 17
    assert (await adjust(client, manager, soap, counted=17)).json()["quantity"] == 17  # matches: nothing to record

    ledger = (await client.get(f"{INV}/movements", params={"item_id": str(soap.id)}, headers=auth_headers(manager))).json()
    assert [(m["kind"], m["quantity"], m["balance_after"]) for m in ledger["items"]] == [("adjustment", -3, 17), ("count", 20, 20)]
    assert ledger["items"][0]["reason"] == "damaged in transit" and ledger["items"][0]["created_by"] == "Test Manager"

    assert (await adjust(client, manager, soap, delta=1, counted=5)).status_code == 422
    assert (await adjust(client, manager, soap)).status_code == 422
    assert (await adjust(client, manager, soap, delta=0)).status_code == 422
    assert (await adjust(client, manager, soap, delta="0.5")).json()["error"]["code"] == "whole_quantity_required"


async def test_low_stock_level_alerts_once_when_crossed(client, db, redis_client, branch, manager, cashier, events):
    soap, onion = await shop(db, redis_client, branch)
    await receive(client, manager, [{"item_id": str(soap.id), "quantity": 6}, {"item_id": str(onion.id), "quantity": 50}])
    r = await client.put(f"{INV}/stock/{soap.id}", json={"low_stock_level": 5}, headers=auth_headers(manager))
    assert r.status_code == 200 and r.json()["low_stock_level"] == 5 and r.json()["is_low"] is False
    assert (await client.put(f"{INV}/stock/{soap.id}", json={"low_stock_level": 5}, headers=auth_headers(cashier))).status_code == 403

    await sell(client, cashier, [line(soap, 1)])  # 5 left: at the level
    await sell(client, cashier, [line(soap, 1)])  # 4 left: already low, no second alert
    low = [e for e in events if e["type"] == "stock.low"]
    assert len(low) == 1 and low[0]["data"]["quantity"] == "5.000" and low[0]["entity_id"] == str(soap.id)

    assert list(await stock(client, manager, low_only=True)) == ["Soap"]
    assert (await stock(client, manager))["Soap"]["is_low"] is True

    r = await client.put(f"{INV}/stock/{soap.id}", json={"low_stock_level": None}, headers=auth_headers(manager))
    assert r.json()["low_stock_level"] is None and r.json()["is_low"] is False


async def test_tracking_can_be_switched_off(client, db, redis_client, branch, manager, cashier):
    soap, _ = await shop(db, redis_client, branch)
    await receive(client, manager, [{"item_id": str(soap.id), "quantity": 10}])
    r = await client.put(f"{INV}/stock/{soap.id}", json={"is_tracked": False}, headers=auth_headers(manager))
    assert r.json()["is_tracked"] is False
    await sell(client, cashier, [line(soap, 2)])
    assert await stock(client, manager) == {}
    assert (await stock(client, manager, include_untracked=True))["Soap"]["quantity"] == 10


# --- branches and tenants ----------------------------------------------------------------------------------------------------


async def test_stock_is_per_branch(client, db, redis_client, branch, other_branch, owner, manager):
    soap, _ = await shop(db, redis_client, branch)
    await receive(client, owner, [{"item_id": str(soap.id), "quantity": 10}], branch_id=str(branch.id))
    await receive(client, owner, [{"item_id": str(soap.id), "quantity": 3}], branch_id=str(other_branch.id))
    assert (await stock(client, owner, branch_id=str(branch.id)))["Soap"]["quantity"] == 10
    assert (await stock(client, owner, branch_id=str(other_branch.id)))["Soap"]["quantity"] == 3
    r = await client.get(f"{INV}/stock", params={"branch_id": str(other_branch.id)}, headers=auth_headers(manager))
    assert r.status_code == 403  # a manager only sees their own branch


async def test_another_organizations_stock_is_invisible(client, db, redis_client, branch, manager):
    soap, _ = await shop(db, redis_client, branch)
    purchase = (await receive(client, manager, [{"item_id": str(soap.id), "quantity": 10}])).json()

    other = await factories.make_org(db, slug="other", name="Other")
    their_branch = await factories.make_branch(db, code="DEL1", name="Delhi", org=other)
    intruder = await factories.make_staff(db, their_branch, role="owner", username="intruder")
    assert await stock(client, intruder) == {}
    assert (await client.get(f"{INV}/purchases/{purchase['id']}", headers=auth_headers(intruder))).status_code == 404
    assert (await adjust(client, intruder, soap, counted=0)).status_code == 404
    r = await client.get(f"{INV}/stock", params={"branch_id": str(branch.id)}, headers=auth_headers(intruder))
    assert r.status_code == 403
    assert (await stock(client, manager))["Soap"]["quantity"] == Decimal(10)
