"""Card/UPI payments through Pine Labs cloud: send, check and cancel record the payment only on a clear answer (Pine Labs mocked)."""

import pytest

from app.core.config import settings
from app.modules.pinelabs_cloud import client as pl
from tests import factories
from tests.factories import auth_headers, idem
from tests.flows import API, bill, create_order, fire, line


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for name, value in (("PINELABS_MERCHANT_ID", "111"), ("PINELABS_SECURITY_TOKEN", "tok"), ("PINELABS_STORE_ID", "22"),
                        ("PINELABS_CLIENT_ID", "33")):
        monkeypatch.setattr(settings, name, value)


@pytest.fixture
def pine(monkeypatch):
    """Scripted Pine Labs: set .upload / .status / .cancel answers (dicts, or an exception to raise)."""
    class Fake:
        upload = {"ResponseCode": 0, "ResponseMessage": "TXN UPLOADED", "PlutusTransactionReferenceID": 77}
        status = {"ResponseCode": 1001, "ResponseMessage": "TXN UPLOADED"}
        cancel = {"ResponseCode": 0, "ResponseMessage": "OK"}

    fake = Fake()
    fake.calls = []

    def answer(kind):
        async def call(*args, **kwargs):
            fake.calls.append((kind, args))
            value = getattr(fake, kind)
            if isinstance(value, Exception):
                raise value
            return value
        return call

    monkeypatch.setattr(pl, "upload_transaction", answer("upload"))
    monkeypatch.setattr(pl, "get_status", answer("status"))
    monkeypatch.setattr(pl, "cancel_transaction", answer("cancel"))
    return fake


@pytest.fixture
async def pending(client, db, cashier, table):
    item = await factories.make_menu_item(db, name="Thali", price="400.00")
    order = await create_order(client, cashier, table, items=[line(item)])
    await fire(client, cashier, order["id"])
    b = await bill(client, cashier, order["id"])
    r = await client.post(f"{API}/billing/bills/{b['id']}/terminal-payments",
                          json={"payment_mode": "card", "amount": str(b["grand_total"])}, headers=idem(cashier))
    assert r.status_code == 201, r.text
    return r.json()["payment"]


async def cloud(client, user, payment, action):
    return await client.post(f"{API}/billing/payments/{payment['id']}/cloud/{action}", headers=auth_headers(user))


async def test_config_reports_whether_cloud_is_set_up(client, cashier, monkeypatch):
    assert (await client.get(f"{API}/billing/terminal-cloud", headers=auth_headers(cashier))).json() == {"enabled": True}
    monkeypatch.setattr(settings, "PINELABS_SECURITY_TOKEN", "")
    assert (await client.get(f"{API}/billing/terminal-cloud", headers=auth_headers(cashier))).json() == {"enabled": False}


async def test_send_then_approval_settles_the_bill(client, cashier, pending, pine):
    r = await cloud(client, cashier, pending, "send")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "pending" and r.json()["payment"]["status"] == "pending"
    assert pine.calls[0][0] == "upload"

    assert (await cloud(client, cashier, pending, "check")).json()["state"] == "pending"  # still waiting: nothing recorded

    pine.status = {"ResponseCode": 0, "ResponseMessage": "TXN APPROVED", "PlutusTransactionReferenceID": 77,
                   "TransactionData": [{"Tag": "Approval Code", "Value": "OK1234"}]}
    body = (await cloud(client, cashier, pending, "check")).json()
    assert body["state"] == "approved" and body["payment"]["status"] == "success" and body["payment"]["reference_id"] == "OK1234"
    assert body["bill"]["status"] == "paid"
    assert (await cloud(client, cashier, pending, "check")).json()["state"] == "approved"  # repeating is harmless


async def test_sending_twice_does_not_upload_twice(client, cashier, pending, pine):
    await cloud(client, cashier, pending, "send")
    await cloud(client, cashier, pending, "send")
    assert [c[0] for c in pine.calls].count("upload") == 1


async def test_a_refused_upload_fails_the_payment_and_the_bill_stays_open(client, cashier, pending, pine):
    pine.upload = {"ResponseCode": 2001, "ResponseMessage": "INVALID STORE"}
    body = (await cloud(client, cashier, pending, "send")).json()
    assert body["state"] == "declined" and body["payment"]["status"] == "failed" and body["bill"]["status"] == "unpaid"


async def test_a_network_failure_leaves_the_payment_pending(client, cashier, pending, pine):
    pine.upload = pl.PineLabsError("Could not reach Pine Labs")
    body = (await cloud(client, cashier, pending, "send")).json()
    assert body["state"] == "unknown" and body["payment"]["status"] == "pending"


async def test_cancel_records_a_cancelled_payment(client, cashier, pending, pine):
    await cloud(client, cashier, pending, "send")
    pine.status = {"ResponseMessage": "TXN CANCELLED"}
    body = (await cloud(client, cashier, pending, "cancel")).json()
    assert body["state"] == "declined" and body["payment"]["status"] == "failed"


async def test_cancel_after_the_customer_paid_records_the_payment(client, cashier, pending, pine):
    await cloud(client, cashier, pending, "send")
    pine.cancel = {"ResponseCode": 5, "ResponseMessage": "ALREADY PAID"}
    pine.status = {"ResponseMessage": "TXN APPROVED", "PlutusTransactionReferenceID": 77}
    body = (await cloud(client, cashier, pending, "cancel")).json()
    assert body["state"] == "approved" and body["bill"]["status"] == "paid"


async def test_not_configured_is_refused(client, cashier, pending, monkeypatch):
    monkeypatch.setattr(settings, "PINELABS_SECURITY_TOKEN", "")
    assert (await cloud(client, cashier, pending, "send")).status_code == 422
