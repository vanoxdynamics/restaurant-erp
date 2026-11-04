"""
Card/UPI payments taken on a terminal (Pine Labs): recorded as pending before the terminal charges, counted only when approved,
reversible by a manager the same day, and never lost when the answer does not arrive.
"""

import pytest
from sqlalchemy import select

from app.models import AuditLog, Order
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line, pay

APPROVED = {
    "outcome": "approved", "approval_code": "A1B2C3", "message": "APPROVED",
    "details": {"ApprovalCode": "A1B2C3", "RetrievalReferenceNumber": "431122334455", "TerminalId": "30000001",
                "InvoiceNumber": 12, "BatchNumber": 9001, "AcquiringBankCode": "01", "CardType": "VISA",
                "CardNumber": "4111111111111111", "CardholderName": "ASHA RAO", "ExpiryDate": "2812", "Track2": "secret"},
}


@pytest.fixture
async def billed(client, db, cashier, table):
    item = await factories.make_menu_item(db, name="Thali", price="400.00")
    order = await create_order(client, cashier, table, items=[line(item)])
    await fire(client, cashier, order["id"])
    return order, await bill(client, cashier, order["id"])


async def start(client, user, bill_id, amount, mode="card", key=None):
    return await client.post(f"{API}/billing/bills/{bill_id}/terminal-payments",
                             json={"payment_mode": mode, "amount": str(amount)}, headers=idem(user, key))


async def complete(client, user, payment_id, body):
    return await client.post(f"{API}/billing/payments/{payment_id}/complete", json=body, headers=auth_headers(user))


async def test_a_card_payment_counts_only_once_the_terminal_approves_it(client, db, cashier, billed):
    order, b = billed
    r = await start(client, cashier, b["id"], b["grand_total"])
    assert r.status_code == 201, r.text
    payment, after_start = r.json()["payment"], r.json()["bill"]
    ref = payment["provider_meta"]["billing_ref_no"]
    assert (payment["status"], payment["provider"], len(ref)) == ("pending", "pinelabs", 10)
    assert ref.isalnum() and ref.isupper() and not set(ref) & set("01OI")  # readable off a charge slip
    assert (after_start["status"], after_start["paid_amount"]) == ("unpaid", "0.00")  # not counted yet

    r = await complete(client, cashier, payment["id"], APPROVED)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["payment"]["status"] == "success" and done["payment"]["reference_id"] == "A1B2C3"
    assert done["bill"]["status"] == "paid" and done["bill"]["balance_due"] == "0.00"
    assert (await db.get(Order, order["id"], populate_existing=True)).status == "completed"

    meta = done["payment"]["provider_meta"]
    assert meta["billing_ref_no"] == ref and meta["RetrievalReferenceNumber"] == "431122334455" and meta["source"] == "terminal"
    assert meta["CardNumber"] == "************1111"  # stored masked whatever the device sends
    assert not {"CardholderName", "ExpiryDate", "Track2"} & set(meta)  # and nothing else about the card


async def test_a_declined_payment_is_kept_as_failed_and_the_bill_stays_open(client, db, cashier, billed):
    _, b = billed
    payment = (await start(client, cashier, b["id"], b["grand_total"], mode="upi")).json()["payment"]
    r = await complete(client, cashier, payment["id"], {"outcome": "declined", "message": "CANCELLED BY USER",
                                                        "details": {"ResponseCode": "1", "ResponseMsg": "CANCELLED"}})
    assert r.json()["payment"]["status"] == "failed" and r.json()["bill"]["status"] == "unpaid"
    assert (await start(client, cashier, b["id"], b["grand_total"])).status_code == 201  # try again


async def test_nothing_else_may_change_the_bill_while_a_terminal_is_charging(client, db, cashier, manager, billed):
    _, b = billed
    payment = (await start(client, cashier, b["id"], "100.00")).json()["payment"]
    for r in (
        await pay(client, cashier, b["id"], "50.00"),
        await start(client, cashier, b["id"], "50.00"),
        await client.post(f"{API}/billing/bills/{b['id']}/void", json={"reason": "mistake", "approver_pin": "9001"},
                          headers=auth_headers(manager)),
    ):
        assert r.status_code == 409 and r.json()["error"]["code"] == "terminal_payment_pending", r.text
        assert r.json()["error"]["details"]["payment_id"] == payment["id"]
    await complete(client, cashier, payment["id"], APPROVED)
    r = await pay(client, cashier, b["id"], str(float(b["grand_total"]) - 100))  # the rest in cash
    assert r.status_code == 200 and r.json()["bill"]["status"] == "paid"


async def test_answers_are_safe_to_repeat_but_cannot_change(client, db, cashier, billed):
    _, b = billed
    payment = (await start(client, cashier, b["id"], b["grand_total"])).json()["payment"]
    first = await complete(client, cashier, payment["id"], APPROVED)
    again = await complete(client, cashier, payment["id"], APPROVED)  # the app retried after a dropped connection
    assert again.status_code == 200 and again.json()["bill"]["paid_amount"] == first.json()["bill"]["paid_amount"]
    flip = await complete(client, cashier, payment["id"], {"outcome": "declined"})
    assert flip.status_code == 409 and flip.json()["error"]["code"] == "payment_already_completed"


async def test_starting_is_idempotent_and_validated(client, db, cashier, billed):
    _, b = billed
    a = await start(client, cashier, b["id"], b["grand_total"], key="term-start-0001")
    again = await start(client, cashier, b["id"], b["grand_total"], key="term-start-0001")
    assert again.headers.get("idempotent-replay") == "true" and again.json()["payment"]["id"] == a.json()["payment"]["id"]
    payment_id = a.json()["payment"]["id"]
    no_code = await complete(client, cashier, payment_id, {"outcome": "approved"})
    assert no_code.status_code == 422  # an approval needs the terminal's approval code
    await complete(client, cashier, payment_id, {"outcome": "declined"})
    assert (await start(client, cashier, b["id"], "99999.00")).status_code == 422  # more than is owed
    cash = await client.post(f"{API}/billing/bills/{b['id']}/terminal-payments", json={"payment_mode": "cash", "amount": "10.00"},
                             headers=idem(cashier))
    assert cash.status_code == 422  # cash is never a terminal payment


async def test_an_unconfirmed_payment_can_be_resolved_from_the_charge_slip(client, db, cashier, billed):
    _, b = billed
    payment = (await start(client, cashier, b["id"], b["grand_total"])).json()["payment"]
    listed = (await client.get(f"{API}/billing/bills/{b['id']}", headers=auth_headers(cashier))).json()
    assert [p["status"] for p in listed["payments"]] == ["pending"]  # the app finds it again after a restart
    r = await complete(client, cashier, payment["id"], {"outcome": "approved", "approval_code": "Z9Y8X7", "source": "manual"})
    assert r.json()["bill"]["status"] == "paid" and r.json()["payment"]["provider_meta"]["source"] == "manual"


async def test_a_manager_can_reverse_a_terminal_payment_and_the_bill_reopens(client, db, cashier, manager, billed):
    order, b = billed
    payment = (await start(client, cashier, b["id"], b["grand_total"])).json()["payment"]
    await complete(client, cashier, payment["id"], APPROVED)
    url = f"{API}/billing/payments/{payment['id']}/reverse"
    body = {"reason": "charged twice", "details": {"ApprovalCode": "V1", "ResponseCode": "0"}}

    r = await client.post(url, json=body, headers=auth_headers(cashier))
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"
    r = await client.post(url, json={**body, "approver_pin": "9001"}, headers=auth_headers(cashier))
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["payment"]["status"] == "reversed" and done["payment"]["provider_meta"]["reversal"]["reason"] == "charged twice"
    assert (done["bill"]["status"], done["bill"]["paid_amount"], done["bill"]["settled_at"]) == ("unpaid", "0.00", None)
    assert (await db.get(Order, order["id"], populate_existing=True)).status == "billed"
    [audit] = (await db.scalars(select(AuditLog).where(AuditLog.action_type == "payment_reverse"))).all()
    assert audit.actor_id == cashier.id and audit.approver_id == manager.id and audit.details["reason"] == "charged twice"

    again = await client.post(url, json={**body, "approver_pin": "9001"}, headers=auth_headers(cashier))
    assert again.status_code == 200 and again.json()["bill"]["paid_amount"] == "0.00"  # not taken off twice
    r = await pay(client, cashier, b["id"], b["grand_total"])  # collected again in cash
    assert r.json()["bill"]["status"] == "paid"
    cash_payment = r.json()["payment"]
    r = await client.post(f"{API}/billing/payments/{cash_payment['id']}/reverse", json={**body, "approver_pin": "9001"},
                          headers=auth_headers(cashier))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_reversible"  # only terminal payments


async def test_who_may_take_terminal_payments(client, db, cashier, waiter, billed):
    _, b = billed
    assert (await start(client, waiter, b["id"], "10.00")).status_code == 403  # waiters bill only on a plan without a kitchen screen
    other_org = await factories.make_org(db, slug="globex", name="Globex")
    other_branch = await factories.make_branch(db, code="DEL1", name="Delhi", org=other_org)
    stranger = await factories.make_staff(db, other_branch, role="owner", pin="8000", username="owner_b")
    payment = (await start(client, cashier, b["id"], b["grand_total"])).json()["payment"]
    assert (await start(client, stranger, b["id"], "10.00")).status_code == 404
    assert (await complete(client, stranger, payment["id"], APPROVED)).status_code == 404


# --- printed customer bill ---------------------------------------------------------------------------------------------------


async def test_the_customer_bill_prints_items_taxes_and_payments(client, db, branch, cashier, table):
    branch.gstin, branch.address, branch.phone = "29ABCDE1234F1Z5", "12 CMH Road, Indiranagar", "080-4000-1234"
    await db.commit()
    dal = await factories.make_menu_item(db, name="Dal Makhani with an extraordinarily long and descriptive name", price="310.00")
    naan = await factories.make_menu_item(db, name="Butter Naan", price="70.00")
    order = await create_order(client, cashier, table, items=[line(dal), line(naan, 2)])
    await fire(client, cashier, order["id"])
    b = await bill(client, cashier, order["id"])
    await pay(client, cashier, b["id"], "100.00")

    for width, cols in ((80, 48), (58, 32)):
        r = await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"width": width}, headers=auth_headers(cashier))
        assert r.status_code == 200, r.text
        receipt = r.json()
        assert receipt["paper_mm"] == width and receipt["bill_number"] == b["bill_number"]
        for row in receipt["rows"]:
            limit = cols // 2 if row["style"] == "big" else cols
            assert len(row["text"]) <= limit, row
        text = receipt["text"]
        assert "TAX INVOICE" in text and "GSTIN: 29ABCDE1234F1Z5" in text and "Table: T1" in text
        assert "2 x Butter Naan" in text and "140.00" in text and "Dal Makhani" in text
        assert "TOTAL" in text and b["grand_total"] in text.replace(",", "")
        assert "Paid CASH" in text and "Balance due" in text  # partly paid

    page = await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"format": "html", "width": 58}, headers=auth_headers(cashier))
    assert page.headers["content-type"].startswith("text/html") and "@page { size: 58mm auto" in page.text
    assert (await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"width": 70}, headers=auth_headers(cashier))).status_code == 422


async def test_takeaway_bills_and_escaping(client, db, cashier):
    item = await factories.make_menu_item(db, name="<b>Biryani</b>", price="250.00")
    order = await create_order(client, cashier, items=[line(item)])
    await fire(client, cashier, order["id"])
    b = await bill(client, cashier, order["id"])
    text = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"format": "text"}, headers=auth_headers(cashier))).text
    assert "TAKEAWAY" in text and "BILL" in text and "TAX INVOICE" not in text  # no GSTIN on this branch
    page = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"format": "html"}, headers=auth_headers(cashier))).text
    assert "<b>Biryani" not in page and "&lt;b&gt;Biryani" in page


async def test_a_reversal_is_checked_before_the_terminal_voids_anything(client, db, cashier, manager, billed):
    _, b = billed
    payment = (await start(client, cashier, b["id"], b["grand_total"])).json()["payment"]
    await complete(client, cashier, payment["id"], APPROVED)
    url = f"{API}/billing/payments/{payment['id']}/reverse"
    wrong = await client.post(url, json={"reason": "wrong amount", "approver_pin": "1111", "check_only": True}, headers=auth_headers(cashier))
    assert wrong.status_code == 403  # the app stops here: nothing is voided on the card
    ok = await client.post(url, json={"reason": "wrong amount", "approver_pin": "9001", "check_only": True}, headers=auth_headers(cashier))
    assert ok.status_code == 200 and ok.json()["payment"]["status"] == "success" and ok.json()["bill"]["status"] == "paid"
    assert (await db.scalars(select(AuditLog).where(AuditLog.action_type == "payment_reverse"))).all() == []
    assert manager.id
