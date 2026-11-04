"""Phase 7: security alerts, four-eyes approvals, audit verification and break-glass recovery."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, text, update

from app.cli import reset_platform_admin_2fa, verify_audit
from app.core.config import settings
from app.core.enums import PlatformRole
from app.core.platform_security import totp_code
from app.models import PlatformAlert, PlatformApproval, PlatformAuditLog
from app.modules.platform import alerts as alerts_module
from tests.flows import API
from tests.conftest import superuser_sql
from tests.platform_helpers import P, create_org, make_admin, make_plan, operator, org_body, sign_in, tenant_login

ANALYTICS = f"{API}/analytics/kpis/live"
BODY = {"enabled": False, "reason": "security incident in reporting"}


async def alerts_of(db, kind=None):
    db.expire_all()
    stmt = select(PlatformAlert).order_by(PlatformAlert.created_at)
    if kind:
        stmt = stmt.where(PlatformAlert.kind == kind)
    return list((await db.scalars(stmt)).all())


@pytest.fixture(autouse=True)
def four_eyes_on(monkeypatch):
    """Four-eyes approval is off by default; these tests are about how it behaves when an operator team turns it on."""
    monkeypatch.setattr(settings, "PLATFORM_FOUR_EYES", True)


@pytest.fixture
async def pro(db):
    return await make_plan(db, "pro")


@pytest.fixture
async def two_supers(client, db):
    """Two super admins, both signed in and re-authenticated. Returns (headersA, adminA, headersB, adminB)."""
    a_headers, a = await operator(client, db, email="alice@example.com")
    b_headers, b = await operator(client, db, email="bob@example.com")
    return a_headers, a, b_headers, b


# --- alerts ---------------------------------------------------------------------------------------------------------------


async def test_failed_sign_ins_raise_a_lockout_alert_once(client, db):
    admin, _ = await make_admin(db, email="alice@example.com")
    for _ in range(6):
        await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "not-the-password-1A!"})
    found = await alerts_of(db, "auth.lockout")
    assert len(found) == 1 and found[0].severity == "warning" and found[0].data["email"] == "alice@example.com"


async def test_second_factor_lockout_is_critical(client, db):
    admin, _ = await make_admin(db, email="alice@example.com")
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "Correct-Horse-Battery-9"})).json()["mfa_token"]
    for _ in range(5):
        await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": "000000"})
    [alert] = await alerts_of(db, "auth.lockout_2fa")
    assert alert.severity == "critical" and "password but not the code" in alert.message


async def test_replayed_refresh_token_is_a_critical_alert(client, db):
    admin, secret = await make_admin(db, email="alice@example.com")
    headers = await sign_in(client, db, admin, secret)
    token = headers["Authorization"].split()[1]
    csrf = client.cookies.get("pa_csrf")
    old = client.cookies.get("pa_refresh", path="/api/v1/platform/auth")
    assert (await client.post(f"{P}/auth/refresh", headers={"X-CSRF-Token": csrf})).status_code == 200
    client.cookies.set("pa_refresh", old, path="/api/v1/platform/auth")
    assert (await client.post(f"{P}/auth/refresh", headers={"X-CSRF-Token": client.cookies.get("pa_csrf")})).status_code == 401
    [alert] = await alerts_of(db, "auth.refresh_reuse")
    assert alert.severity == "critical" and token


async def test_operator_changes_and_support_access_are_alerted(client, db, pro):
    ops, _ = await operator(client, db, email="alice@example.com")
    r = await client.post(f"{P}/admins", headers=ops, json={"email": "sam@example.com", "full_name": "Sam Support", "role": "support",
                                                            "password": "Sup-Port-Pass-2025!"})
    assert r.status_code == 201
    sam = r.json()["admin"]["id"]
    assert (await client.patch(f"{P}/admins/{sam}", json={"role": "billing"}, headers=ops)).status_code == 200
    org = await create_org(client, ops, pro, start="active", key="sec-0001")
    await client.post(f"{P}/organizations/{org['id']}/impersonate", json={"reason": "ticket 55 investigation"}, headers=ops)
    kinds = [a.kind for a in await alerts_of(db)]
    assert {"admin.created", "admin.changed", "org.impersonation"} <= set(kinds)
    changed = (await alerts_of(db, "admin.changed"))[0]
    assert "support -> billing" in changed.message


async def test_alert_endpoints_and_roles(client, db):
    ops, _ = await operator(client, db, email="alice@example.com")
    admin, _ = await make_admin(db, role="support", email="sam@example.com")
    read_only, _ = await operator(client, db, role="read_only")  # sign everyone in before the lockout trips the IP limit
    billing, _ = await operator(client, db, role="billing")
    for _ in range(6):
        await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "wrong-password-1A!"})
    listed = (await client.get(f"{P}/alerts", params={"open_only": "true"}, headers=ops)).json()
    assert [a["kind"] for a in listed] == ["auth.lockout"] and listed[0]["acknowledged_at"] is None
    summary = (await client.get(f"{P}/alerts/summary", headers=ops)).json()
    assert summary == {"open_alerts": {"warning": 1}, "pending_approvals": 0}
    assert (await client.get(f"{P}/alerts", params={"severity": "critical"}, headers=ops)).json() == []
    assert (await client.get(f"{P}/alerts", params={"severity": "bogus"}, headers=ops)).status_code == 422

    assert (await client.get(f"{P}/alerts", headers=read_only)).status_code == 200
    assert (await client.post(f"{P}/alerts/{listed[0]['id']}/acknowledge", headers=read_only)).status_code == 403
    assert (await client.get(f"{P}/alerts", headers=billing)).status_code == 403

    r = await client.post(f"{P}/alerts/{listed[0]['id']}/acknowledge", headers=ops)
    assert r.status_code == 200 and r.json()["acknowledged_at"] and r.json()["acknowledged_by"]
    assert (await client.get(f"{P}/alerts", params={"open_only": "true"}, headers=ops)).json() == []


REAL_CLIENT = httpx.AsyncClient  # captured before any test patches it


class Receiver:
    """A stand-in webhook endpoint that records what it was sent."""

    def __init__(self, status=200):
        self.status, self.requests = status, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)

    def install(self, monkeypatch):
        monkeypatch.setattr(alerts_module.httpx, "AsyncClient",
                            lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(self.handler), **kw))


async def test_webhook_delivery_is_signed_and_retried(client, db, monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_ALERT_WEBHOOK_URL", "https://alerts.example.test/hook")
    monkeypatch.setattr(settings, "PLATFORM_ALERT_WEBHOOK_SECRET", "hook-secret")
    down = Receiver(status=500)
    down.install(monkeypatch)
    admin, _ = await make_admin(db, email="alice@example.com")
    for _ in range(6):
        await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "wrong-password-1A!"})
    await asyncio.sleep(0.5)  # the automatic delivery task runs after the commit
    [alert] = await alerts_of(db, "auth.lockout")
    assert down.requests and alert.notified_at is None  # endpoint was down: nothing is marked delivered

    up = Receiver()
    up.install(monkeypatch)
    assert await alerts_module.dispatch_pending_alerts() == {"sent": 1, "failed": 0}
    [request] = up.requests
    body = request.content
    assert request.headers["x-alert-signature"] == alerts_module.sign(body)
    payload = json.loads(body)
    assert payload["kind"] == "auth.lockout" and payload["severity"] == "warning" and payload["text"].startswith("[WARNING]")
    assert (await alerts_of(db, "auth.lockout"))[0].notified_at is not None
    assert await alerts_module.dispatch_pending_alerts() == {"sent": 0, "failed": 0}  # delivered once only


def test_production_requires_https_webhook():
    from app.core.config import Settings

    good = dict(ENVIRONMENT="production", SECRET_KEY="a" * 40, PIN_PEPPER="b" * 40, PLATFORM_SECRET_KEY="c" * 40,
                PLATFORM_ENCRYPTION_KEY="d" * 40, PLATFORM_COOKIE_SECURE=True, _env_file=None)
    assert Settings(**good, PLATFORM_ALERT_WEBHOOK_URL="https://hooks.example.com/x").PLATFORM_ALERT_WEBHOOK_URL
    with pytest.raises(ValueError, match="https"):
        Settings(**good, PLATFORM_ALERT_WEBHOOK_URL="http://hooks.example.com/x")


# --- four-eyes approvals -----------------------------------------------------------------------------------------------------


async def test_a_lone_super_admin_is_not_locked_out_but_it_is_flagged(client, db, pro):
    ops, _ = await operator(client, db, email="alice@example.com")
    org = await create_org(client, ops, pro, start="active", key="fe-key-0001")
    tenant = await tenant_login(client, org_body(pro))
    assert (await client.patch(f"{P}/modules/analytics", json=BODY, headers=ops)).status_code == 204  # ran at once
    assert (await client.get(ANALYTICS, headers=tenant)).json()["error"]["code"] == "module_disabled"
    [alert] = await alerts_of(db, "approval.unavailable")
    assert "only active super admin" in alert.message and org["id"]


async def test_global_kill_switch_needs_a_second_super_admin(client, db, pro, two_supers):
    a, alice, b, bob = two_supers
    await create_org(client, a, pro, start="active", key="fe-key-0002")
    tenant = await tenant_login(client, org_body(pro))
    r = await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)
    assert r.status_code == 202, r.text
    approval = r.json()
    assert approval["status"] == "pending" and approval["requested_by"] == alice.email and "analytics" in approval["summary"]
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200  # nothing happened yet

    # You cannot approve your own request, however much you re-authenticate.
    r = await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=a)
    assert r.status_code == 403 and r.json()["error"]["code"] == "four_eyes_self_approval"

    r = await client.post(f"{P}/approvals/{approval['id']}/approve", json={"note": "confirmed with the on-call lead"}, headers=b)
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["status"], done["decided_by"], done["result"]) == ("executed", bob.email, {"module": "analytics", "enabled": False})
    assert (await client.get(ANALYTICS, headers=tenant)).json()["error"]["code"] == "module_disabled"

    assert (await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=b)).status_code == 409  # one-shot
    actions = (await db.scalars(select(PlatformAuditLog.action).where(PlatformAuditLog.action.like("approval.%")))).all()
    assert {"approval.request", "approval.approve"} <= set(actions)
    assert {"approval.requested", "approval.executed"} <= {x.kind for x in await alerts_of(db)}
    assert "platform.module_switch" in (await db.scalars(select(PlatformAuditLog.action))).all()


async def test_approving_needs_step_up_and_super_admin(client, db, pro, two_supers):
    a, _, b, bob = two_supers
    approval = (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).json()
    fresh, _ = await operator(client, db, email="carol@example.com", stepped=False)  # a third super admin without step-up
    r = await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=fresh)
    assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"
    billing, _ = await operator(client, db, role="billing")
    assert (await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=billing)).status_code == 403
    assert bob


async def test_rejecting_and_withdrawing(client, db, pro, two_supers):
    a, _, b, bob = two_supers
    first = (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).json()
    assert (await client.post(f"{P}/approvals/{first['id']}/reject", json={}, headers=b)).status_code == 422  # a note is mandatory
    r = await client.post(f"{P}/approvals/{first['id']}/reject", json={"note": "not during the dinner rush"}, headers=b)
    assert (r.json()["status"], r.json()["decided_by"], r.json()["decision_note"]) == ("rejected", bob.email, "not during the dinner rush")
    assert (await client.post(f"{P}/approvals/{first['id']}/approve", json={}, headers=b)).status_code == 409

    second = (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).json()
    assert (await client.post(f"{P}/approvals/{second['id']}/cancel", headers=b)).json()["error"]["code"] == "not_requester"
    assert (await client.post(f"{P}/approvals/{second['id']}/reject", json={"note": "x y z"}, headers=a)).json()["error"]["code"] == "four_eyes_self_decision"
    assert (await client.post(f"{P}/approvals/{second['id']}/cancel", headers=a)).json()["status"] == "cancelled"
    modules = {m["key"]: m for m in (await client.get(f"{P}/modules", headers=a)).json()}
    assert modules["analytics"]["globally_enabled"] is True  # nothing ever ran
    pending = (await client.get(f"{P}/approvals", params={"status": "pending"}, headers=a)).json()
    assert pending == []


async def test_stale_requests_expire(client, db, pro, two_supers):
    a, _, b, _ = two_supers
    approval = (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).json()
    await db.execute(update(PlatformApproval).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    await db.commit()
    listed = (await client.get(f"{P}/approvals", headers=a)).json()
    assert listed[0]["status"] == "expired"  # shown as expired even before anyone touches it
    r = await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=b)
    assert r.status_code == 409 and r.json()["error"]["code"] == "approval_expired"
    assert (await client.get(f"{P}/approvals/summary", headers=a)).status_code in (404, 405, 422)


async def test_switching_a_module_back_on_is_not_gated(client, db, pro, two_supers):
    a, _, b, _ = two_supers
    approval = (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).json()
    await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=b)
    assert (await client.patch(f"{P}/modules/analytics", json={"enabled": True, "reason": "fixed"}, headers=a)).status_code == 204


async def test_bulk_apply_is_gated_but_the_dry_run_is_not(client, db, pro, two_supers):
    a, _, b, _ = two_supers
    basic = await make_plan(db, "basic", modules=("pos",), monthly="500.00", yearly="5000.00")
    org = await create_org(client, a, basic, start="active", key="fe-key-0003")
    tenant = await tenant_login(client, org_body(basic))
    body = {"module_key": "analytics", "enabled": True, "reason": "promo for the basic plan", "plan_id": str(basic.id)}
    dry = await client.post(f"{P}/modules/bulk", json=body, headers=a)
    assert dry.status_code == 200 and dry.json()["dry_run"] is True
    r = await client.post(f"{P}/modules/bulk", json={**body, "dry_run": False}, headers=a)
    assert r.status_code == 202 and r.json()["action"] == "module.bulk_apply"
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 403  # not applied yet
    done = (await client.post(f"{P}/approvals/{r.json()['id']}/approve", json={}, headers=b)).json()
    assert done["status"] == "executed" and done["result"] == {"changed": 1, "unchanged": 0, "blocked": 0}
    assert (await client.get(ANALYTICS, headers=tenant)).status_code == 200 and org["id"]


async def test_immediate_cancellation_is_gated_period_end_is_not(client, db, pro, two_supers):
    a, _, b, _ = two_supers
    org = await create_org(client, a, pro, start="active", key="fe-key-0004")
    r = await client.post(f"{P}/organizations/{org['id']}/cancel", json={"reason": "customer asked to stop"}, headers=a)
    assert r.status_code == 200 and r.json()["subscription"]["cancel_at_period_end"] is True  # no approval needed

    org2 = await create_org(client, a, pro, slug="second-cafe", start="active", key="fe-key-0005")
    r = await client.post(f"{P}/organizations/{org2['id']}/cancel", json={"reason": "fraud suspected", "at_period_end": False}, headers=a)
    assert r.status_code == 202
    detail = (await client.get(f"{P}/organizations/{org2['id']}", headers=a)).json()
    assert detail["subscription"]["status"] == "active"
    await client.post(f"{P}/approvals/{r.json()['id']}/approve", json={}, headers=b)
    detail = (await client.get(f"{P}/organizations/{org2['id']}", headers=a)).json()
    assert detail["subscription"]["status"] == "cancelled" and detail["access"] == "read_only"


async def test_an_action_that_fails_on_approval_is_recorded_not_half_done(client, db, pro, two_supers):
    a, _, b, _ = two_supers
    org = await create_org(client, a, pro, start="active", key="fe-key-0006")
    r = await client.post(f"{P}/organizations/{org['id']}/cancel", json={"reason": "fraud suspected", "at_period_end": False}, headers=a)
    approval = r.json()
    # Meanwhile somebody else cancels it for real; the stored request can no longer be carried out.
    from app.models import Subscription
    await db.execute(update(Subscription).values(status="cancelled"))
    await db.commit()
    r = await client.post(f"{P}/approvals/{approval['id']}/approve", json={}, headers=b)
    assert r.status_code == 409
    row = (await client.get(f"{P}/approvals", headers=a)).json()[0]
    assert row["status"] == "failed" and "already cancelled" in row["result"]["error"]


async def test_the_second_approver_must_be_active_and_the_feature_can_be_off(client, db, pro, two_supers, monkeypatch):
    a, _, b, bob = two_supers
    await client.patch(f"{P}/admins/{bob.id}", json={"is_active": False}, headers=a)  # Bob no longer counts
    assert (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).status_code == 204
    await client.patch(f"{P}/modules/analytics", json={"enabled": True, "reason": "reset"}, headers=a)
    await client.patch(f"{P}/admins/{bob.id}", json={"is_active": True}, headers=a)
    assert (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).status_code == 202  # two active again
    monkeypatch.setattr(settings, "PLATFORM_FOUR_EYES", False)
    assert (await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)).status_code == 204


async def test_only_oversight_roles_see_approvals(client, db, two_supers):
    a, _, _, _ = two_supers
    await client.patch(f"{P}/modules/analytics", json=BODY, headers=a)
    for role, code in (("read_only", 200), ("billing", 403), ("support", 403)):
        headers, _ = await operator(client, db, role=role)
        assert (await client.get(f"{P}/approvals", headers=headers)).status_code == code
        if role == "read_only":
            summary = (await client.get(f"{P}/alerts/summary", headers=headers)).json()
            assert summary["pending_approvals"] == 1


# --- audit verification and break-glass recovery ------------------------------------------------------------------------------


async def test_verify_audit_command_catches_tampering_and_alerts(client, db):
    ops, _ = await operator(client, db, email="alice@example.com")
    assert (await verify_audit())["ok"] is True
    assert await alerts_of(db, "audit.chain_broken") == []

    await superuser_sql("ALTER TABLE platform_audit_logs DISABLE TRIGGER platform_audit_logs_no_change")
    try:
        await db.execute(text("UPDATE platform_audit_logs SET action = 'auth.nothing_to_see' WHERE seq = 2"))
        await db.commit()
        result = await verify_audit()
        assert result["ok"] is False and result["first_bad_seq"] == 2
    finally:
        await superuser_sql("ALTER TABLE platform_audit_logs ENABLE TRIGGER platform_audit_logs_no_change")
    [alert] = await alerts_of(db, "audit.chain_broken")
    assert alert.severity == "critical" and "entry #2" in alert.message and ops


async def test_break_glass_reset_replaces_the_authenticator_and_ends_sessions(client, db):
    admin, old_secret = await make_admin(db, email="alice@example.com")
    headers = await sign_in(client, db, admin, old_secret)
    assert (await client.get(f"{P}/me", headers=headers)).status_code == 200

    new_secret, uri, codes = await reset_platform_admin_2fa("alice@example.com")
    assert new_secret != old_secret and len(codes) == 10 and new_secret in uri
    assert (await client.get(f"{P}/me", headers=headers)).status_code == 401  # the old session is gone

    login = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "Correct-Horse-Battery-9"})
    mfa = login.json()["mfa_token"]
    wrong = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": totp_code(old_secret)})
    assert wrong.status_code == 401  # the lost phone's code no longer works
    right = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": totp_code(new_secret)})
    assert right.status_code == 200
    assert "admin.2fa_reset_cli" in (await db.scalars(select(PlatformAuditLog.action))).all()
    [alert] = await alerts_of(db, "admin.2fa_reset")
    assert alert.severity == "critical"
    with pytest.raises(SystemExit):
        await reset_platform_admin_2fa("nobody@example.com")



async def test_encryption_key_rotation(client, db, monkeypatch):
    from app.cli import rotate_encryption_key
    from app.core.platform_security import decrypt_secret
    from app.modules.platform.admin_service import create_admin_record
    from app.models import PlatformAdmin

    old_key = settings.PLATFORM_ENCRYPTION_KEY
    admin, secret, _, codes = await create_admin_record(db, email="alice@example.com", full_name="Alice", role=PlatformRole.SUPER_ADMIN,
                                                        password="Correct-Horse-Battery-9")
    await db.commit()

    # 1. Key replaced, old key still listed: the authenticator and the unused recovery codes keep working.
    monkeypatch.setattr(settings, "PLATFORM_ENCRYPTION_KEY", "n" * 40)
    monkeypatch.setattr(settings, "PLATFORM_ENCRYPTION_KEYS_OLD", [old_key])
    await sign_in(client, db, admin, secret)  # TOTP path (decrypts with the old key)
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "Correct-Horse-Battery-9"})).json()["mfa_token"]
    assert (await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "recovery_code": codes[0]})).status_code == 200

    # 2. Re-encrypt in place, then retire the old key: the authenticator still works.
    assert await rotate_encryption_key() == 1
    monkeypatch.setattr(settings, "PLATFORM_ENCRYPTION_KEYS_OLD", [])
    row = await db.get(PlatformAdmin, admin.id, populate_existing=True)
    assert decrypt_secret(row.totp_secret_enc) == secret
    await sign_in(client, db, admin, secret)
    # Recovery codes cannot be re-hashed (the plaintext is gone), so retiring the old key retires them: reissue after rotating.
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "Correct-Horse-Battery-9"})).json()["mfa_token"]
    assert (await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "recovery_code": codes[1]})).status_code == 401


def test_old_keys_setting_parses_and_validates():
    from app.core.config import Settings

    assert Settings(PLATFORM_ENCRYPTION_KEYS_OLD="a" * 40 + ", " + "b" * 40, _env_file=None).PLATFORM_ENCRYPTION_KEYS_OLD == ["a" * 40, "b" * 40]
    good = dict(ENVIRONMENT="production", SECRET_KEY="a" * 40, PIN_PEPPER="b" * 40, PLATFORM_SECRET_KEY="c" * 40,
                PLATFORM_ENCRYPTION_KEY="d" * 40, PLATFORM_COOKIE_SECURE=True, _env_file=None)
    with pytest.raises(ValueError, match="OLD"):
        Settings(**good, PLATFORM_ENCRYPTION_KEYS_OLD="short")
    with pytest.raises(ValueError, match="OLD"):
        Settings(**good, PLATFORM_ENCRYPTION_KEYS_OLD="d" * 40)  # the current key is not an "old" key
