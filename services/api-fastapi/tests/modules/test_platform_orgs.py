"""Platform API: subscriber lifecycle, billing terms with proration, module switches, plans, impersonation."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.entitlements import invalidate_entitlements
from app.core.billing_periods import add_term, compute_change, unused_credit
from app.core.modules import ModuleKey
from app.models import OrganizationModule, PlatformAuditLog, StaffUser, SubscriptionEvent
from app.modules.platform.module_service import _dependency_problem
from app.realtime.connection_manager import RealtimeConnectionManager
from tests.factories import auth_headers
from tests.flows import API
from tests.platform_helpers import (
    P,
    create_org,
    get_sub,
    idem,
    make_plan,
    now,
    operator,
    org_body,
    shift_period,
    tenant_login,
)

ANALYTICS = f"{API}/analytics/kpis/live"
D = Decimal


@pytest.fixture
async def ops(client, db):
    headers, admin = await operator(client, db)
    return headers


@pytest.fixture
async def pro(db):
    return await make_plan(db, "pro")


@pytest.fixture
async def basic(db):
    return await make_plan(db, "basic", modules=("pos",), monthly="500.00", yearly="5000.00", max_branches=1)


# --- billing math (pure) ----------------------------------------------------------------------------------------


def test_add_term_is_calendar_aware():
    jan31 = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert add_term(jan31, "monthly") == datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert add_term(datetime(2024, 2, 29, tzinfo=timezone.utc), "yearly") == datetime(2025, 2, 28, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        add_term(jan31, "weekly")


def test_unused_credit_is_proportional_and_bounded():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert unused_credit(D("300"), start, end, datetime(2026, 1, 16, tzinfo=timezone.utc)) == D("150.00")
    assert unused_credit(D("300"), start, end, end + timedelta(days=1)) == D("0.00")
    assert unused_credit(D("300"), start, None, start) == D("0.00")
    assert unused_credit(D("300"), start, end, start - timedelta(days=5)) == D("300.00")  # never more than paid


def test_change_now_credits_unused_time():
    t = datetime(2026, 1, 16, tzinfo=timezone.utc)
    p = compute_change(now=t, effective="now", current_term="monthly", current_price=D("1000"),
                       period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
                       paid=True, new_term="yearly", new_price=D("10000"))
    assert (p.credit_for_unused_time, p.amount_due_now, p.account_credit_remaining) == (D("500.00"), D("9500.00"), D("0.00"))
    assert p.new_period_start == t and p.new_period_end == datetime(2027, 1, 16, tzinfo=timezone.utc)


def test_downgrade_leaves_account_credit_and_trial_has_none():
    t = datetime(2026, 1, 16, tzinfo=timezone.utc)
    kw = dict(now=t, effective="now", current_term="yearly", current_price=D("10000"),
              period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=datetime(2026, 12, 31, tzinfo=timezone.utc),
              new_term="monthly", new_price=D("1000"))
    paid = compute_change(paid=True, **kw)
    assert paid.amount_due_now == D("0.00") and paid.account_credit_remaining > D("8000")
    assert compute_change(paid=False, **kw).amount_due_now == D("1000.00")


def test_scheduled_change_moves_no_money_until_renewal():
    t = datetime(2026, 1, 16, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    p = compute_change(now=t, effective="next_renewal", current_term="monthly", current_price=D("1000"),
                       period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=end, paid=True,
                       new_term="yearly", new_price=D("10000"))
    assert p.effective == "next_renewal" and p.effective_at == end and p.amount_due_now == D("0.00")
    assert p.new_period_start == end and p.new_period_end == datetime(2027, 1, 31, tzinfo=timezone.utc)


def test_dependency_rules():
    assert _dependency_problem("analytics", True, frozenset({"pos"})) is None
    assert _dependency_problem("analytics", True, frozenset())["modules"] == ["pos"]
    assert _dependency_problem("pos", False, frozenset({"pos", "analytics"}))["modules"] == ["analytics"]
    assert _dependency_problem("analytics", False, frozenset({"pos", "analytics"})) is None
    assert ModuleKey.ANALYTICS.value == "analytics"


# --- onboarding ------------------------------------------------------------------------------------------------------


async def test_create_subscriber_end_to_end(client, db, ops, pro):
    org = await create_org(client, ops, pro)
    assert org["status"] == "trial" and org["billing_email"] == "billing@acme.example"
    assert org["subscription"]["status"] == "trialing" and org["subscription"]["billing_term"] == "monthly"
    assert org["subscription"]["trial_ends_at"] and org["branches"] == 1 and org["active_staff"] == 1
    assert {m["key"] for m in org["modules"] if m["enabled"]} == {"pos", "analytics"}

    # The owner we created can sign in and sees their entitlements.
    body = org_body(pro)
    headers = await tenant_login(client, body)
    ent = (await client.get(f"{API}/me/entitlements", headers=headers)).json()
    assert ent["access"] == "full" and ent["plan_code"] == "pro" and ent["billing_term"] == "monthly"
    assert (await client.get(ANALYTICS, headers=headers)).status_code == 200
    assert "org.create" in (await db.scalars(select(PlatformAuditLog.action))).all()


async def test_create_active_yearly_returns_amount_due(client, db, ops, pro):
    r = await client.post(f"{P}/organizations", json=org_body(pro, term="yearly", start="active"),
                          headers={**ops, **idem("create-key-2")})
    assert r.status_code == 201
    assert r.json()["amount_due"] == "10000.00"
    sub = r.json()["organization"]["subscription"]
    assert sub["status"] == "active" and sub["billing_term"] == "yearly"
    end = datetime.fromisoformat(sub["current_period_end"])
    assert 364 <= (end - now()).days <= 366


async def test_create_is_idempotent_and_requires_key(client, db, ops, pro):
    body = org_body(pro)
    first = await client.post(f"{P}/organizations", json=body, headers={**ops, **idem("same-key-1")})
    again = await client.post(f"{P}/organizations", json=body, headers={**ops, **idem("same-key-1")})
    assert first.status_code == again.status_code == 201 and again.headers.get("idempotent-replay") == "true"
    assert again.json()["organization"]["id"] == first.json()["organization"]["id"]
    listed = (await client.get(f"{P}/organizations", headers=ops)).json()
    assert listed["total"] == 1
    assert (await client.post(f"{P}/organizations", json=body, headers=ops)).status_code == 400
    other = {**body, "name": "Changed"}
    assert (await client.post(f"{P}/organizations", json=other, headers={**ops, **idem("same-key-1")})).status_code == 422


async def test_create_conflicts_and_validation(client, db, ops, pro):
    await create_org(client, ops, pro)
    r = await client.post(f"{P}/organizations", json=org_body(pro), headers={**ops, **idem("dup-key-001")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "slug_taken"

    body = org_body(pro, slug="other-diner")
    body["owner"]["branch_code"] = org_body(pro)["owner"]["branch_code"]
    r = await client.post(f"{P}/organizations", json=body, headers={**ops, **idem("dup-key-002")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "branch_code_taken"

    body = org_body(pro, slug="third-diner")
    body["owner"]["username"] = org_body(pro)["owner"]["username"]
    r = await client.post(f"{P}/organizations", json=body, headers={**ops, **idem("dup-key-003")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "username_taken"

    noprice = await make_plan(db, "noyearly", yearly=None)
    r = await client.post(f"{P}/organizations", json=org_body(noprice, slug="fourth-diner", term="yearly"),
                          headers={**ops, **idem("dup-key-004")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_required"
    r = await client.post(f"{P}/organizations", json={**org_body(pro, slug="Bad Slug!")}, headers={**ops, **idem("dup-key-005")})
    assert r.status_code == 422


async def test_failed_creation_leaves_nothing_behind(client, db, ops, pro):
    await create_org(client, ops, pro)
    body = org_body(pro, slug="ghost-diner")
    body["owner"]["username"] = org_body(pro)["owner"]["username"]  # collides after the org row is flushed
    assert (await client.post(f"{P}/organizations", json=body, headers={**ops, **idem("rollback-1")})).status_code == 409
    listed = (await client.get(f"{P}/organizations", params={"q": "ghost"}, headers=ops)).json()
    assert listed["total"] == 0


@pytest.mark.parametrize("role,can_create,can_suspend", [
    ("support", False, False),
    ("read_only", False, False),
    ("billing", True, True),
    ("super_admin", True, True),
])
async def test_role_matrix(client, db, ops, pro, role, can_create, can_suspend):
    target = await create_org(client, ops, pro, slug="seed-diner", key="seed-key-1")
    headers, _ = await operator(client, db, role=role if role != "super_admin" else "billing") if role != "super_admin" else (ops, None)
    r = await client.post(f"{P}/organizations", json=org_body(pro, slug="role-probe"), headers={**headers, **idem(f"role-{role}-1")})
    assert r.status_code == (201 if can_create else 403), r.text
    assert (await client.get(f"{P}/organizations", headers=headers)).status_code == 200
    assert (await client.get(f"{P}/dashboard", headers=headers)).status_code == 200
    r = await client.post(f"{P}/organizations/{target['id']}/suspend", json={"reason": "role probe"}, headers=headers)
    assert r.status_code == (200 if can_suspend else 403), r.text


async def test_list_filters(client, db, ops, pro, basic):
    a = await create_org(client, ops, pro, slug="alpha-cafe", key="list-a-001")
    b = await create_org(client, ops, basic, slug="bravo-bar", term="yearly", start="active", key="list-b-001")
    await shift_period(db, b["id"], started_days_ago=360, ends_in_days=5)

    async def ids(**params):
        return {o["id"] for o in (await client.get(f"{P}/organizations", params=params, headers=ops)).json()["items"]}

    assert await ids() == {a["id"], b["id"]}
    assert await ids(q="ALPHA") == {a["id"]}
    assert await ids(status="trial") == {a["id"]} and await ids(status="active") == {b["id"]}
    assert await ids(term="yearly") == {b["id"]} and await ids(plan_id=str(pro.id)) == {a["id"]}
    assert await ids(subscription_status="active") == {b["id"]}
    assert b["id"] in await ids(expiring_within_days=7)
    assert (await client.get(f"{P}/organizations", params={"limit": 1}, headers=ops)).json()["total"] == 2


# --- suspend / reinstate / cancel / activate ---------------------------------------------------------------------------


async def test_suspend_cuts_off_tenant_immediately_and_reinstate_restores(client, db, ops, pro, events):
    org = await create_org(client, ops, pro)
    tenant = await tenant_login(client, org_body(pro))
    assert (await client.get(f"{API}/orders", headers=tenant)).status_code == 200  # warms the entitlement cache

    r = await client.post(f"{P}/organizations/{org['id']}/suspend", json={"reason": "unpaid invoice 42"}, headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "suspended" and r.json()["access"] == "blocked"
    assert r.json()["suspension_reason"] == "unpaid invoice 42"

    # The existing token is dead, and a fresh sign-in gets nothing but a clear "suspended".
    assert (await client.get(f"{API}/orders", headers=tenant)).status_code == 401
    fresh = await tenant_login(client, org_body(pro))
    r = await client.get(f"{API}/orders", headers=fresh)
    assert r.status_code == 403 and r.json()["error"]["code"] == "organization_suspended"
    assert any(e["type"] == "access.revoked" and e["topic"].value == "entitlements" for e in events)

    assert (await client.post(f"{P}/organizations/{org['id']}/suspend", json={"reason": "again"}, headers=ops)).status_code == 409
    r = await client.post(f"{P}/organizations/{org['id']}/reinstate", json={"reason": "paid"}, headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "trial" and r.json()["suspended_at"] is None
    again = await tenant_login(client, org_body(pro))
    assert (await client.get(f"{API}/orders", headers=again)).status_code == 200


async def test_suspend_needs_reason_and_step_up_and_only_hits_that_org(client, db, ops, pro):
    a = await create_org(client, ops, pro, slug="alpha-cafe", key="s-a-0001")
    await create_org(client, ops, pro, slug="bravo-bar", key="s-b-0001")
    other_tenant = await tenant_login(client, org_body(pro, slug="bravo-bar"))
    assert (await client.post(f"{P}/organizations/{a['id']}/suspend", json={}, headers=ops)).status_code == 422
    fresh, _ = await operator(client, db, role="billing", stepped=False)
    r = await client.post(f"{P}/organizations/{a['id']}/suspend", json={"reason": "no step up"}, headers=fresh)
    assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"
    await client.post(f"{P}/organizations/{a['id']}/suspend", json={"reason": "bad debt"}, headers=ops)
    assert (await client.get(f"{API}/orders", headers=other_tenant)).status_code == 200  # neighbours unaffected


async def test_reinstate_requires_suspension(client, db, ops, pro):
    org = await create_org(client, ops, pro)
    r = await client.post(f"{P}/organizations/{org['id']}/reinstate", json={"reason": "oops"}, headers=ops)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_suspended"


async def test_cancel_at_period_end_then_lapse(client, db, ops, pro, redis_client):
    org = await create_org(client, ops, pro, start="active", key="c-0000001")
    tenant = await tenant_login(client, org_body(pro))
    r = await client.post(f"{P}/organizations/{org['id']}/cancel", json={"reason": "customer asked"}, headers=ops)
    assert r.status_code == 200
    sub = r.json()["subscription"]
    assert sub["cancel_at_period_end"] is True and sub["auto_renew"] is False and sub["status"] == "active"
    assert (await client.post(f"{API}/floor/branches", json={"name": "X", "code": "XX1"}, headers=tenant)).status_code == 201

    await shift_period(db, org["id"], started_days_ago=31, ends_in_days=-1)  # paid period over, still inside "grace"
    await invalidate_entitlements(redis_client, uuid.UUID(org["id"]))
    r = await client.post(f"{API}/floor/branches", json={"name": "Y", "code": "YY1"}, headers=tenant)
    assert r.status_code == 403 and r.json()["error"]["code"] == "subscription_inactive"  # no grace after cancelling
    assert (await client.get(f"{API}/orders", headers=tenant)).status_code == 200  # but data stays readable


async def test_cancel_immediately(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="c-0000002")
    r = await client.post(f"{P}/organizations/{org['id']}/cancel", json={"reason": "fraud", "at_period_end": False}, headers=ops)
    assert r.json()["subscription"]["status"] == "cancelled" and r.json()["access"] == "read_only"
    assert (await client.post(f"{P}/organizations/{org['id']}/cancel", json={"reason": "again"}, headers=ops)).status_code == 409


async def test_activate_converts_trial(client, db, ops, pro):
    org = await create_org(client, ops, pro, term="yearly")
    r = await client.post(f"{P}/organizations/{org['id']}/activate", json={"reason": "payment received"},
                          headers={**ops, **idem("act-00001")})
    assert r.status_code == 200
    body = r.json()
    assert (body["amount_due"], body["currency"], body["credit_applied"]) == ("10000.00", "INR", "0.00")
    assert body["invoice_number"].startswith("INV-")
    detail = (await client.get(f"{P}/organizations/{org['id']}", headers=ops)).json()
    assert detail["status"] == "active" and detail["subscription"]["status"] == "active"
    assert detail["subscription"]["trial_ends_at"] is None
    replay = await client.post(f"{P}/organizations/{org['id']}/activate", json={"reason": "payment received"},
                               headers={**ops, **idem("act-00001")})
    assert replay.headers.get("idempotent-replay") == "true"


# --- billing term changes --------------------------------------------------------------------------------------------------


async def test_switch_monthly_to_yearly_with_proration(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="t-0000001")
    await shift_period(db, org["id"], started_days_ago=15, ends_in_days=15)
    version = (await get_sub(db, org["id"])).version

    change = {"billing_term": "yearly", "effective": "now"}
    preview = await client.post(f"{P}/organizations/{org['id']}/subscription/preview-change", json=change, headers=ops)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert D(p["credit_for_unused_time"]) == pytest.approx(D("500"), abs=D("5"))
    assert D(p["amount_due_now"]) == D("10000") - D(p["credit_for_unused_time"])
    assert (await get_sub(db, org["id"])).billing_term == "monthly"  # previewing changes nothing

    r = await client.put(f"{P}/organizations/{org['id']}/subscription",
                         json={**change, "reason": "customer prefers annual", "version": version}, headers={**ops, **idem("term-00001")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subscription"]["billing_term"] == "yearly" and body["subscription"]["price_amount"] == "10000.00"
    assert body["subscription"]["version"] == version + 1
    assert body["change"]["amount_due_now"] == p["amount_due_now"] or abs(D(body["change"]["amount_due_now"]) - D(p["amount_due_now"])) < D("1")
    end = datetime.fromisoformat(body["subscription"]["current_period_end"])
    assert 364 <= (end - now()).days <= 366
    events = (await db.scalars(select(SubscriptionEvent.event_type).where(SubscriptionEvent.organization_id == org["id"]))).all()
    assert "term_changed" in events
    assert "subscription.update" in (await db.scalars(select(PlatformAuditLog.action))).all()

    # The tenant sees the new term at once.
    tenant = await tenant_login(client, org_body(pro))
    assert (await client.get(f"{API}/me/entitlements", headers=tenant)).json()["billing_term"] == "yearly"


async def test_scheduled_term_change_applies_at_renewal(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="t-0000002")
    sub = await get_sub(db, org["id"])
    r = await client.put(f"{P}/organizations/{org['id']}/subscription",
                         json={"billing_term": "yearly", "effective": "next_renewal", "reason": "switch at renewal",
                               "version": sub.version}, headers={**ops, **idem("term-00002")})
    assert r.status_code == 200, r.text
    s = r.json()["subscription"]
    assert s["billing_term"] == "monthly" and s["pending_billing_term"] == "yearly" and s["pending_price_amount"] == "10000.00"
    assert s["current_period_end"] == org["subscription"]["current_period_end"]  # period untouched
    assert r.json()["change"]["amount_due_now"] == "0.00"


async def test_stale_version_and_empty_change_are_rejected(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="t-0000003")
    sub = await get_sub(db, org["id"])
    base = {"billing_term": "yearly", "reason": "annual deal"}
    r = await client.put(f"{P}/organizations/{org['id']}/subscription", json={**base, "version": sub.version + 5},
                         headers={**ops, **idem("term-00003")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_version"
    r = await client.put(f"{P}/organizations/{org['id']}/subscription", json={"reason": "nothing", "version": sub.version},
                         headers={**ops, **idem("term-00004")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_changes"
    r = await client.put(f"{P}/organizations/{org['id']}/subscription", json={**base, "version": sub.version})
    assert r.status_code == 401


async def test_retried_term_change_is_applied_once(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="t-0000004")
    version = (await get_sub(db, org["id"])).version
    payload = {"billing_term": "yearly", "reason": "annual deal", "version": version}
    first = await client.put(f"{P}/organizations/{org['id']}/subscription", json=payload, headers={**ops, **idem("term-00005")})
    retry = await client.put(f"{P}/organizations/{org['id']}/subscription", json=payload, headers={**ops, **idem("term-00005")})
    assert first.status_code == retry.status_code == 200 and retry.headers.get("idempotent-replay") == "true"
    assert (await get_sub(db, org["id"])).version == version + 1  # bumped once, not twice


async def test_plan_change_applies_immediately_and_changes_modules(client, db, ops, pro, basic):
    org = await create_org(client, ops, pro, start="active", key="p-0000001")
    tenant = await tenant_login(client, org_body(pro))
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200
    sub = await get_sub(db, org["id"])
    r = await client.put(f"{P}/organizations/{org['id']}/subscription",
                         json={"plan_id": str(basic.id), "reason": "downgrade", "version": sub.version}, headers={**ops, **idem("plan-0001")})
    assert r.status_code == 200 and r.json()["subscription"]["price_amount"] == "500.00"
    assert (await client.get(ANALYTICS, headers=tenant)).json()["error"]["code"] == "module_disabled"
    r = await client.put(f"{P}/organizations/{org['id']}/subscription",
                         json={"plan_id": str(pro.id), "effective": "next_renewal", "reason": "schedule it", "version": sub.version + 1},
                         headers={**ops, **idem("plan-0002")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "plan_change_immediate"


async def test_custom_price_limits_and_auto_renew(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="p-0000002")
    sub = await get_sub(db, org["id"])
    r = await client.put(f"{P}/organizations/{org['id']}/subscription",
                         json={"price_amount": "750.00", "auto_renew": False, "max_branches_override": 3, "max_staff_override": 40,
                               "reason": "negotiated deal", "version": sub.version}, headers={**ops, **idem("cust-0001")})
    assert r.status_code == 200, r.text
    s = r.json()["subscription"]
    assert s["price_amount"] == "750.00" and s["auto_renew"] is False
    assert (s["max_branches_override"], s["max_staff_override"]) == (3, 40)
    r = await client.put(f"{P}/organizations/{org['id']}/subscription",
                         json={"clear_limit_overrides": True, "reason": "reset", "version": s["version"]}, headers={**ops, **idem("cust-0002")})
    assert r.json()["subscription"]["max_branches_override"] is None


async def test_extend_revives_an_expired_subscription(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="e-0000001")
    await shift_period(db, org["id"], started_days_ago=60, ends_in_days=-30)  # long past the 7-day grace
    tenant = await tenant_login(client, org_body(pro))
    assert (await client.post(f"{API}/floor/branches", json={"name": "X", "code": "XX1"}, headers=tenant)).status_code == 403

    sub = await get_sub(db, org["id"])
    assert (await client.post(f"{P}/organizations/{org['id']}/subscription/extend", json={"days": 45, "reason": "goodwill"},
                              headers={**ops, **idem("ext-0001")})).status_code == 422  # version is mandatory
    r = await client.post(f"{P}/organizations/{org['id']}/subscription/extend",
                          json={"days": 45, "reason": "goodwill after outage", "version": sub.version}, headers={**ops, **idem("ext-0002")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    assert (datetime.fromisoformat(r.json()["current_period_end"]) - now()).days in (44, 45)
    assert (await client.post(f"{API}/floor/branches", json={"name": "X", "code": "XX1"}, headers=tenant)).status_code == 201


# --- modules -----------------------------------------------------------------------------------------------------------------


async def test_override_switches_a_module_off_and_on_instantly(client, db, ops, pro, events):
    org = await create_org(client, ops, pro, start="active", key="m-0000001")
    tenant = await tenant_login(client, org_body(pro))
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200  # cache warm

    r = await client.put(f"{P}/organizations/{org['id']}/modules/analytics",
                         json={"enabled": False, "reason": "contract dispute"}, headers=ops)
    assert r.status_code == 200, r.text
    state = {m["key"]: m for m in r.json()["modules"]}["analytics"]
    assert state["enabled"] is False and state["source"] == "off" and state["override"]["reason"] == "contract dispute"
    assert (await client.get(ANALYTICS, headers=tenant)).json()["error"]["code"] == "module_disabled"
    assert any(e["type"] == "entitlements.changed" for e in events)

    r = await client.delete(f"{P}/organizations/{org['id']}/modules/analytics", params={"reason": "resolved"}, headers=ops)
    assert r.status_code == 200
    assert {m["key"]: m for m in r.json()["modules"]}["analytics"]["source"] == "plan"
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200
    assert (await client.delete(f"{P}/organizations/{org['id']}/modules/analytics", params={"reason": "again"}, headers=ops)).status_code == 404


async def test_override_grants_a_module_until_expiry(client, db, ops, basic):
    org = await create_org(client, ops, basic, start="active", key="m-0000002")
    tenant = await tenant_login(client, org_body(basic))
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 403
    expires = (now() + timedelta(days=7)).isoformat()
    r = await client.put(f"{P}/organizations/{org['id']}/modules/analytics",
                         json={"enabled": True, "reason": "14-day analytics trial", "expires_at": expires}, headers=ops)
    assert r.status_code == 200 and {m["key"]: m for m in r.json()["modules"]}["analytics"]["source"] == "override"
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200


async def test_override_validation(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="m-0000003")
    url = f"{P}/organizations/{org['id']}/modules"
    r = await client.put(f"{url}/pos", json={"enabled": False, "reason": "no way"}, headers=ops)
    assert r.status_code == 422 and r.json()["error"]["code"] == "core_module"
    assert (await client.put(f"{url}/bogus", json={"enabled": True, "reason": "x y z"}, headers=ops)).status_code == 404
    past = (now() - timedelta(days=1)).isoformat()
    r = await client.put(f"{url}/analytics", json={"enabled": True, "reason": "late", "expires_at": past}, headers=ops)
    assert r.status_code == 422 and r.json()["error"]["code"] == "expiry_in_past"
    assert (await client.put(f"{url}/analytics", json={"enabled": True}, headers=ops)).status_code == 422  # reason mandatory
    support, _ = await operator(client, db, role="support")
    assert (await client.put(f"{url}/analytics", json={"enabled": False, "reason": "nope"}, headers=support)).status_code == 403
    assert (await db.scalars(select(OrganizationModule))).all() == []


async def test_global_kill_switch(client, db, ops, pro):
    a = await create_org(client, ops, pro, slug="alpha-cafe", start="active", key="g-a-00001")
    await create_org(client, ops, pro, slug="bravo-bar", start="active", key="g-b-00001")
    ta = await tenant_login(client, org_body(pro, slug="alpha-cafe"))
    tb = await tenant_login(client, org_body(pro, slug="bravo-bar"))
    assert (await client.get(ANALYTICS, headers=ta)).status_code == 200

    modules = {m["key"]: m for m in (await client.get(f"{P}/modules", headers=ops)).json()}
    assert modules["analytics"]["organizations_with_access"] == 2 and modules["pos"]["is_core"] is True

    billing, _ = await operator(client, db, role="billing")
    body = {"enabled": False, "reason": "security incident in reporting"}
    assert (await client.patch(f"{P}/modules/analytics", json=body, headers=billing)).status_code == 403
    assert (await client.patch(f"{P}/modules/pos", json=body, headers=ops)).status_code == 422

    assert (await client.patch(f"{P}/modules/analytics", json=body, headers=ops)).status_code == 204
    for tenant in (ta, tb):
        assert (await client.get(ANALYTICS, headers=tenant)).json()["error"]["code"] == "module_disabled"
    detail = (await client.get(f"{P}/organizations/{a['id']}", headers=ops)).json()
    assert {m["key"]: m for m in detail["modules"]}["analytics"]["source"] == "platform_disabled"
    # An override cannot defeat the kill switch.
    await client.put(f"{P}/organizations/{a['id']}/modules/analytics", json={"enabled": True, "reason": "try"}, headers=ops)
    assert (await client.get(ANALYTICS, headers=ta)).status_code == 403

    modules = {m["key"]: m for m in (await client.get(f"{P}/modules", headers=ops)).json()}
    assert modules["analytics"]["globally_enabled"] is False and modules["analytics"]["organizations_with_access"] == 0
    await client.patch(f"{P}/modules/analytics", json={"enabled": True, "reason": "fixed"}, headers=ops)
    assert (await client.get(ANALYTICS, headers=tb)).status_code == 200


async def test_bulk_dry_run_then_apply(client, db, ops, pro, basic):
    a = await create_org(client, ops, pro, slug="alpha-cafe", start="active", key="b-a-00001")
    b = await create_org(client, ops, basic, slug="bravo-bar", start="active", key="b-b-00001")
    body = {"module_key": "analytics", "enabled": True, "reason": "promo for basic plan", "plan_id": str(basic.id)}
    r = await client.post(f"{P}/modules/bulk", json=body, headers=ops)
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert [c["organization_id"] for c in r.json()["changed"]] == [b["id"]] and r.json()["unchanged"] == 0
    assert (await db.scalars(select(OrganizationModule))).all() == []  # dry run wrote nothing

    unstepped, _ = await operator(client, db, role="billing", stepped=False)
    r = await client.post(f"{P}/modules/bulk", json={**body, "dry_run": False}, headers=unstepped)
    assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"

    r = await client.post(f"{P}/modules/bulk", json={**body, "dry_run": False}, headers=ops)
    assert r.status_code == 200 and len(r.json()["changed"]) == 1
    tenant_b = await tenant_login(client, org_body(basic, slug="bravo-bar"))
    assert (await client.get(ANALYTICS, headers=tenant_b)).status_code == 200
    # Targeting both orgs by id reports the already-enabled one as unchanged.
    r = await client.post(f"{P}/modules/bulk", json={"module_key": "analytics", "enabled": True, "reason": "again",
                                                      "organization_ids": [a["id"], b["id"]]}, headers=ops)
    assert r.json()["changed"] == [] and r.json()["unchanged"] == 2
    r = await client.post(f"{P}/modules/bulk", json={"module_key": "analytics", "enabled": True, "reason": "bad target"}, headers=ops)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_target"


# --- plans ---------------------------------------------------------------------------------------------------------------------


async def test_plan_crud_and_live_effect_on_subscribers(client, db, ops):
    r = await client.post(f"{P}/plans", json={"code": "starter", "name": "Starter", "price_monthly": "300.00",
                                               "price_yearly": "3000.00", "max_branches": 1, "module_keys": ["pos"]}, headers=ops)
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["module_keys"] == ["pos"] and plan["subscribers"] == 0
    assert (await client.post(f"{P}/plans", json={"code": "starter", "name": "Dup", "module_keys": ["pos"]}, headers=ops)).status_code == 409
    assert {p["code"] for p in (await client.get(f"{P}/plans", headers=ops)).json()} == {"starter"}

    org = await create_org(client, ops, SimpleNamespace(id=plan["id"]), start="active", key="pl-000001")
    tenant = await tenant_login(client, org_body(SimpleNamespace(id=plan["id"])))
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 403
    r = await client.patch(f"{P}/plans/{plan['id']}", json={"module_keys": ["pos", "analytics"], "price_monthly": "350.00",
                                                           "reason": "adding analytics to starter"}, headers=ops)
    assert r.status_code == 200 and r.json()["module_keys"] == ["analytics", "pos"] and r.json()["subscribers"] == 1
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200  # live, no re-login
    r = await client.patch(f"{P}/plans/{plan['id']}", json={"is_active": False, "reason": "retire plan"}, headers=ops)
    assert r.json()["is_active"] is False
    support, _ = await operator(client, db, role="support")
    assert (await client.post(f"{P}/plans", json={"code": "evil", "name": "Evil", "module_keys": ["pos"]}, headers=support)).status_code == 403
    assert org["id"]


# --- impersonation -------------------------------------------------------------------------------------------------------------


async def test_impersonation_is_read_only_audited_and_time_boxed(client, db, ops, pro):
    org = await create_org(client, ops, pro, start="active", key="i-0000001")
    support, admin = await operator(client, db, role="support")
    assert (await client.post(f"{P}/organizations/{org['id']}/impersonate", json={}, headers=support)).status_code == 422  # reason
    r = await client.post(f"{P}/organizations/{org['id']}/impersonate", json={"reason": "ticket #881: bill totals look wrong", "minutes": 10},
                          headers=support)
    assert r.status_code == 200, r.text
    token = r.json()
    assert token["read_only"] is True and token["expires_in"] == 600
    view = {"Authorization": f"Bearer {token['access_token']}"}

    assert (await client.get(f"{API}/staff", headers=view)).status_code == 200
    assert (await client.get(ANALYTICS, headers=view)).status_code == 200
    ent = (await client.get(f"{API}/me/entitlements", headers=view)).json()
    assert ent["impersonation"] is True
    r = await client.post(f"{API}/floor/branches", json={"name": "Hack", "code": "HK1"}, headers=view)
    assert r.status_code == 403 and r.json()["error"]["code"] == "impersonation_read_only"
    assert (await client.patch(f"{API}/catalog/categories/{org['id']}", json={"name": "x"}, headers=view)).status_code == 403

    entries = (await db.scalars(select(PlatformAuditLog).where(PlatformAuditLog.action == "org.impersonate"))).all()
    assert len(entries) == 1 and entries[0].admin_id == admin.id and "ticket #881" in entries[0].reason

    assert (await client.post(f"{P}/organizations/{org['id']}/impersonate", json={"reason": "too long", "minutes": 60}, headers=support)).status_code == 422
    unstepped, _ = await operator(client, db, role="support", stepped=False, email="support2@example.com")
    r = await client.post(f"{P}/organizations/{org['id']}/impersonate", json={"reason": "no step up"}, headers=unstepped)
    assert r.json()["error"]["code"] == "step_up_required"
    billing, _ = await operator(client, db, role="billing")
    assert (await client.post(f"{P}/organizations/{org['id']}/impersonate", json={"reason": "billing may not"}, headers=billing)).status_code == 403


async def test_impersonation_cannot_cross_orgs_and_dies_on_suspend(client, db, ops, pro):
    a = await create_org(client, ops, pro, slug="alpha-cafe", start="active", key="i-a-00001")
    b = await create_org(client, ops, pro, slug="bravo-bar", start="active", key="i-b-00001")
    stranger = (await db.scalars(select(StaffUser).where(StaffUser.organization_id == b["id"]))).first()
    r = await client.post(f"{P}/organizations/{a['id']}/impersonate", json={"reason": "wrong org", "staff_id": str(stranger.id)}, headers=ops)
    assert r.status_code == 404
    token = (await client.post(f"{P}/organizations/{a['id']}/impersonate", json={"reason": "look around"}, headers=ops)).json()
    view = {"Authorization": f"Bearer {token['access_token']}"}
    assert (await client.get(f"{API}/staff", headers=view)).status_code == 200
    await client.post(f"{P}/organizations/{a['id']}/suspend", json={"reason": "stop everything"}, headers=ops)
    assert (await client.get(f"{API}/staff", headers=view)).status_code == 401


# --- dashboard / realtime -----------------------------------------------------------------------------------------------------------


async def test_dashboard_counts_and_recurring_revenue(client, db, ops, pro):
    await create_org(client, ops, pro, slug="alpha-cafe", start="active", key="d-a-00001")  # 1000 / month
    y = await create_org(client, ops, pro, slug="bravo-bar", term="yearly", start="active", key="d-b-00001")  # 10000 / year
    await create_org(client, ops, pro, slug="charlie-pub", key="d-c-00001")  # trial: not revenue yet
    await shift_period(db, y["id"], started_days_ago=350, ends_in_days=10)
    d = (await client.get(f"{P}/dashboard", headers=ops)).json()
    assert d["organizations_by_status"] == {"active": 2, "trial": 1}
    assert d["subscriptions_by_term"] == {"monthly": 2, "yearly": 1}
    assert d["subscriptions_by_status"] == {"active": 2, "trialing": 1}
    assert d["mrr"] == {"INR": str((D("1000") + D("10000") / 12).quantize(D("0.01")))}
    assert d["arr"]["INR"] == str(((D("1000") + D("10000") / 12) * 12).quantize(D("0.01")))
    assert [r["slug"] for r in d["renewals_next_30_days"]][0] == "bravo-bar"
    assert d["trials_ending_next_7_days"] == [] and d["past_due"] == 0


class FakeSocket:
    def __init__(self):
        self.closed = None

    async def close(self, code=1000):
        self.closed = code

    async def send_text(self, text):
        pass


async def test_access_revoked_event_closes_every_socket_of_the_branch():
    mgr = RealtimeConnectionManager()
    branch, other = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    mine = [FakeSocket(), FakeSocket()]
    theirs = FakeSocket()
    mgr.register(f"branch:{branch}:orders", mine[0])
    mgr.register(f"branch:{branch}:entitlements", mine[1])
    mgr.register(f"branch:{other}:orders", theirs)
    await mgr.fan_out_local(f"branch:{branch}:entitlements", '{"type": "access.revoked"}')
    assert [s.closed for s in mine] == [4403, 4403] and theirs.closed is None
    assert mgr.stats() == {"channels": 1, "sockets": 1}
    await mgr.fan_out_local(f"branch:{other}:orders", '{"type": "order.updated"}')
    assert theirs.closed is None


async def test_tenant_tokens_cannot_use_the_platform_api(client, db, ops, pro, owner):
    await create_org(client, ops, pro, key="x-0000001")
    for path in ("/dashboard", "/organizations", "/plans", "/modules"):
        assert (await client.get(f"{P}{path}", headers=auth_headers(owner))).status_code == 401


# --- business categories --------------------------------------------------------------------------------------------


async def test_category_on_create_override_and_filter(client, db, redis_client, ops, pro):
    cats = (await client.get(f"{P}/business-categories", headers=ops)).json()
    assert [c["key"] for c in cats["categories"]] == ["restaurant", "cafe", "grocery", "retail"]
    assert cats["setting_labels"]["tokens"] == "Token numbers"

    org = await create_org(client, ops, pro, category="grocery", settings={"tokens": True})
    assert org["category"] == "grocery" and org["settings"]["barcode"] is True and org["settings"]["tokens"] is True
    assert org["settings_overrides"] == {"tokens": True}

    tenant = await tenant_login(client, org_body(pro))
    ent = (await client.get(f"{API}/me/entitlements", headers=tenant)).json()
    assert ent["category"] == "grocery" and ent["settings"]["quick_sale"] is True and ent["settings"]["tokens"] is True

    # Changing the type is live for the tenant at once (cached entitlements are dropped) and audited.
    r = await client.patch(f"{P}/organizations/{org['id']}", json={"category": "cafe", "settings": {}, "reason": "Opened a cafe"},
                           headers=ops)
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "cafe" and r.json()["settings_overrides"] == {}
    ent = (await client.get(f"{API}/me/entitlements", headers=tenant)).json()
    assert ent["category"] == "cafe" and ent["settings"]["kitchen_tickets"] is True and ent["settings"]["barcode"] is False
    events = (await db.scalars(select(SubscriptionEvent.event_type))).all()
    assert "business_settings_changed" in events

    listed = (await client.get(f"{P}/organizations", params={"category": "cafe"}, headers=ops)).json()
    assert [o["id"] for o in listed["items"]] == [org["id"]] and listed["items"][0]["category"] == "cafe"
    assert (await client.get(f"{P}/organizations", params={"category": "grocery"}, headers=ops)).json()["total"] == 0

    bad = await client.patch(f"{P}/organizations/{org['id']}", json={"settings": {"nonsense": True}, "reason": "typo"}, headers=ops)
    assert bad.status_code == 422
    bad = await client.patch(f"{P}/organizations/{org['id']}", json={"category": "pharmacy", "reason": "nope"}, headers=ops)
    assert bad.status_code == 422
