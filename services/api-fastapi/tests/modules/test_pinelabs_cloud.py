"""Pine Labs cloud client: request bodies, paise conversion, configuration and failure handling (HTTP is mocked)."""

import json
from decimal import Decimal

import httpx
import pytest

from app.core.config import settings
from app.modules.pinelabs_cloud import client as pl


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for name, value in (("PINELABS_MERCHANT_ID", "111"), ("PINELABS_SECURITY_TOKEN", "tok"), ("PINELABS_STORE_ID", "22"),
                        ("PINELABS_CLIENT_ID", "33"), ("PINELABS_CLOUD_BASE_URL", "https://pl.test")):
        monkeypatch.setattr(settings, name, value)


def mock_client(answer, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_to_paise():
    assert pl.to_paise(Decimal("5.00")) == "500"
    assert pl.to_paise(Decimal("10.505")) == "1051"
    with pytest.raises(ValueError):
        pl.to_paise(Decimal("0"))


async def test_allowed_payment_modes():
    assert [pl.allowed_payment_mode(m) for m in ("card", "upi", "bharatqr", "other")] == ["1", "11", "11", "1"]
    seen = []
    async with mock_client({"ResponseCode": 0, "PlutusTransactionReferenceID": 7}, seen) as c:
        await pl.upload_transaction("B-2", Decimal("5"), payment_mode=pl.allowed_payment_mode("upi"), client=c)
    assert seen[0][1]["AllowedPaymentMode"] == "11"


async def test_upload_body():
    seen = []
    async with mock_client({"ResponseCode": 0, "PlutusTransactionReferenceID": 7}, seen) as c:
        data = await pl.upload_transaction("B-1", Decimal("5"), client=c)
    url, body = seen[0]
    assert url == "https://pl.test" + pl.UPLOAD_PATH
    assert body["Amount"] == "500" and body["MerchantID"] == 111 and body["StoreID"] == "22" and body["ClientID"] == "33"
    assert body["TransactionNumber"] == "B-1" and body["AutoCancelDurationInMinutes"] == 5
    assert data["PlutusTransactionReferenceID"] == 7


async def test_status_and_cancel_bodies():
    seen = []
    async with mock_client({"ResponseCode": 0}, seen) as c:
        await pl.get_status(7, client=c)
        await pl.cancel_transaction(7, Decimal("5"), client=c)
    assert seen[0][1]["PlutusTransactionReferenceID"] == 7 and seen[0][1]["StoreId"] == "22"
    assert seen[1][1]["Amount"] == "500" and seen[1][1]["StoreID"] == "22"


async def test_errors(monkeypatch):
    async with mock_client(httpx.Response(500, text="boom"), []) as c:
        with pytest.raises(pl.PineLabsError):
            await pl.get_status(1, client=c)
    async with mock_client(httpx.Response(200, text="not json"), []) as c:
        with pytest.raises(pl.PineLabsError):
            await pl.get_status(1, client=c)
    monkeypatch.setattr(settings, "PINELABS_SECURITY_TOKEN", "")
    with pytest.raises(pl.PineLabsNotConfigured):
        await pl.get_status(1)


def test_reading_pine_labs_status_answers():
    from app.modules.pinelabs_cloud import status as st
    approved = st.read_status({"ResponseCode": 0, "ResponseMessage": "TXN APPROVED", "PlutusTransactionReferenceID": 9, "TransactionData": [
        {"Tag": "Approval Code", "Value": "ABC123"}, {"Tag": "Card Number", "Value": "4111111111111111"}, {"Tag": "Secret", "Value": "x"}]})
    assert (approved.state, approved.approval_code) == ("approved", "ABC123")
    assert approved.details["CardNumber"] == "4111111111111111" and "Secret" not in approved.details  # the server masks the card
    assert st.read_status({"ResponseCode": 0, "ResponseMessage": "TXN APPROVED", "PlutusTransactionReferenceID": 9}).approval_code == "PL9"
    assert st.read_status({"ResponseMessage": "TXN UPLOADED"}).state == "pending"
    assert st.read_status({"ResponseMessage": ""}).state == "pending"  # unfamiliar answers never settle a payment
    for message in ("TXN CANCELLED", "TXN DECLINED", "Transaction timed out", "NOT APPROVED"):
        assert st.read_status({"ResponseMessage": message}).state == "declined", message
    assert st.upload_reference({"ResponseCode": 0, "PlutusTransactionReferenceID": 5}) == 5
    assert st.upload_reference({"ResponseCode": 1, "PlutusTransactionReferenceID": 5}) is None
    assert st.upload_reference({"ResponseCode": 0}) is None
    assert st.upload_may_be_on_terminal({"ResponseMessage": "Duplicate transaction number"})


class FakeDb:
    async def commit(self):
        pass


def fake_world(monkeypatch):
    """A pending card payment and an adapter that records what the service completes."""
    from types import SimpleNamespace
    from app.modules.pinelabs_cloud import service as svc
    payment = SimpleNamespace(provider="pinelabs", payment_mode="card", status="pending", amount=Decimal("5"),
                              provider_meta={"billing_ref_no": "REF1234567"})
    sale = SimpleNamespace()
    done = []

    async def load():
        return payment, sale

    async def complete(outcome, code, details, message):
        done.append((outcome, code, message))
        payment.status = "success" if outcome == "approved" else "failed"
        return payment, sale

    async def commit(db):
        pass

    monkeypatch.setattr(svc, "commit", commit)
    return svc, svc.Adapter(load, complete), payment, done


async def test_service_records_only_clear_answers(monkeypatch):
    svc, adapter, payment, done = fake_world(monkeypatch)
    answers = {"upload": {"ResponseCode": 0, "PlutusTransactionReferenceID": 5}, "status": {"ResponseMessage": "TXN UPLOADED"}}

    async def upload(*a, **k):
        return answers["upload"]

    async def status(*a, **k):
        return answers["status"]

    monkeypatch.setattr(pl, "upload_transaction", upload)
    monkeypatch.setattr(pl, "get_status", status)
    assert (await svc.send(FakeDb(), adapter))[2] == "pending" and payment.provider_meta["cloud"]["reference_id"] == 5
    assert (await svc.check(FakeDb(), adapter))[2] == "pending" and not done
    answers["status"] = {"ResponseMessage": "TXN APPROVED", "PlutusTransactionReferenceID": 5}
    assert (await svc.check(FakeDb(), adapter))[2] == "approved" and done == [("approved", "PL5", "TXN APPROVED")]
    assert (await svc.check(FakeDb(), adapter))[2] == "approved" and len(done) == 1  # already recorded: no second completion


async def test_service_refuses_under_one_rupee(monkeypatch):
    svc, adapter, payment, done = fake_world(monkeypatch)
    payment.amount = Decimal("0.50")
    with pytest.raises(Exception) as err:
        await svc.send(FakeDb(), adapter)
    assert getattr(err.value, "code", "") == "terminal_amount_too_small" and not done


async def test_service_keeps_the_payment_pending_when_pine_labs_is_unreachable(monkeypatch):
    svc, adapter, payment, done = fake_world(monkeypatch)

    async def down(*a, **k):
        raise pl.PineLabsError("Could not reach Pine Labs")

    monkeypatch.setattr(pl, "upload_transaction", down)
    assert (await svc.send(FakeDb(), adapter))[2] == "unknown" and not done and payment.status == "pending"
    payment.provider_meta["cloud"] = {"reference_id": 5}
    monkeypatch.setattr(pl, "get_status", down)
    assert (await svc.check(FakeDb(), adapter))[2] == "unknown" and not done


def test_a_real_uat_approval_is_read():
    from app.modules.pinelabs_cloud import status as st
    tags = {"TID": "10146670", "PaymentMode": "CARD", "Amount": "1.00", "BatchNumber": "6", "RRN": "000020", "ApprovalCode": "123456",
            "Invoice Number": "5", "Card Type": "VISA", "Acquirer Name": "HDFC", "Card Holder Name": "A B", "Expiry Date": "1229"}
    data = {"ResponseCode": 0, "ResponseMessage": "TXN APPROVED", "PlutusTransactionReferenceID": 891470,
            "TransactionData": [{"Tag": k, "Value": v} for k, v in tags.items()]}
    reading = st.read_status(data)
    assert (reading.state, reading.approval_code) == ("approved", "123456")
    assert reading.details["RetrievalReferenceNumber"] == "000020" and reading.details["TerminalId"] == "10146670"
    assert reading.details["InvoiceNumber"] == "5" and reading.details["CardType"] == "VISA"
    assert "Card Holder Name" not in reading.details and "Expiry Date" not in reading.details
