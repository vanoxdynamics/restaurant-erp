import asyncio
import re
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select

from app.models import AuditLog, DiningTable, Payment
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line, open_shift, pay


async def _ready_order(client, db, waiter, table=None, price="250.00", qty=2):
    item = await factories.make_menu_item(db, price=price)
    order = await create_order(client, waiter, table, items=[line(item, qty)])
    await fire(client, waiter, order["id"])
    return order


async def test_generate_bill_totals_and_state(client, db, waiter, cashier, table):
    order = await _ready_order(client, db, waiter, table)
    b = await bill(client, cashier, order["id"])
    assert re.fullmatch(r"BLR1/\d{4}/000001", b["bill_number"])
    assert (b["subtotal"], b["tax_cgst"], b["tax_sgst"], b["grand_total"]) == ("500.00", "12.50", "12.50", "525.00")
    assert b["balance_due"] == "525.00" and b["status"] == "unpaid"
    assert (await db.get(DiningTable, table.id, populate_existing=True)).status == "billed"

    again = await client.post(f"{API}/billing/bills", json={"order_id": order["id"]}, headers=idem(cashier))
    assert again.status_code == 409


async def test_bill_guards(client, db, waiter, cashier):
    item = await factories.make_menu_item(db)
    order = await create_order(client, waiter, items=[line(item)])
    unfired = await client.post(f"{API}/billing/bills", json={"order_id": order["id"]}, headers=idem(cashier))
    assert unfired.status_code == 409 and unfired.json()["error"]["code"] == "unfired_items"
    await fire(client, waiter, order["id"])
    assert (await client.post(f"{API}/billing/bills", json={"order_id": order["id"]},
                              headers=idem(waiter))).status_code == 403


async def test_discount_approval_threshold(client, db, waiter, cashier, manager):
    order = await _ready_order(client, db, waiter, price="100.00", qty=1)
    body = {"order_id": order["id"], "discount_amount": "11.00", "discount_reason": "regular"}
    denied = await client.post(f"{API}/billing/bills", json=body, headers=idem(cashier))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "approval_required"
    ok = await client.post(f"{API}/billing/bills", json={**body, "approver_pin": "9001"}, headers=idem(cashier))
    assert ok.status_code == 201, ok.text
    # 89.00 taxable @5% = 4.45 tax
    assert ok.json()["grand_total"] == "93.45"
    audit = await db.scalar(select(AuditLog).where(AuditLog.action_type == "high_discount"))
    assert audit.approver_id == manager.id


async def test_small_discount_needs_no_approval(client, db, waiter, cashier):
    order = await _ready_order(client, db, waiter, price="100.00", qty=1)
    b = await bill(client, cashier, order["id"], discount_amount="10.00", discount_reason="birthday")
    assert b["discount_amount"] == "10.00"


async def test_partial_then_full_payment_settles(client, db, waiter, cashier, table, events):
    order = await _ready_order(client, db, waiter, table)
    b = await bill(client, cashier, order["id"])
    # Cash needs no open shift; the payment just isn't tied to one.
    no_shift = await pay(client, cashier, b["id"], "100.00")
    assert no_shift.status_code == 200 and no_shift.json()["bill"]["status"] == "partially_paid"
    payment_row = await db.get(Payment, no_shift.json()["payment"]["id"])
    assert payment_row.shift_id is None and payment_row.received_by_id == cashier.id
    await open_shift(client, cashier)

    r = await pay(client, cashier, b["id"], "200.00", "card", reference="AUTH9")
    assert r.status_code == 200 and r.json()["bill"]["status"] == "partially_paid"
    over = await pay(client, cashier, b["id"], "400.00", "upi", reference="UTR1")
    assert over.status_code == 422 and over.json()["error"]["code"] == "overpayment"

    r = await pay(client, cashier, b["id"], "400.00")  # cash: change returned
    assert r.status_code == 200, r.text
    assert r.json()["change_due"] == "175.00"
    assert r.json()["bill"]["status"] == "paid" and r.json()["bill"]["balance_due"] == "0.00"
    o = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert o.json()["status"] == "completed"
    assert (await db.get(DiningTable, table.id, populate_existing=True)).status == "vacant"
    assert "bill.settled" in {e["type"] for e in events}

    closed = await pay(client, cashier, b["id"], "1.00")
    assert closed.status_code == 409


async def test_find_bill_by_order_and_payment_provider(client, db, waiter, cashier):
    order = await _ready_order(client, db, waiter)
    other = await _ready_order(client, db, waiter)
    b = await bill(client, cashier, order["id"])
    await bill(client, cashier, other["id"])
    await open_shift(client, cashier)
    r = await pay(client, cashier, b["id"], "100.00")
    assert r.json()["payment"]["provider"] == "manual"

    found = await client.get(f"{API}/billing/bills", params={"order_id": order["id"]}, headers=auth_headers(cashier))
    assert found.status_code == 200
    assert [x["id"] for x in found.json()["items"]] == [b["id"]]
    assert found.json()["items"][0]["payments"][0]["provider"] == "manual"


async def test_payment_idempotent_replay(client, db, waiter, cashier):
    order = await _ready_order(client, db, waiter)
    b = await bill(client, cashier, order["id"])
    await open_shift(client, cashier)
    first = await pay(client, cashier, b["id"], "100.00", key="pay-key-00001")
    second = await pay(client, cashier, b["id"], "100.00", key="pay-key-00001")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert await db.scalar(select(func.count(Payment.id))) == 1


async def test_concurrent_full_payments_only_one_wins(client, db, waiter, cashier):
    order = await _ready_order(client, db, waiter)
    b = await bill(client, cashier, order["id"])
    results = await asyncio.gather(*(
        pay(client, cashier, b["id"], "525.00", "card", reference=f"AUTH{i}") for i in range(3)
    ))
    assert sorted(r.status_code for r in results)[0] == 200
    assert [r.status_code for r in results].count(200) == 1
    assert await db.scalar(select(func.count(Payment.id))) == 1


async def test_split_payment(client, db, waiter, cashier):
    order = await _ready_order(client, db, waiter)
    b = await bill(client, cashier, order["id"])
    await open_shift(client, cashier)
    url = f"{API}/billing/bills/{b['id']}/split-payments"
    off = {"payments": [{"payment_mode": "cash", "amount": "300.00"},
                        {"payment_mode": "upi", "amount": "224.99", "reference_id": "UTR2"}]}
    r = await client.post(url, json=off, headers=idem(cashier))
    assert r.status_code == 422 and r.json()["error"]["code"] == "split_mismatch"
    assert await db.scalar(select(func.count(Payment.id))) == 0
    exact = {"payments": [{"payment_mode": "cash", "amount": "300.00"},
                          {"payment_mode": "upi", "amount": "225.00", "reference_id": "UTR2"}]}
    r = await client.post(url, json=exact, headers=idem(cashier))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid" and len(r.json()["payments"]) == 2


async def test_upi_qr(client, db, waiter, cashier, branch):
    order = await _ready_order(client, db, waiter)
    b = await bill(client, cashier, order["id"])
    missing = await client.get(f"{API}/billing/bills/{b['id']}/upi-qr", headers=auth_headers(cashier))
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "upi_not_configured"

    branch.upi_vpa = "cafe@okbank"
    branch.upi_payee_name = "Cafe & Co"
    await db.commit()
    r = await client.get(f"{API}/billing/bills/{b['id']}/upi-qr", headers=auth_headers(cashier))
    assert r.status_code == 200
    url = r.json()["upi_intent_url"]
    params = parse_qs(urlparse(url).query)
    assert url.startswith("upi://pay?")
    assert params["am"] == ["525.00"] and params["tr"] == [b["bill_number"]] and params["pn"] == ["Cafe & Co"]


async def test_void_bill(client, db, waiter, cashier, manager, table):
    order = await _ready_order(client, db, waiter, table)
    b = await bill(client, cashier, order["id"])
    url = f"{API}/billing/bills/{b['id']}/void"
    assert (await client.post(url, json={"reason": "wrong table"}, headers=auth_headers(cashier))).status_code == 403
    r = await client.post(url, json={"reason": "wrong table", "approver_pin": "9001"}, headers=auth_headers(cashier))
    assert r.status_code == 200 and r.json()["status"] == "voided"
    o = await client.get(f"{API}/orders/{order['id']}", headers=auth_headers(waiter))
    assert o.json()["status"] == "active"
    # A fresh bill gets the next number; voided numbers are never reused.
    b2 = await bill(client, cashier, order["id"])
    assert b2["bill_number"].endswith("000002")


async def test_live_kpis_and_z_report(client, db, waiter, cashier, manager, floor, table, table2):
    await open_shift(client, cashier)
    order = await _ready_order(client, db, waiter, table, price="100.00", qty=1)
    b = await bill(client, cashier, order["id"])
    assert (await pay(client, cashier, b["id"], "105.00")).status_code == 200
    busy = await _ready_order(client, db, waiter, table2, price="50.00", qty=1)
    assert busy

    assert (await client.get(f"{API}/analytics/kpis/live", headers=auth_headers(cashier))).status_code == 403
    assert (await client.get(f"{API}/analytics/kpis/live",
                             headers=auth_headers(manager, auth_method="pin"))).status_code == 403
    k = await client.get(f"{API}/analytics/kpis/live", headers=auth_headers(manager))
    assert k.status_code == 200, k.text
    kpis = k.json()
    assert (kpis["active_tables_count"], kpis["vacant_tables_count"], kpis["open_orders_count"]) == (1, 1, 1)
    # Only the open order's ticket counts; the paid order's unserved ticket is not kitchen work.
    assert kpis["pending_kots_count"] == 1
    assert kpis["total_sales_today"] == "105.00" and kpis["average_ticket_size"] == "105.00"

    z = await client.get(f"{API}/analytics/reports/z-report", headers=auth_headers(manager))
    assert z.status_code == 200
    report = z.json()
    assert report["gross_sales"] == "100.00" and report["total_bills_count"] == 1
    assert report["cash_collected"] == "105.00"
    total_tenders = sum(float(report[k]) for k in ("cash_collected", "card_collected", "upi_collected"))
    assert total_tenders == float(report["net_sales"]) + float(report["total_tax_cgst"]) + float(report["total_tax_sgst"])

    closed = await client.post(f"{API}/analytics/reports/z-report/close", params={"report_date": report["report_date"]},
                               headers=auth_headers(manager))
    assert closed.status_code == 200
    again = await client.post(f"{API}/analytics/reports/z-report/close", params={"report_date": report["report_date"]},
                              headers=auth_headers(manager))
    assert again.status_code == 200


async def test_audit_log_listing(client, db, waiter, manager, table, table2):
    await create_order(client, waiter, table)
    await client.post(f"{API}/floor/tables/transfer", headers=auth_headers(waiter),
                      json={"source_table_id": str(table.id), "target_table_id": str(table2.id)})
    r = await client.get(f"{API}/analytics/audit-log", headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    entry = r.json()["items"][0]
    assert entry["action_type"] == "table_transfer" and entry["actor_name"] == "Test Waiter"
    filtered = await client.get(f"{API}/analytics/audit-log", params={"action_type": "kot_void"},
                                headers=auth_headers(manager))
    assert filtered.json()["total"] == 0
