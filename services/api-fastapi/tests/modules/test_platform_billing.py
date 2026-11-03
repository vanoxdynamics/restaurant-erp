"""Platform billing: invoices, recorded payments, renewals, reminders, grace and the sweep."""

import asyncio
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.entitlements import compute_entitlements
from app.models import BillingNotification, Invoice, InvoicePayment, Organization, Subscription
from app.modules.platform import billing_service
from app.modules.platform.billing_service import dispatch_notifications, run_billing_sweep
from tests.flows import API
from tests.platform_helpers import P, create_org, get_sub, idem, make_plan, now, operator, org_body, shift_period, tenant_login

D = Decimal
BRANCH = f"{API}/floor/branches"


@pytest.fixture
async def ops(client, db):
    headers, _ = await operator(client, db)
    return headers


@pytest.fixture
async def pro(db):
    return await make_plan(db, "pro")


async def sweep(redis_client, **kw):
    return await run_billing_sweep(redis_client=redis_client, send=False, **kw)


async def kinds(db, org_id):
    return sorted((await db.scalars(select(BillingNotification.kind).where(BillingNotification.organization_id == org_id))).all())


async def invoices(db, org_id):
    return list((await db.scalars(select(Invoice).where(Invoice.organization_id == org_id).order_by(Invoice.issued_at))).all())


async def pay(client, headers, invoice, amount=None, key=None, **extra):
    body = {"amount": str(amount if amount is not None else invoice["amount_due"]), "method": "bank_transfer", **extra}
    return await client.post(f"{P}/invoices/{invoice['id']}/payments", json=body, headers={**headers, **idem(key or uuid.uuid4().hex)})


async def api_invoices(client, headers, org_id):
    return (await client.get(f"{P}/organizations/{org_id}/invoices", headers=headers)).json()


# --- issuing and paying ------------------------------------------------------------------------------------------------


async def test_paid_start_creates_an_activation_invoice(client, db, ops, pro):
    r = await client.post(f"{P}/organizations", json=org_body(pro, term="yearly", start="active"), headers={**ops, **idem("bill-0001")})
    assert r.status_code == 201 and r.json()["invoice_number"].startswith(f"INV-{now().year}-")
    org = r.json()["organization"]
    [inv] = await api_invoices(client, ops, org["id"])
    assert (inv["kind"], inv["status"], inv["amount_due"], inv["balance"], inv["billing_term"]) == ("activation", "open", "10000.00", "10000.00", "yearly")
    assert inv["overdue"] is False and inv["payments"] == []
    # A trial has nothing to bill yet.
    trial = await create_org(client, ops, pro, slug="trial-cafe", key="bill-0002")
    assert await api_invoices(client, ops, trial["id"]) == []


async def test_partial_then_full_payment(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="bill-0003")
    [inv] = await api_invoices(client, ops, org["id"])
    r = await pay(client, ops, inv, amount="400.00", reference="NEFT-1", method="upi")
    assert r.status_code == 201, r.text
    assert (r.json()["settled"], r.json()["renewed"]) == (False, False)
    assert (r.json()["invoice"]["status"], r.json()["invoice"]["balance"]) == ("open", "600.00")
    r = await pay(client, ops, inv, amount="600.00", reference="NEFT-2")
    assert r.json()["settled"] is True and r.json()["invoice"]["status"] == "paid" and r.json()["invoice"]["balance"] == "0.00"
    assert [p["reference"] for p in r.json()["invoice"]["payments"]] == ["NEFT-1", "NEFT-2"]
    assert (await pay(client, ops, inv, amount="1.00")).status_code == 409  # already paid


async def test_payment_validation_and_duplicates(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="bill-0004")
    [inv] = await api_invoices(client, ops, org["id"])
    assert (await pay(client, ops, inv, amount="0")).status_code == 422
    assert (await pay(client, ops, inv, amount="-5")).status_code == 422
    future = (now() + timedelta(days=2)).isoformat()
    r = await pay(client, ops, inv, amount="100", received_at=future)
    assert r.status_code == 422 and r.json()["error"]["code"] == "future_payment"
    assert (await pay(client, ops, inv, amount="100", reference="REF-1")).status_code == 201
    r = await pay(client, ops, inv, amount="100", reference="REF-1")  # same bank reference, new request
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_payment"
    assert (await db.scalar(select(func.count()).select_from(InvoicePayment))) == 1


async def test_retried_payment_request_is_recorded_once(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="bill-0005")
    [inv] = await api_invoices(client, ops, org["id"])
    first = await pay(client, ops, inv, amount="250.00", key="pay-retry-1")
    again = await pay(client, ops, inv, amount="250.00", key="pay-retry-1")
    assert first.status_code == again.status_code == 201 and again.headers.get("idempotent-replay") == "true"
    assert (await db.scalar(select(func.count()).select_from(InvoicePayment))) == 1
    assert (await client.post(f"{P}/invoices/{inv['id']}/payments", json={"amount": "1", "method": "cash"}, headers=ops)).status_code == 400  # key required


async def test_payment_roles(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="bill-0006")
    [inv] = await api_invoices(client, ops, org["id"])
    for role in ("support", "read_only"):
        headers, _ = await operator(client, db, role=role)
        assert (await pay(client, headers, inv, amount="10")).status_code == 403
        assert (await client.get(f"{P}/invoices", headers=headers)).status_code == 200
    billing, _ = await operator(client, db, role="billing")
    assert (await pay(client, billing, inv, amount="10")).status_code == 201


async def test_overpayment_becomes_credit_applied_to_the_next_invoice(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="bill-0007")
    [inv] = await api_invoices(client, ops, org["id"])
    r = await pay(client, ops, inv, amount="1300.00")  # 300 too much
    assert r.json()["settled"] is True and r.json()["credit_added"] == "300.00"
    assert (await get_sub(db, org["id"])).credit_balance == D("300.00")
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    renewal = (await client.post(f"{P}/organizations/{org['id']}/invoices/renewal", headers=ops)).json()
    assert (renewal["amount"], renewal["credit_applied"], renewal["amount_due"]) == ("1000.00", "300.00", "700.00")
    assert (await get_sub(db, org["id"])).credit_balance == D("0.00")


async def test_void_returns_credit_and_blocks_paid_invoices(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="bill-0008")
    [inv] = await api_invoices(client, ops, org["id"])
    await db.execute(update(Subscription).where(Subscription.organization_id == org["id"]).values(credit_balance=D("200.00")))
    await db.commit()
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    renewal = (await client.post(f"{P}/organizations/{org['id']}/invoices/renewal", headers=ops)).json()
    assert renewal["credit_applied"] == "200.00"

    unstepped, _ = await operator(client, db, role="billing", stepped=False)
    r = await client.post(f"{P}/invoices/{renewal['id']}/void", json={"reason": "wrong plan"}, headers=unstepped)
    assert r.json()["error"]["code"] == "step_up_required"
    assert (await client.post(f"{P}/invoices/{renewal['id']}/void", json={}, headers=ops)).status_code == 422  # reason mandatory
    r = await client.post(f"{P}/invoices/{renewal['id']}/void", json={"reason": "issued by mistake"}, headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "void" and r.json()["void_reason"] == "issued by mistake"
    assert (await get_sub(db, org["id"])).credit_balance == D("200.00")  # the credit it consumed is back
    assert (await client.post(f"{P}/invoices/{renewal['id']}/void", json={"reason": "again"}, headers=ops)).status_code == 409

    await pay(client, ops, inv, amount="10")
    r = await client.post(f"{P}/invoices/{inv['id']}/void", json={"reason": "has money on it"}, headers=ops)
    assert r.status_code == 409 and r.json()["error"]["code"] == "has_payments"


# --- renewals ------------------------------------------------------------------------------------------------------------------


async def test_sweep_invoices_the_renewal_and_is_idempotent(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0001")
    await shift_period(db, org["id"], started_days_ago=20, ends_in_days=10)
    sub = await get_sub(db, org["id"])
    end = sub.current_period_end

    first = await sweep(redis_client)
    assert (first["invoices_issued"], first["reminders"], first["errors"]) == (1, 1, 0)
    [renewal] = [i for i in await invoices(db, org["id"]) if i.kind == "renewal"]
    assert (renewal.status, renewal.period_start, renewal.amount_due, renewal.due_at) == ("open", end, D("1000.00"), end)
    assert renewal.period_end > end and renewal.currency == "INR"
    assert await kinds(db, org["id"]) == ["invoice_issued", "renewal_reminder"]

    second = await sweep(redis_client)
    assert (second["invoices_issued"], second["reminders"]) == (0, 0)
    assert len([i for i in await invoices(db, org["id"]) if i.kind == "renewal"]) == 1
    assert await kinds(db, org["id"]) == ["invoice_issued", "renewal_reminder"]


async def test_two_sweeps_at_once_issue_one_invoice(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0002")
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    results = await asyncio.gather(sweep(redis_client), sweep(redis_client), sweep(redis_client))
    assert sum(r["errors"] for r in results) == 0
    assert len([i for i in await invoices(db, org["id"]) if i.kind == "renewal"]) == 1
    assert sum(r["invoices_issued"] for r in results) == 1


async def test_nothing_is_invoiced_too_early_or_when_not_renewing(client, db, ops, pro, redis_client):
    early = await create_org(client, ops, pro, slug="early-cafe", start="active", key="ren-0003")  # 30 days left
    manual = await create_org(client, ops, pro, slug="manual-cafe", start="active", key="ren-0004")
    leaving = await create_org(client, ops, pro, slug="leaving-cafe", start="active", key="ren-0005")
    for o in (manual, leaving):
        await shift_period(db, o["id"], started_days_ago=25, ends_in_days=5)
    await db.execute(update(Subscription).where(Subscription.organization_id == manual["id"]).values(auto_renew=False))
    await db.execute(update(Subscription).where(Subscription.organization_id == leaving["id"]).values(cancel_at_period_end=True, auto_renew=False))
    await db.commit()
    await sweep(redis_client)
    assert [i.kind for i in await invoices(db, early["id"])] == ["activation"]
    assert [i.kind for i in await invoices(db, manual["id"])] == ["activation"]
    assert await kinds(db, manual["id"]) == ["renewal_reminder"]  # told, but not billed
    assert await kinds(db, leaving["id"]) == []  # already leaving: no nagging


async def test_reminders_escalate_without_repeating(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0006")
    for days in (13, 6, 6, 0):  # the third pass is a repeat of the second
        await shift_period(db, org["id"], started_days_ago=30 - days, ends_in_days=days or 1)
        await sweep(redis_client)
    keys = sorted((await db.scalars(select(BillingNotification.dedupe_key).where(BillingNotification.kind == "renewal_reminder"))).all())
    assert sorted(k.rsplit(":", 1)[1] for k in keys) == ["1", "14", "7"]


async def test_paying_the_renewal_extends_without_a_gap(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0007")
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    before = await get_sub(db, org["id"])
    old_end, version = before.current_period_end, before.version
    await sweep(redis_client)
    [renewal] = [i for i in await api_invoices(client, ops, org["id"]) if i["kind"] == "renewal"]
    r = await pay(client, ops, renewal, reference="UTR-100")
    assert r.status_code == 201 and (r.json()["settled"], r.json()["renewed"]) == (True, True)
    sub = await get_sub(db, org["id"])
    assert sub.current_period_start == old_end and sub.current_period_end == renewal_end(renewal) and sub.version > version
    assert sub.status == "active" and sub.grace_until is None
    assert await sweep_noop(redis_client)


def renewal_end(invoice) -> datetime:
    return datetime.fromisoformat(invoice["period_end"])


async def sweep_noop(redis_client) -> bool:
    s = await sweep(redis_client)
    return s["invoices_issued"] == 0 and s["past_due"] == 0 and s["expired"] == 0


async def test_scheduled_term_change_is_invoiced_and_applied_on_payment(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0008")
    sub = await get_sub(db, org["id"])
    r = await client.put(f"{P}/organizations/{org['id']}/subscription", headers={**ops, **idem("ren-term-01")},
                         json={"billing_term": "yearly", "effective": "next_renewal", "reason": "annual from next renewal", "version": sub.version})
    assert r.status_code == 200 and r.json()["subscription"]["pending_billing_term"] == "yearly"
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    await sweep(redis_client)
    [renewal] = [i for i in await api_invoices(client, ops, org["id"]) if i["kind"] == "renewal"]
    assert (renewal["billing_term"], renewal["amount"]) == ("yearly", "10000.00")  # billed at the scheduled terms
    assert (await get_sub(db, org["id"])).billing_term == "monthly"  # not yet applied: unpaid

    await pay(client, ops, renewal)
    sub = await get_sub(db, org["id"])
    assert (sub.billing_term, sub.price_amount, sub.pending_billing_term, sub.pending_price_amount) == ("yearly", D("10000.00"), None, None)
    assert 364 <= (sub.current_period_end - sub.current_period_start).days <= 366


async def test_late_payment_after_grace_starts_the_period_today(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0009")
    await shift_period(db, org["id"], started_days_ago=60, ends_in_days=-30)  # lapsed far beyond the 7-day grace
    renewal = (await client.post(f"{P}/organizations/{org['id']}/invoices/renewal", headers=ops)).json()
    await pay(client, ops, renewal)
    sub = await get_sub(db, org["id"])
    assert abs((sub.current_period_start - now()).total_seconds()) < 60  # no free time for the lockout
    assert 27 <= (sub.current_period_end - now()).days <= 31 and sub.status == "active"


async def test_manual_renewal_issue_is_idempotent_and_refuses_trials(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="ren-0010")
    a = (await client.post(f"{P}/organizations/{org['id']}/invoices/renewal", headers=ops)).json()
    b = (await client.post(f"{P}/organizations/{org['id']}/invoices/renewal", headers=ops)).json()
    assert a["id"] == b["id"]
    trial = await create_org(client, ops, pro, slug="trial-cafe", key="ren-0011")
    r = await client.post(f"{P}/organizations/{trial['id']}/invoices/renewal", headers=ops)
    assert r.status_code == 422 and r.json()["error"]["code"] == "trialing"
    support, _ = await operator(client, db, role="support")
    assert (await client.post(f"{P}/organizations/{org['id']}/invoices/renewal", headers=support)).status_code == 403


async def test_credit_that_covers_the_renewal_renews_without_payment(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="ren-0012")
    await db.execute(update(Subscription).where(Subscription.organization_id == org["id"]).values(credit_balance=D("1500.00")))
    await db.commit()
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    old_end = (await get_sub(db, org["id"])).current_period_end
    await sweep(redis_client)
    sub = await get_sub(db, org["id"])
    [renewal] = [i for i in await invoices(db, org["id"]) if i.kind == "renewal"]
    assert (renewal.status, renewal.amount_due, renewal.credit_applied) == ("paid", D("0.00"), D("1000.00"))
    assert sub.credit_balance == D("500.00") and sub.current_period_start == old_end


# --- lapse: past due -> grace -> read-only ---------------------------------------------------------------------------------


async def test_unpaid_renewal_goes_past_due_then_read_only_then_recovers(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="lap-0001")
    tenant = await tenant_login(client, org_body(pro))
    await shift_period(db, org["id"], started_days_ago=32, ends_in_days=-2)  # two days into the 7-day grace

    s = await sweep(redis_client)
    assert (s["past_due"], s["invoices_issued"], s["expired"]) == (1, 1, 0)
    sub = await get_sub(db, org["id"])
    assert sub.status == "past_due" and (await db.get(Organization, org["id"])).status == "past_due"
    assert (await compute_entitlements(db, org["id"])).access == "full"  # grace: still working
    assert (await client.post(BRANCH, json={"name": "X", "code": "XX1"}, headers=tenant)).status_code == 201
    assert {"payment_overdue", "invoice_issued"} <= set(await kinds(db, org["id"]))

    await shift_period(db, org["id"], started_days_ago=60, ends_in_days=-30)  # grace is over
    s = await sweep(redis_client)
    assert (s["expired"], s["past_due"]) == (1, 0)
    assert (await get_sub(db, org["id"])).status == "expired"
    r = await client.post(BRANCH, json={"name": "Y", "code": "YY1"}, headers=tenant)
    assert r.status_code == 403 and r.json()["error"]["code"] == "subscription_inactive"
    assert (await client.get(f"{API}/orders", headers=tenant)).status_code == 200  # still readable
    assert "access_restricted" in await kinds(db, org["id"])
    again = await sweep(redis_client)
    assert (again["expired"], again["past_due"], again["invoices_issued"]) == (0, 0, 0)  # second pass changes nothing

    [renewal] = [i for i in await api_invoices(client, ops, org["id"]) if i["kind"] == "renewal"]
    assert renewal["overdue"] is True
    assert (await pay(client, ops, renewal)).json()["renewed"] is True
    assert (await client.post(BRANCH, json={"name": "Z", "code": "ZZ1"}, headers=tenant)).status_code == 201  # access is back at once
    assert (await db.get(Organization, org["id"])).status == "active"


async def test_cancel_at_period_end_becomes_cancelled_without_billing(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="lap-0002")
    await client.post(f"{P}/organizations/{org['id']}/cancel", json={"reason": "customer leaving"}, headers=ops)
    await shift_period(db, org["id"], started_days_ago=31, ends_in_days=-1)
    s = await sweep(redis_client)
    assert s["cancelled"] == 1 and (await get_sub(db, org["id"])).status == "cancelled"
    assert [i.kind for i in await invoices(db, org["id"])] == ["activation"]  # no renewal invoice was ever raised


async def test_trial_reminders_and_expiry(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, key="tri-0001")
    await db.execute(update(Subscription).where(Subscription.organization_id == org["id"]).values(trial_ends_at=now() + timedelta(days=2)))
    await db.commit()
    s = await sweep(redis_client)
    assert s["reminders"] == 1 and await kinds(db, org["id"]) == ["trial_ending"]
    assert (await sweep(redis_client))["reminders"] == 0
    await db.execute(update(Subscription).where(Subscription.organization_id == org["id"]).values(trial_ends_at=now() - timedelta(hours=1)))
    await db.commit()
    s = await sweep(redis_client)
    assert s["trials_expired"] == 1 and (await get_sub(db, org["id"])).status == "expired"
    assert (await compute_entitlements(db, org["id"])).access == "read_only"
    assert "trial_expired" in await kinds(db, org["id"])


async def test_one_bad_subscription_does_not_stop_the_sweep(client, db, ops, pro, redis_client, monkeypatch):
    a = await create_org(client, ops, pro, slug="alpha-cafe", start="active", key="bad-0001")
    b = await create_org(client, ops, pro, slug="bravo-bar", start="active", key="bad-0002")
    for o in (a, b):
        await shift_period(db, o["id"], started_days_ago=25, ends_in_days=5)
    real = billing_service.issue_renewal_invoice

    async def flaky(db_, org, sub, actor_id, **kw):
        if org.slug == "alpha-cafe":
            raise RuntimeError("boom")
        return await real(db_, org, sub, actor_id, **kw)

    monkeypatch.setattr(billing_service, "issue_renewal_invoice", flaky)
    s = await sweep(redis_client)
    assert s["errors"] == 1 and s["invoices_issued"] == 1
    assert [i.kind for i in await invoices(db, b["id"])] == ["activation", "renewal"]
    assert [i.kind for i in await invoices(db, a["id"])] == ["activation"]


# --- proration money ---------------------------------------------------------------------------------------------------------------


async def test_term_change_now_invoices_the_prorated_amount(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="pro-0001")
    await shift_period(db, org["id"], started_days_ago=15, ends_in_days=15)
    sub = await get_sub(db, org["id"])
    r = await client.put(f"{P}/organizations/{org['id']}/subscription", headers={**ops, **idem("pro-term-01")},
                         json={"billing_term": "yearly", "reason": "customer prefers annual", "version": sub.version})
    assert r.status_code == 200, r.text
    assert r.json()["invoice_number"]
    [change] = [i for i in await api_invoices(client, ops, org["id"]) if i["kind"] == "plan_change"]
    assert change["amount_due"] == r.json()["change"]["amount_due_now"] and D(change["amount_due"]) > D("9000")
    assert change["billing_term"] == "yearly"


async def test_downgrade_keeps_the_unused_value_as_credit(client, db, ops, pro):
    org = await create_org(client, ops, pro, term="yearly", start="active", key="pro-0002")
    await shift_period(db, org["id"], started_days_ago=15, ends_in_days=350)
    sub = await get_sub(db, org["id"])
    r = await client.put(f"{P}/organizations/{org['id']}/subscription", headers={**ops, **idem("pro-term-02")},
                         json={"billing_term": "monthly", "reason": "moving to monthly", "version": sub.version})
    assert r.status_code == 200 and r.json()["invoice_number"] is None
    credit = (await get_sub(db, org["id"])).credit_balance
    assert D("8000") < credit < D("10000") and r.json()["subscription"]["credit_balance"] == str(credit)
    detail = (await client.get(f"{P}/organizations/{org['id']}", headers=ops)).json()
    assert detail["subscription"]["credit_balance"] == str(credit)


async def test_activating_a_trial_applies_available_credit(client, db, ops, pro):
    org = await create_org(client, ops, pro, key="pro-0003")
    await db.execute(update(Subscription).where(Subscription.organization_id == org["id"]).values(credit_balance=D("400.00")))
    await db.commit()
    r = await client.post(f"{P}/organizations/{org['id']}/activate", json={"reason": "pays by transfer"}, headers={**ops, **idem("pro-act-01")})
    assert r.status_code == 200
    assert (r.json()["amount_due"], r.json()["credit_applied"]) == ("600.00", "400.00") and r.json()["invoice_number"]


# --- notifications, dashboard, operations --------------------------------------------------------------------------------------------


class Capture:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    async def send(self, to, subject, body):
        if subject in self.fail_for:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, body))


async def test_dispatch_sends_once_and_retries_failures(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="not-0001")
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    await sweep(redis_client)
    flaky = Capture(fail_for={"Your subscription renews soon"})
    result = await dispatch_notifications(flaky)
    assert result == {"sent": 1, "failed": 1}
    assert flaky.sent[0][0] == "billing@acme.example" and "INV-" in flaky.sent[0][1]
    healthy = Capture()
    assert await dispatch_notifications(healthy) == {"sent": 1, "failed": 0}  # only the failed one is retried
    assert await dispatch_notifications(Capture()) == {"sent": 0, "failed": 0}
    assert (await db.scalar(select(func.count()).select_from(BillingNotification).where(BillingNotification.sent_at.is_(None)))) == 0


async def test_dashboard_shows_outstanding_and_overdue(client, db, ops, pro):
    a = await create_org(client, ops, pro, slug="alpha-cafe", start="active", key="dash-0001")  # 1000 open
    b = await create_org(client, ops, pro, slug="bravo-bar", term="yearly", start="active", key="dash-0002")  # 10000 open
    [inv_b] = await api_invoices(client, ops, b["id"])
    await pay(client, ops, inv_b, amount="4000.00")
    await db.execute(update(Invoice).where(Invoice.organization_id == a["id"]).values(due_at=now() - timedelta(days=3)))
    await db.commit()
    d = (await client.get(f"{P}/dashboard", headers=ops)).json()
    assert d["outstanding"] == {"INR": "7000.00"} and d["open_invoices"] == 2 and d["overdue_invoices"] == 1
    overdue = (await client.get(f"{P}/invoices", params={"overdue": "true"}, headers=ops)).json()
    assert [i["organization_name"] for i in overdue["items"]] == ["Acme Diner"] and overdue["total"] == 1
    assert (await client.get(f"{P}/invoices", params={"status": "paid"}, headers=ops)).json()["total"] == 0


async def test_manual_sweep_endpoint_needs_super_admin_and_step_up(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="run-0001")
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    r = await client.post(f"{P}/billing/run", headers=ops)
    assert r.status_code == 200 and r.json()["result"]["invoices_issued"] == 1
    billing, _ = await operator(client, db, role="billing")
    assert (await client.post(f"{P}/billing/run", headers=billing)).status_code == 403
    unstepped, _ = await operator(client, db, role="super_admin", stepped=False, email="second@example.com")
    assert (await client.post(f"{P}/billing/run", headers=unstepped)).json()["error"]["code"] == "step_up_required"


async def test_worker_entry_point_runs_a_pass(client, db, ops, pro):
    from app.worker.billing import run_once

    org = await create_org(client, ops, pro, start="active", key="wrk-0001")
    await shift_period(db, org["id"], started_days_ago=25, ends_in_days=5)
    summary = await run_once()
    assert summary["invoices_issued"] == 1 and summary["errors"] == 0
    assert summary["mail_sent"] >= 1
    assert [i.kind for i in await invoices(db, org["id"])] == ["activation", "renewal"]
