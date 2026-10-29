"""Endpoints added for the admin app: remote void approvals, shift history, today's sales breakdown."""

from sqlalchemy import select

from app.models import AuditLog
from tests import factories
from tests.factories import auth_headers
from tests.flows import API, bill, create_order, fire, line, open_shift, pay


async def _fired_order(client, db, waiter, table=None, price="200.00", qty=1, name="Dal"):
    item = await factories.make_menu_item(db, name=name, price=price)
    order = await create_order(client, waiter, table, items=[line(item, qty)])
    kots = await fire(client, waiter, order["id"])
    return order, kots[0]


# --- Void requests ----------------------------------------------------------------


async def test_void_request_approve_flow(client, db, waiter, manager, table, events):
    order, kot = await _fired_order(client, db, waiter, table)
    r = await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "Customer changed mind"},
                          headers=auth_headers(waiter))
    assert r.status_code == 201, r.text
    request = r.json()
    assert request["status"] == "pending" and request["amount"] == "200.00"
    assert request["table_number"] == "T1" and request["items"] == [{"name": "Dal", "quantity": 1}]
    assert request["requested_by_name"] == "Test Waiter"
    assert any(e["type"] == "void_request.created" for e in events)

    dup = await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "again please"},
                            headers=auth_headers(waiter))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "void_request_pending"

    pending = await client.get(f"{API}/approvals/void-requests", params={"status": "pending"}, headers=auth_headers(manager))
    assert pending.status_code == 200 and pending.json()["total"] == 1

    ok = await client.post(f"{API}/approvals/void-requests/{request['id']}/approve", json={}, headers=auth_headers(manager))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved" and ok.json()["reviewed_by_name"] == "Test Manager"

    kots = await client.get(f"{API}/kots", params={"order_id": order["id"]}, headers=auth_headers(manager))
    assert kots.json()[0]["status"] == "voided"
    o = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert o.json()["subtotal"] == "0.00"
    audit = await db.scalar(select(AuditLog).where(AuditLog.action_type == "kot_void"))
    assert audit.actor_id == waiter.id and audit.approver_id == manager.id

    again = await client.post(f"{API}/approvals/void-requests/{request['id']}/approve", json={}, headers=auth_headers(manager))
    assert again.status_code == 409


async def test_void_request_reject_keeps_ticket(client, db, waiter, manager):
    order, kot = await _fired_order(client, db, waiter)
    request = (await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "Wrong order"},
                                 headers=auth_headers(waiter))).json()
    r = await client.post(f"{API}/approvals/void-requests/{request['id']}/reject", json={"note": "Already served"},
                          headers=auth_headers(manager))
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["review_note"] == "Already served"
    kots = await client.get(f"{API}/kots", params={"order_id": order["id"]}, headers=auth_headers(manager))
    assert kots.json()[0]["status"] == "queued"
    # A new request can be raised after a rejection.
    again = await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "Wrong order"},
                              headers=auth_headers(waiter))
    assert again.status_code == 201


async def test_void_request_permissions(client, db, waiter, manager, other_branch):
    _, kot = await _fired_order(client, db, waiter)
    request = (await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "Spillage"},
                                 headers=auth_headers(waiter))).json()
    # Floor staff can't review; managers need a password session; other branches can't see it.
    assert (await client.post(f"{API}/approvals/void-requests/{request['id']}/approve", json={},
                              headers=auth_headers(waiter))).status_code == 403
    assert (await client.post(f"{API}/approvals/void-requests/{request['id']}/approve", json={},
                              headers=auth_headers(manager, auth_method="pin"))).status_code == 403
    other_manager = await factories.make_staff(db, other_branch, role="manager", pin="7070")
    assert (await client.post(f"{API}/approvals/void-requests/{request['id']}/approve", json={},
                              headers=auth_headers(other_manager))).status_code == 404


async def test_served_ticket_cannot_be_requested(client, db, waiter, kitchen):
    _, kot = await _fired_order(client, db, waiter)
    for status in ("preparing", "ready", "served"):
        await client.patch(f"{API}/kots/{kot['id']}/status", json={"status": status}, headers=auth_headers(kitchen))
    r = await client.post(f"{API}/kots/{kot['id']}/void-requests", json={"reason": "Too late"},
                          headers=auth_headers(waiter))
    assert r.status_code == 409


# --- Shift history ----------------------------------------------------------------


async def test_shift_history(client, db, waiter, cashier, manager):
    await open_shift(client, cashier, "500.00")
    order, _ = await _fired_order(client, db, waiter, price="100.00")
    b = await bill(client, cashier, order["id"])
    assert (await pay(client, cashier, b["id"], "105.00")).status_code == 200
    await client.post(f"{API}/staff/shifts/close", json={"blind_drop_cash": "600.00"}, headers=auth_headers(cashier))

    assert (await client.get(f"{API}/staff/shifts", headers=auth_headers(cashier))).status_code == 403
    r = await client.get(f"{API}/staff/shifts", params={"status": "closed"}, headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    shift = r.json()["items"][0]
    assert shift["user_name"] == "Test Cashier"
    assert (shift["opening_cash"], shift["cash_sales"], shift["expected_cash"], shift["cash_variance"]) == (
        "500.00", "105.00", "605.00", "-5.00",
    )


# --- Sales breakdown ----------------------------------------------------------------


async def test_sales_breakdown(client, db, waiter, cashier, manager):
    await open_shift(client, cashier)
    order, _ = await _fired_order(client, db, waiter, price="100.00", qty=2, name="Naan")
    b = await bill(client, cashier, order["id"])
    await pay(client, cashier, b["id"], "210.00", "upi", reference="UTR9")
    unpaid, _ = await _fired_order(client, db, waiter, price="999.00", name="Unpaid dish")
    assert unpaid

    r = await client.get(f"{API}/analytics/sales/today", headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["hourly"]) == 1 and data["hourly"][0]["sales"] == "210.00" and data["hourly"][0]["bills"] == 1
    assert data["top_items"] == [{"name": "Naan", "category": "Mains", "quantity": 2, "total": "200.00"}]
    assert data["payments"] == {"cash": "0.00", "card": "0.00", "upi": "210.00", "total": "210.00"}


async def test_sales_breakdown_empty_day(client, manager):
    r = await client.get(f"{API}/analytics/sales/today", headers=auth_headers(manager))
    assert r.status_code == 200
    assert r.json()["hourly"] == [] and r.json()["top_items"] == [] and r.json()["payments"]["total"] == "0.00"
