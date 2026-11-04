"""Module gating, subscription states, plan limits and the entitlement cache."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.entitlements import compute_entitlements, invalidate_entitlements
from app.core.modules import CORE_MODULES, resolve_dependencies
from app.models import Organization, OrganizationModule, Plan, PlanModule, Subscription
from tests import factories
from tests.factories import auth_headers
from tests.flows import API

ANALYTICS = f"{API}/analytics/kpis/live"


def now() -> datetime:
    return datetime.now(timezone.utc)


async def subscription_of(db, org) -> Subscription:
    return await db.scalar(select(Subscription).where(Subscription.organization_id == org.id))


async def use_plan(db, org, modules, code="basic", **limits) -> Plan:
    plan = Plan(code=code, name=code.title(), **limits)
    db.add(plan)
    await db.flush()
    db.add_all(PlanModule(plan_id=plan.id, module_key=m) for m in modules)
    sub = await subscription_of(db, org)
    sub.plan_id = plan.id
    await db.commit()
    return plan


async def update_sub(db, org, redis_client, **fields) -> None:
    sub = await subscription_of(db, org)
    for k, v in fields.items():
        setattr(sub, k, v)
    await db.commit()
    await invalidate_entitlements(redis_client, org.id)


@pytest.fixture
async def org(db):
    return await factories.make_org(db)


# --- module gating ----------------------------------------------------------------


async def test_entitlements_endpoint_for_legacy_plan(client, owner):
    r = await client.get(f"{API}/me/entitlements", headers=auth_headers(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access"] == "full" and body["plan_code"] == "legacy_all_modules"
    assert body["billing_term"] == "yearly" and body["subscription_status"] == "active"
    assert set(body["modules"]) == {"pos", "analytics", "kds"}


async def test_disabled_module_is_refused_and_enabled_by_override(client, db, org, owner, branch, redis_client):
    await use_plan(db, org, ["pos"])
    headers = auth_headers(owner)
    r = await client.get(ANALYTICS, headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    assert r.json()["error"]["details"]["module"] == "analytics"
    # Core POS keeps working.
    assert (await client.get(f"{API}/orders", headers=headers)).status_code == 200

    db.add(OrganizationModule(organization_id=org.id, module_key="analytics", enabled=True, reason="trial of analytics"))
    await db.commit()
    # Still cached: the change is invisible until the cache is dropped...
    assert (await client.get(ANALYTICS, headers=headers)).status_code == 403
    await invalidate_entitlements(redis_client, org.id)
    # ...then it takes effect on the very next request.
    assert (await client.get(ANALYTICS, headers=headers)).status_code == 200


async def test_override_can_disable_a_plan_module(client, db, org, owner, branch, redis_client):
    db.add(OrganizationModule(organization_id=org.id, module_key="analytics", enabled=False))
    await db.commit()
    r = await client.get(ANALYTICS, headers=auth_headers(owner))
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"


async def test_expired_override_falls_back_to_plan(db, org):
    await use_plan(db, org, ["pos"])
    db.add(OrganizationModule(organization_id=org.id, module_key="analytics", enabled=True,
                              expires_at=now() - timedelta(minutes=1)))
    await db.commit()
    assert "analytics" not in (await compute_entitlements(db, org.id)).modules


async def test_override_expiry_bounds_cache_validity(db, org):
    await use_plan(db, org, ["pos"])
    expires = now() + timedelta(hours=1)
    db.add(OrganizationModule(organization_id=org.id, module_key="analytics", enabled=True, expires_at=expires))
    await db.commit()
    ent = await compute_entitlements(db, org.id)
    assert "analytics" in ent.modules and ent.valid_until == expires


async def test_core_module_cannot_be_disabled(db, org):
    db.add(OrganizationModule(organization_id=org.id, module_key="pos", enabled=False))
    await db.commit()
    assert CORE_MODULES <= (await compute_entitlements(db, org.id)).modules


def test_dependencies_are_pruned():
    assert resolve_dependencies(frozenset({"analytics"})) == frozenset()
    assert resolve_dependencies(frozenset({"pos", "analytics"})) == frozenset({"pos", "analytics"})
    assert resolve_dependencies(frozenset({"pos", "bogus"})) == frozenset({"pos"})


async def test_each_org_has_its_own_modules(client, db, org, owner, branch):
    org_b = await factories.make_org(db, slug="globex")
    branch_b = await factories.make_branch(db, code="DEL1", org=org_b)
    owner_b = await factories.make_staff(db, branch_b, role="owner", pin="8000", username="owner_b")
    await use_plan(db, org_b, ["pos"])
    assert (await client.get(ANALYTICS, headers=auth_headers(owner))).status_code == 200
    assert (await client.get(ANALYTICS, headers=auth_headers(owner_b))).status_code == 403


# --- subscription state -----------------------------------------------------------


async def test_lapsed_period_allows_grace_then_goes_read_only(client, db, org, owner, branch, redis_client):
    headers = auth_headers(owner)
    body = {"name": "Floor 2"}
    await update_sub(db, org, redis_client, current_period_end=now() - timedelta(days=2))  # inside the 7-day grace
    r = await client.get(f"{API}/me/entitlements", headers=headers)
    assert r.json()["subscription_status"] == "past_due" and r.json()["access"] == "full"
    assert (await client.post(f"{API}/floor/branches/{branch.id}/floors", json=body, headers=headers)).status_code == 201

    await update_sub(db, org, redis_client, current_period_end=now() - timedelta(days=30))  # grace over
    r = await client.get(f"{API}/me/entitlements", headers=headers)
    assert r.json()["subscription_status"] == "expired" and r.json()["access"] == "read_only"
    assert (await client.get(f"{API}/orders", headers=headers)).status_code == 200  # reads still work
    r = await client.post(f"{API}/floor/branches/{branch.id}/floors", json=body, headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "subscription_inactive"


async def test_explicit_grace_overrides_default(db, org, redis_client):
    await update_sub(db, org, redis_client, current_period_end=now() - timedelta(days=30),
                     grace_until=now() + timedelta(days=1))
    assert (await compute_entitlements(db, org.id)).access == "full"


async def test_trial_ends(db, org, redis_client):
    await update_sub(db, org, redis_client, status="trialing", trial_ends_at=now() + timedelta(days=3))
    ent = await compute_entitlements(db, org.id)
    assert ent.access == "full" and ent.subscription_status == "trialing" and ent.valid_until == ent.trial_ends_at
    await update_sub(db, org, redis_client, trial_ends_at=now() - timedelta(minutes=1))
    ent = await compute_entitlements(db, org.id)
    assert ent.access == "read_only" and ent.subscription_status == "expired"


@pytest.mark.parametrize("status", ["paused", "cancelled", "expired"])
async def test_inactive_statuses_are_read_only(db, org, redis_client, status):
    await update_sub(db, org, redis_client, status=status)
    assert (await compute_entitlements(db, org.id)).access == "read_only"


async def test_missing_subscription_fails_closed(client, db, owner, org):
    sub = await subscription_of(db, org)
    await db.delete(sub)
    await db.commit()
    ent = await compute_entitlements(db, org.id)
    assert ent.access == "read_only" and ent.modules == CORE_MODULES
    r = await client.post(f"{API}/floor/branches", json={"name": "X", "code": "XX1"}, headers=auth_headers(owner))
    assert r.status_code == 403 and r.json()["error"]["code"] == "subscription_inactive"


async def test_suspended_org_is_blocked_entirely(client, db, org, owner, branch, redis_client):
    headers = auth_headers(owner)
    assert (await client.get(f"{API}/orders", headers=headers)).status_code == 200  # warms the cache
    org_row = await db.get(Organization, org.id)
    org_row.status = "suspended"
    await db.commit()
    await invalidate_entitlements(redis_client, org.id)
    for method, path in (("GET", f"{API}/orders"), ("GET", f"{API}/me/entitlements"), ("GET", ANALYTICS)):
        r = await client.request(method, path, headers=headers)
        assert r.status_code == 403 and r.json()["error"]["code"] == "organization_suspended", (path, r.text)


async def test_login_still_works_when_read_only(client, db, org, redis_client):
    """Sign-in must work so the app can show 'subscription expired' instead of a blank failure."""
    await factories.make_staff(db, await factories.make_branch(db), role="manager", username="mgr")
    await update_sub(db, org, redis_client, status="expired")
    r = await client.post(f"{API}/staff/auth/login", json={"username": "mgr", "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text


# --- plan limits ------------------------------------------------------------------


async def test_branch_limit_from_plan_and_override(client, db, org, owner, branch, redis_client):
    await use_plan(db, org, ["pos"], max_branches=1)
    headers = auth_headers(owner)
    body = {"name": "Koramangala", "code": "BLR2"}
    r = await client.post(f"{API}/floor/branches", json=body, headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "plan_limit_reached"

    await update_sub(db, org, redis_client, max_branches_override=2)
    assert (await client.post(f"{API}/floor/branches", json=body, headers=headers)).status_code == 201


async def test_staff_limit(client, db, org, owner, branch):
    await use_plan(db, org, ["pos"], max_staff=1)  # the owner already fills the only seat
    payload = {"username": "waiter01", "full_name": "Wait Er", "role": "waiter", "pin": "4321", "branch_id": str(branch.id)}
    r = await client.post(f"{API}/staff", json=payload, headers=auth_headers(owner))
    assert r.status_code == 403 and r.json()["error"]["code"] == "plan_limit_reached"


# --- cache --------------------------------------------------------------------------


async def test_entitlements_are_cached_and_invalidated(client, db, org, owner, redis_client):
    headers = auth_headers(owner)
    await client.get(f"{API}/me/entitlements", headers=headers)
    assert await redis_client.exists(f"entitlements:{org.id}") == 1
    await invalidate_entitlements(redis_client, org.id)
    assert await redis_client.exists(f"entitlements:{org.id}") == 0


async def test_cache_never_outlives_a_subscription_boundary(client, db, org, owner, redis_client):
    await update_sub(db, org, redis_client, current_period_end=now() + timedelta(seconds=30))
    await client.get(f"{API}/me/entitlements", headers=auth_headers(owner))
    assert 0 < await redis_client.ttl(f"entitlements:{org.id}") <= 30
