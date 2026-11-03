"""Shared builders for platform API tests: operators, sign-in, step-up, plans and subscribers."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update

from app.core.enums import PlatformRole
from app.core.platform_security import totp_code
from app.models import PlatformAdmin, Plan, PlanModule, Subscription
from app.modules.platform.admin_service import create_admin_record
from tests.flows import API

PWD = "Correct-Horse-Battery-9"
P = f"{API}/platform"


def now() -> datetime:
    return datetime.now(timezone.utc)


async def make_admin(db, role="super_admin", email=None, password=PWD):
    email = email or f"{role}@example.com"
    admin, secret, _, _ = await create_admin_record(
        db, email=email, full_name=f"{role} person", role=PlatformRole(role), password=password)
    await db.commit()
    return admin, secret


async def sign_in(client, db, admin, secret, password=PWD):
    await db.execute(update(PlatformAdmin).where(PlatformAdmin.id == admin.id).values(totp_last_step=0))
    await db.commit()
    r = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": password})
    assert r.status_code == 200, r.text
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": r.json()["mfa_token"], "code": totp_code(secret)})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def step_up(client, db, admin, secret, headers, password=PWD):
    await db.execute(update(PlatformAdmin).where(PlatformAdmin.id == admin.id).values(totp_last_step=0))
    await db.commit()
    r = await client.post(f"{P}/auth/step-up", json={"password": password, "code": totp_code(secret)}, headers=headers)
    assert r.status_code == 204, r.text


async def operator(client, db, role="super_admin", stepped=True, email=None):
    """Sign in an operator of `role`; returns (headers, admin)."""
    admin, secret = await make_admin(db, role, email=email)
    headers = await sign_in(client, db, admin, secret)
    if stepped:
        await step_up(client, db, admin, secret, headers)
    return headers, admin


async def make_plan(db, code="pro", modules=("pos", "analytics"), monthly="1000.00", yearly="10000.00", **limits) -> Plan:
    plan = Plan(code=code, name=code.title(), price_monthly=Decimal(monthly) if monthly else None,
                price_yearly=Decimal(yearly) if yearly else None, **limits)
    db.add(plan)
    await db.flush()
    db.add_all(PlanModule(plan_id=plan.id, module_key=m) for m in modules)
    await db.commit()
    return plan


def org_body(plan, slug="acme-diner", term="monthly", start="trial", **extra):
    return {
        "name": "Acme Diner", "slug": slug, "plan_id": str(plan.id), "billing_term": term, "start": start,
        "billing_email": "Billing@Acme.example", "country": "IN",
        "owner": {"username": f"owner_{slug}".replace("-", "_")[:40], "full_name": "Olivia Owner",
                  "password": "owner-password-1", "pin": "4321", "branch_code": slug.replace("-", "").upper()[:6],
                  "branch_name": "Main"},
        **extra,
    }


def idem(key):
    return {"X-Idempotency-Key": key}


async def create_org(client, headers, plan, key="create-key-1", **kw):
    r = await client.post(f"{P}/organizations", json=org_body(plan, **kw), headers={**headers, **idem(key)})
    assert r.status_code == 201, r.text
    return r.json()["organization"]


async def tenant_login(client, org_body_, password="owner-password-1"):
    r = await client.post(f"{API}/staff/auth/login", json={"username": org_body_["owner"]["username"], "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def get_sub(db, org_id) -> Subscription:
    stmt = select(Subscription).where(Subscription.organization_id == org_id).execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def shift_period(db, org_id, *, started_days_ago, ends_in_days):
    """Place the current paid period relative to now so proration is deterministic."""
    t = now()
    await db.execute(update(Subscription).where(Subscription.organization_id == org_id).values(
        current_period_start=t - timedelta(days=started_days_ago), current_period_end=t + timedelta(days=ends_in_days)))
    await db.commit()
