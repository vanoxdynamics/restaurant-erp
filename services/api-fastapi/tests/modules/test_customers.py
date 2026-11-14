"""
Customers: adding and finding them, one phone number per business, putting one on an order, their name on the printed bill,
visits and spend from paid bills, and never seeing another business's customers.
"""

from decimal import Decimal

import pytest

from app.schemas.customers import normalize_phone
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, create_order, fire, line, pay
from tests.flows import bill as make_bill

CUSTOMERS = f"{API}/customers"


async def add(client, user, **body):
    return await client.post(CUSTOMERS, json={"name": "Asha Rao", **body}, headers=auth_headers(user))


async def added(client, user, **body) -> dict:
    r = await add(client, user, **body)
    assert r.status_code == 201, r.text
    return r.json()


async def search(client, user, term=None, **params) -> list:
    r = await client.get(CUSTOMERS, params={**({"search": term} if term else {}), **params}, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return [c["name"] for c in r.json()["items"]]


async def put_customer(client, user, order_id, customer_id):
    return await client.put(f"{API}/orders/{order_id}/customer", json={"customer_id": customer_id}, headers=auth_headers(user))


async def bill(client, user, order_id: str) -> dict:
    """A restaurant bills what the kitchen was sent, so send the order first."""
    await fire(client, user, order_id)
    return await make_bill(client, user, order_id)


async def other_business(db):
    org = await factories.make_org(db, slug="rival", name="Rival Foods")
    branch = await factories.make_branch(db, code="RIV1", name="Rival", org=org)
    owner = await factories.make_staff(db, branch, role="owner", username="rival.owner", pin="9911")
    return branch, owner


# --- phone numbers -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("typed, stored", [
    ("9876543210", "9876543210"), ("+91 98765 43210", "9876543210"), ("098765-43210", "9876543210"),
    ("(080) 2345 6789", "8023456789"), ("+1 415 555 0100", "14155550100"), ("  ", None), (None, None),
])
def test_phone_numbers_are_stored_as_digits(typed, stored):
    assert normalize_phone(typed) == stored


@pytest.mark.parametrize("typed", ["12345", "98765x3210", "1" * 16])
def test_bad_phone_numbers_are_refused(typed):
    with pytest.raises(ValueError):
        normalize_phone(typed)


# --- adding, finding, changing -----------------------------------------------------------------------------------------------


async def test_cashier_adds_a_customer_and_finds_them_by_phone_or_name(client, cashier, waiter):
    asha = await added(client, cashier, phone="+91 98765 43210", gstin="29abcde1234f1z5", email=" asha@example.com ", address="  ")
    assert asha["phone"] == "9876543210" and asha["gstin"] == "29ABCDE1234F1Z5" and asha["email"] == "asha@example.com"
    assert asha["address"] is None and asha["is_active"] is True
    assert asha["visits"] == 0 and Decimal(asha["total_spent"]) == 0 and asha["last_visit_at"] is None
    await added(client, cashier, name="Bharat Stores", phone="9000011111")
    await added(client, cashier, name="Walk-in regular")  # no phone is fine

    assert await search(client, waiter) == ["Asha Rao", "Bharat Stores", "Walk-in regular"]
    assert await search(client, waiter, "98765") == ["Asha Rao"]
    assert await search(client, waiter, "+91 98765 43210") == ["Asha Rao"]
    assert await search(client, waiter, "bhar") == ["Bharat Stores"]
    assert await search(client, waiter, "29abcde1234f1z5") == ["Asha Rao"]
    assert await search(client, waiter, "%") == []  # wildcards are matched literally

    one = await client.get(f"{CUSTOMERS}/{asha['id']}", headers=auth_headers(waiter))
    assert one.status_code == 200 and one.json()["name"] == "Asha Rao"


async def test_bad_details_are_refused(client, cashier):
    assert (await add(client, cashier, name="  ")).status_code == 422
    assert (await add(client, cashier, phone="12345")).status_code == 422
    assert (await add(client, cashier, gstin="NOT-A-GSTIN")).status_code == 422
    assert (await add(client, cashier, email="nobody")).status_code == 422


async def test_a_phone_number_belongs_to_one_customer(client, cashier):
    asha = await added(client, cashier, phone="9876543210")
    r = await add(client, cashier, name="Someone Else", phone="+91 98765 43210")
    assert r.status_code == 409 and r.json()["error"]["code"] == "customer_phone_taken"
    assert r.json()["error"]["details"] == {"customer_id": asha["id"], "name": "Asha Rao", "is_active": True}

    other = await added(client, cashier, name="Someone Else", phone="9000011111")
    r = await client.patch(f"{CUSTOMERS}/{other['id']}", json={"phone": "9876543210"}, headers=auth_headers(cashier))
    assert r.status_code == 409
    r = await client.patch(f"{CUSTOMERS}/{asha['id']}", json={"phone": "9876543210", "name": "Asha R"}, headers=auth_headers(cashier))
    assert r.status_code == 200 and r.json()["name"] == "Asha R"  # keeping her own number is not a duplicate


async def test_details_change_and_only_managers_hide_a_customer(client, cashier, manager):
    asha = await added(client, cashier, phone="9876543210", notes="Likes less sugar")
    url = f"{CUSTOMERS}/{asha['id']}"
    r = await client.patch(url, json={"notes": None, "gstin": "29ABCDE1234F1Z5"}, headers=auth_headers(cashier))
    assert r.status_code == 200 and r.json()["notes"] is None and r.json()["gstin"] == "29ABCDE1234F1Z5"
    assert r.json()["phone"] == "9876543210"  # fields that were not sent stay

    assert (await client.patch(url, json={"is_active": False}, headers=auth_headers(cashier))).status_code == 403
    assert (await client.patch(url, json={"is_active": False}, headers=auth_headers(manager))).json()["is_active"] is False
    assert await search(client, cashier) == []
    assert await search(client, cashier, include_inactive=True) == ["Asha Rao"]


async def test_kitchen_staff_have_no_access(client, kitchen):
    assert (await client.get(CUSTOMERS, headers=auth_headers(kitchen))).status_code == 403
    assert (await add(client, kitchen)).status_code == 403


# --- on an order and its bill ------------------------------------------------------------------------------------------------


async def test_customer_on_an_order_prints_on_the_bill_and_counts_as_a_visit(client, db, table, cashier, manager):
    item = await factories.make_menu_item(db, name="Thali", price="200.00")
    asha = await added(client, cashier, phone="9876543210", gstin="29ABCDE1234F1Z5")

    order = await create_order(client, cashier, items=[line(item)], customer_id=asha["id"])
    assert order["customer_id"] == asha["id"]
    b = await bill(client, cashier, order["id"])

    text = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"format": "text"}, headers=auth_headers(cashier))).text
    assert "Customer: Asha Rao" in text and "Ph: 9876543210" in text and "GSTIN: 29ABCDE1234F1Z5" in text

    # An unpaid bill is not a visit yet.
    assert (await client.get(f"{CUSTOMERS}/{asha['id']}", headers=auth_headers(cashier))).json()["visits"] == 0
    assert (await pay(client, cashier, b["id"], b["grand_total"])).status_code == 200
    seen = (await client.get(f"{CUSTOMERS}/{asha['id']}", headers=auth_headers(cashier))).json()
    assert seen["visits"] == 1 and seen["total_spent"] == b["grand_total"] and seen["last_visit_at"] is not None
    listed = (await client.get(CUSTOMERS, headers=auth_headers(cashier))).json()["items"][0]
    assert listed["visits"] == 1 and listed["total_spent"] == b["grand_total"]

    bills = await client.get(f"{CUSTOMERS}/{asha['id']}/bills", headers=auth_headers(manager))
    assert bills.status_code == 200 and [x["bill_number"] for x in bills.json()["items"]] == [b["bill_number"]]
    assert (await client.get(f"{CUSTOMERS}/{asha['id']}/bills", headers=auth_headers(cashier))).status_code == 403


async def test_customer_is_added_changed_and_removed_after_the_order_exists(client, db, cashier):
    item = await factories.make_menu_item(db, name="Thali", price="200.00")
    asha = await added(client, cashier, phone="9876543210")
    order = await create_order(client, cashier, items=[line(item)])
    assert order["customer_id"] is None
    b = await bill(client, cashier, order["id"])
    receipt = f"{API}/billing/bills/{b['id']}/receipt"
    assert "Customer:" not in (await client.get(receipt, params={"format": "text"}, headers=auth_headers(cashier))).text

    # After billing: "can you put my name on it?"
    r = await put_customer(client, cashier, order["id"], asha["id"])
    assert r.status_code == 200 and r.json()["customer_id"] == asha["id"]
    assert "Customer: Asha Rao" in (await client.get(receipt, params={"format": "text"}, headers=auth_headers(cashier))).text

    r = await put_customer(client, cashier, order["id"], None)
    assert r.status_code == 200 and r.json()["customer_id"] is None


async def test_unknown_hidden_or_cancelled_are_refused(client, db, cashier, manager):
    item = await factories.make_menu_item(db, name="Thali", price="200.00")
    asha = await added(client, cashier, phone="9876543210")
    order = await create_order(client, cashier, items=[line(item)])

    missing = "00000000-0000-0000-0000-000000000001"
    r = await put_customer(client, cashier, order["id"], missing)
    assert r.status_code == 422 and r.json()["error"]["code"] == "customer_not_found"
    r = await client.post(f"{API}/orders", json={"order_type": "takeaway", "items": [line(item)], "customer_id": missing},
                          headers=idem(cashier))
    assert r.status_code == 422 and r.json()["error"]["code"] == "customer_not_found"

    await client.patch(f"{CUSTOMERS}/{asha['id']}", json={"is_active": False}, headers=auth_headers(manager))
    assert (await put_customer(client, cashier, order["id"], asha["id"])).status_code == 422


# --- other businesses --------------------------------------------------------------------------------------------------------


async def test_another_business_cannot_see_or_use_our_customers(client, db, cashier):
    item = await factories.make_menu_item(db, name="Thali", price="200.00")
    asha = await added(client, cashier, phone="9876543210")
    order = await create_order(client, cashier, items=[line(item)])
    _, rival = await other_business(db)

    assert await search(client, rival) == []
    assert (await client.get(f"{CUSTOMERS}/{asha['id']}", headers=auth_headers(rival))).status_code == 404
    assert (await client.patch(f"{CUSTOMERS}/{asha['id']}", json={"name": "Mine now"}, headers=auth_headers(rival))).status_code == 404
    assert (await client.get(f"{CUSTOMERS}/{asha['id']}/bills", headers=auth_headers(rival))).status_code == 404

    # The same phone number is free in the other business, and their customer cannot go on our order.
    theirs = await added(client, rival, name="Their Asha", phone="9876543210")
    r = await put_customer(client, cashier, order["id"], theirs["id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "customer_not_found"
    assert await search(client, cashier) == ["Asha Rao"]
