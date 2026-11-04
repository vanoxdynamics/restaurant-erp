"""Platform console security: sign-in with 2FA, sessions, refresh rotation, step-up, RBAC and the audit chain."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.enums import PlatformRole
from app.core.platform_middleware import PlatformGuardMiddleware
from app.core.platform_security import (
    create_platform_token,
    decrypt_secret,
    generate_recovery_codes,
    hash_recovery_code,
    totp_code,
    validate_admin_password,
    verify_totp,
)
from app.modules.platform.admin_service import create_admin_record
from app.models import PlatformAdmin, PlatformAuditLog, PlatformSession
from app.modules.platform.audit import verify_chain
from tests.factories import auth_headers
from tests.conftest import superuser_sql
from tests.flows import API

PWD = "Correct-Horse-Battery-9"
P = f"{API}/platform"


async def make_admin(db, role="super_admin", email="root@example.com", password=PWD):
    admin, secret, _, codes = await create_admin_record(
        db, email=email, full_name="Root Admin", role=PlatformRole(role), password=password)
    await db.commit()
    return admin, secret, codes


async def sign_in(client, db, admin, secret, password=PWD):
    """Full two-step sign-in. Resets the TOTP replay guard so tests may sign in repeatedly."""
    await db.execute(update(PlatformAdmin).where(PlatformAdmin.id == admin.id).values(totp_last_step=0))
    await db.commit()
    r = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": password})
    assert r.status_code == 200, r.text
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": r.json()["mfa_token"], "code": totp_code(secret)})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def step_up(client, db, admin, secret, tokens, password=PWD):
    await db.execute(update(PlatformAdmin).where(PlatformAdmin.id == admin.id).values(totp_last_step=0))
    await db.commit()
    r = await client.post(f"{P}/auth/step-up", json={"password": password, "code": totp_code(secret)},
                          headers=bearer(tokens))
    assert r.status_code == 204, r.text


@pytest.fixture
async def root(db):
    return await make_admin(db)


# --- primitives -------------------------------------------------------------------------


def test_totp_matches_rfc6238_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
    assert totp_code(secret, at=59) == "287082"  # RFC 6238 appendix B, last 6 of 94287082
    assert verify_totp(secret, "287082", at=59) == 1
    assert verify_totp(secret, "287082", last_step=1, at=59) is None  # same step cannot be reused
    assert verify_totp(secret, "000000", at=59) is None
    assert verify_totp(secret, "12345", at=59) is None and verify_totp(secret, "abcdef", at=59) is None


@pytest.mark.parametrize("password", ["short1A!", "alllowercaseletters", "NoDigitsOrSymbolsHere", "aaaaaaaaaaaaaaA1"])
def test_weak_passwords_rejected(password):
    with pytest.raises(ValueError):
        validate_admin_password(password)


def test_password_cannot_contain_email_name():
    with pytest.raises(ValueError):
        validate_admin_password("Jonathan-Lovelace-77", "jonathan@example.com")
    validate_admin_password(PWD, "root@example.com")


def test_recovery_codes_are_unique_and_hashed():
    codes = generate_recovery_codes()
    assert len(set(codes)) == 10 and hash_recovery_code(codes[0]) != codes[0]
    assert hash_recovery_code(codes[0].upper()) == hash_recovery_code(codes[0])


# --- sign-in ---------------------------------------------------------------------------------


async def test_full_sign_in_and_hardened_response(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    assert tokens["admin"]["email"] == "root@example.com" and tokens["expires_in"] == 600
    r = await client.get(f"{P}/me", headers=bearer(tokens))
    assert r.status_code == 200 and r.json()["role"] == "super_admin"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff" and "strict-transport-security" in r.headers


async def test_refresh_cookie_is_httponly_strict_and_csrf_cookie_readable(client, db, root):
    admin, secret, _ = root
    await db.execute(update(PlatformAdmin).values(totp_last_step=0))
    await db.commit()
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": totp_code(secret)})
    cookies = {c.split("=", 1)[0]: c.lower() for c in r.headers.get_list("set-cookie")}
    assert "httponly" in cookies["pa_refresh"] and "samesite=strict" in cookies["pa_refresh"]
    assert "path=/api/v1/platform/auth" in cookies["pa_refresh"]
    assert "httponly" not in cookies["pa_csrf"] and "samesite=strict" in cookies["pa_csrf"]


async def test_wrong_password_and_unknown_email_look_identical(client, db, root):
    admin, _, _ = root
    bad = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "wrong-password-123"})
    ghost = await client.post(f"{P}/auth/login", json={"email": "ghost@example.com", "password": "wrong-password-123"})
    assert bad.status_code == ghost.status_code == 401
    assert bad.json() == ghost.json() and bad.json()["error"]["code"] == "invalid_credentials"
    actions = [a for a in (await db.scalars(select(PlatformAuditLog.action))).all()]
    assert actions.count("auth.login_failed") == 2


async def test_wrong_totp_rejected_and_audited(client, db, root):
    admin, secret, _ = root
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
    wrong = "000000" if totp_code(secret) != "000000" else "111111"
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": wrong})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_code"
    assert "auth.2fa_failed" in (await db.scalars(select(PlatformAuditLog.action))).all()


async def test_totp_code_cannot_be_replayed(client, db, root):
    admin, secret, _ = root
    await sign_in(client, db, admin, secret)
    # Same code, new challenge, replay guard NOT reset this time.
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": totp_code(secret)})
    assert r.status_code == 401


async def test_recovery_code_works_once(client, db, root):
    admin, secret, codes = root
    for expected in (200, 401):
        mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
        r = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "recovery_code": codes[0]})
        assert r.status_code == expected, r.text


async def test_2fa_requires_exactly_one_factor(client, db, root):
    admin, _, _ = root
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
    assert (await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa})).status_code == 422
    assert (await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": "123456", "recovery_code": "x"})).status_code == 422


async def test_password_attempts_are_rate_limited(client, db, root):
    admin, _, _ = root
    for _ in range(5):
        assert (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "nope-nope-nope-1"})).status_code == 401
    r = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})  # even the right password
    assert r.status_code == 429 and "retry-after" in r.headers


async def test_totp_attempts_are_rate_limited(client, db, root):
    admin, secret, _ = root
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
    wrong = "000000" if totp_code(secret) != "000000" else "111111"
    for _ in range(5):
        assert (await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": wrong})).status_code == 401
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": mfa, "code": totp_code(secret)})
    assert r.status_code == 429


async def test_encrypted_totp_secret_at_rest(db, root):
    admin, secret, _ = root
    stored = (await db.scalar(select(PlatformAdmin.totp_secret_enc).where(PlatformAdmin.id == admin.id)))
    assert secret not in stored and decrypt_secret(stored) == secret
    assert admin.hashed_password.startswith("$argon2id$")


# --- token separation -------------------------------------------------------------------------------


async def test_mfa_challenge_is_not_an_access_token(client, db, root):
    admin, _, _ = root
    mfa = (await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})).json()["mfa_token"]
    assert (await client.get(f"{P}/me", headers={"Authorization": f"Bearer {mfa}"})).status_code == 401


async def test_tenant_and_platform_tokens_do_not_cross(client, db, root, owner):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    tenant_headers = auth_headers(owner)
    assert (await client.get(f"{P}/me", headers=tenant_headers)).status_code == 401
    assert (await client.get(f"{API}/staff/me", headers=bearer(tokens))).status_code == 401
    # Even a token minted with the platform key but the wrong audience/type is refused.
    forged = create_platform_token(admin.id, "access", timedelta(minutes=5), {"sid": "x"})
    assert (await client.get(f"{P}/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_no_token_is_unauthorized(client):
    for path in ("/me", "/admins", "/sessions", "/audit-logs"):
        assert (await client.get(f"{P}{path}")).status_code == 401


# --- sessions, refresh, logout -------------------------------------------------------------------------


def csrf(tokens):
    return {"X-CSRF-Token": tokens["csrf_token"]}


async def test_refresh_rotates_and_reuse_kills_the_session(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    old_cookie = client.cookies.get("pa_refresh", path="/api/v1/platform/auth")
    assert old_cookie

    r = await client.post(f"{P}/auth/refresh", headers=csrf(tokens))
    assert r.status_code == 200, r.text
    new = r.json()
    assert (await client.get(f"{P}/me", headers=bearer(new))).status_code == 200

    # An attacker replays the old refresh token.
    client.cookies.set("pa_refresh", old_cookie, path="/api/v1/platform/auth")
    r = await client.post(f"{P}/auth/refresh", headers=csrf(new))
    assert r.status_code == 401
    # The whole session is dead, including the legitimate user's newest tokens.
    assert (await client.get(f"{P}/me", headers=bearer(new))).status_code == 401
    assert "auth.refresh_reuse_detected" in (await db.scalars(select(PlatformAuditLog.action))).all()


async def test_refresh_requires_csrf_header(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    assert (await client.post(f"{P}/auth/refresh")).status_code == 403
    assert (await client.post(f"{P}/auth/refresh", headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (await client.post(f"{P}/auth/refresh", headers=csrf(tokens))).status_code == 200


async def test_idle_session_cannot_refresh_or_call_api(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    idle = timedelta(minutes=settings.PLATFORM_SESSION_IDLE_MINUTES + 5)
    await db.execute(update(PlatformSession).values(last_seen_at=datetime.now(timezone.utc) - idle))
    await db.commit()
    assert (await client.get(f"{P}/me", headers=bearer(tokens))).status_code == 401
    tokens2 = await sign_in(client, db, admin, secret)
    await db.execute(update(PlatformSession).where(PlatformSession.id != None)  # noqa: E711
                     .values(last_seen_at=datetime.now(timezone.utc) - idle))
    await db.commit()
    assert (await client.post(f"{P}/auth/refresh", headers=csrf(tokens2))).status_code == 401


async def test_absolute_session_lifetime(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    await db.execute(update(PlatformSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()
    assert (await client.get(f"{P}/me", headers=bearer(tokens))).status_code == 401


async def test_logout_ends_the_session(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    refresh_cookie = client.cookies.get("pa_refresh", path="/api/v1/platform/auth")
    assert (await client.post(f"{P}/auth/logout", headers=csrf(tokens))).status_code == 204
    assert (await client.get(f"{P}/me", headers=bearer(tokens))).status_code == 401
    # The browser dropped its cookies; a copied refresh token must still be dead on the server.
    client.cookies.set("pa_refresh", refresh_cookie, path="/api/v1/platform/auth")
    client.cookies.set("pa_csrf", tokens["csrf_token"], path="/")
    assert (await client.post(f"{P}/auth/refresh", headers=csrf(tokens))).status_code == 401


async def test_session_listing_and_revocation(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    listed = (await client.get(f"{P}/sessions", headers=bearer(tokens))).json()
    assert len(listed) == 1 and listed[0]["current"] is True
    assert (await client.delete(f"{P}/sessions/{listed[0]['id']}", headers=bearer(tokens))).status_code == 204
    assert (await client.get(f"{P}/me", headers=bearer(tokens))).status_code == 401


# --- step-up and RBAC -------------------------------------------------------------------------------------------


NEW_ADMIN = {"email": "support@example.com", "full_name": "Sam Support", "role": "support", "password": "Sup-Port-Pass-2025!"}


async def test_creating_an_admin_needs_step_up_then_works(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    r = await client.post(f"{P}/admins", json=NEW_ADMIN, headers=bearer(tokens))
    assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"

    bad = await client.post(f"{P}/auth/step-up", json={"password": "wrong-password-1A!", "code": totp_code(secret)},
                            headers=bearer(tokens))
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "step_up_failed"

    await step_up(client, db, admin, secret, tokens)
    r = await client.post(f"{P}/admins", json=NEW_ADMIN, headers=bearer(tokens))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["admin"]["role"] == "support" and len(body["recovery_codes"]) == 10
    assert body["provisioning_uri"].startswith("otpauth://totp/") and body["totp_secret"] in body["provisioning_uri"]

    # The handed-over secret really works for the new operator.
    new_admin = await db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == NEW_ADMIN["email"]))
    new_tokens = await sign_in(client, db, new_admin, body["totp_secret"], NEW_ADMIN["password"])
    assert (await client.get(f"{P}/me", headers=bearer(new_tokens))).json()["role"] == "support"


async def test_step_up_expires(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    await step_up(client, db, admin, secret, tokens)
    expired = timedelta(minutes=settings.PLATFORM_STEP_UP_MINUTES + 1)
    await db.execute(update(PlatformSession).values(step_up_at=datetime.now(timezone.utc) - expired))
    await db.commit()
    r = await client.post(f"{P}/admins", json=NEW_ADMIN, headers=bearer(tokens))
    assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"


async def test_weak_password_and_duplicate_email_rejected(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    await step_up(client, db, admin, secret, tokens)
    r = await client.post(f"{P}/admins", json={**NEW_ADMIN, "password": "weakpass"}, headers=bearer(tokens))
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"
    r = await client.post(f"{P}/admins", json={**NEW_ADMIN, "email": "ROOT@example.com"}, headers=bearer(tokens))
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize("role,admins,audit,verify", [
    ("support", 403, 403, 403),
    ("billing", 403, 403, 403),
    ("read_only", 403, 200, 403),
    ("super_admin", 200, 200, 200),
])
async def test_role_matrix(client, db, role, admins, audit, verify):
    admin, secret, _ = await make_admin(db, role=role, email=f"{role}@example.com")
    tokens = await sign_in(client, db, admin, secret)
    assert (await client.get(f"{P}/admins", headers=bearer(tokens))).status_code == admins
    assert (await client.get(f"{P}/audit-logs", headers=bearer(tokens))).status_code == audit
    assert (await client.get(f"{P}/audit-logs/verify", headers=bearer(tokens))).status_code == verify


async def test_changing_role_or_deactivating_ends_sessions(client, db, root):
    admin, secret, _ = root
    other, other_secret, _ = await make_admin(db, role="support", email="other@example.com")
    other_tokens = await sign_in(client, db, other, other_secret)
    tokens = await sign_in(client, db, admin, secret)
    await step_up(client, db, admin, secret, tokens)

    r = await client.patch(f"{P}/admins/{other.id}", json={"is_active": False}, headers=bearer(tokens))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.get(f"{P}/me", headers=bearer(other_tokens))).status_code == 401
    # Deactivated: cannot sign in again.
    r = await client.post(f"{P}/auth/login", json={"email": other.email, "password": PWD})
    assert r.status_code == 401


async def test_cannot_demote_self_or_last_super_admin(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    await step_up(client, db, admin, secret, tokens)
    r = await client.patch(f"{P}/admins/{admin.id}", json={"role": "support"}, headers=bearer(tokens))
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_change"

    other, other_secret, _ = await make_admin(db, role="super_admin", email="second@example.com")
    other_tokens = await sign_in(client, db, other, other_secret)
    await step_up(client, db, other, other_secret, other_tokens)
    # The second super admin demotes the first; the second is now the last one and cannot be removed by anyone.
    assert (await client.patch(f"{P}/admins/{admin.id}", json={"role": "support"}, headers=bearer(other_tokens))).status_code == 200
    r = await client.patch(f"{P}/admins/{other.id}", json={"is_active": False}, headers=bearer(other_tokens))
    assert r.status_code == 403  # self-change guard fires first
    support_tokens = await sign_in(client, db, admin, secret)
    assert (await client.patch(f"{P}/admins/{other.id}", json={"is_active": False}, headers=bearer(support_tokens))).status_code == 403


# --- audit chain -----------------------------------------------------------------------------------------------------


async def test_audit_chain_verifies_and_detects_tampering(client, db, root):
    admin, secret, _ = root
    tokens = await sign_in(client, db, admin, secret)
    await step_up(client, db, admin, secret, tokens)
    await client.post(f"{P}/admins", json=NEW_ADMIN, headers=bearer(tokens))

    r = await client.get(f"{P}/audit-logs/verify", headers=bearer(tokens))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["checked"] >= 3

    entries = (await client.get(f"{P}/audit-logs", headers=bearer(tokens))).json()
    assert entries[0]["seq"] > entries[-1]["seq"] and {"auth.login", "auth.step_up", "admin.create"} <= {e["action"] for e in entries}

    # The database refuses to rewrite or delete history...
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("UPDATE platform_audit_logs SET action = 'x' WHERE seq = 1"))
    await db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM platform_audit_logs WHERE seq = 1"))
    await db.rollback()

    # ...and even a privileged tamperer who bypasses the trigger is caught by the chain.
    await superuser_sql("ALTER TABLE platform_audit_logs DISABLE TRIGGER platform_audit_logs_no_change")
    try:
        await db.execute(text("UPDATE platform_audit_logs SET action = 'auth.nothing_to_see' WHERE seq = 2"))
        await db.commit()
        result = await verify_chain(db)
        assert result["ok"] is False and result["first_bad_seq"] == 2
    finally:
        await superuser_sql("ALTER TABLE platform_audit_logs ENABLE TRIGGER platform_audit_logs_no_change")


# --- network guard ------------------------------------------------------------------------------------------------------


async def _call(mw, path, client_ip):
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x-app", b"1")]})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    guard = PlatformGuardMiddleware(app, mw)
    await guard({"type": "http", "path": path, "client": (client_ip, 1234), "headers": []}, None, send)
    return sent


async def test_ip_allowlist_hides_the_console():
    blocked = await _call(["10.0.0.0/8"], "/api/v1/platform/auth/login", "203.0.113.9")
    assert blocked[0]["status"] == 404
    allowed = await _call(["10.0.0.0/8"], "/api/v1/platform/auth/login", "10.2.3.4")
    assert allowed[0]["status"] == 200 and (b"cache-control", b"no-store") in allowed[0]["headers"]
    # Tenant routes are never affected by the console allowlist.
    tenant = await _call(["10.0.0.0/8"], "/api/v1/orders", "203.0.113.9")
    assert tenant[0]["status"] == 200 and (b"cache-control", b"no-store") not in tenant[0]["headers"]
    # An empty allowlist means no network restriction (development).
    assert (await _call([], "/api/v1/platform/me", "203.0.113.9"))[0]["status"] == 200


async def test_tenant_api_unaffected(client, db, owner, branch):
    assert (await client.get(f"{API}/me/entitlements", headers=auth_headers(owner))).status_code == 200


# --- production configuration -------------------------------------------------------------------------------------------


def _prod(**overrides):
    from app.core.config import Settings
    good = {"ENVIRONMENT": "production", "SECRET_KEY": "a" * 40, "PIN_PEPPER": "b" * 40,
            "PLATFORM_SECRET_KEY": "c" * 40, "PLATFORM_ENCRYPTION_KEY": "d" * 40,
            "PLATFORM_COOKIE_SECURE": True}
    return Settings(**{**good, **overrides}, _env_file=None)


def test_production_requires_distinct_platform_secrets():
    assert _prod().PLATFORM_COOKIE_SECURE is True
    with pytest.raises(ValueError, match="PLATFORM_SECRET_KEY"):
        _prod(PLATFORM_SECRET_KEY="dev-platform-secret-change-in-production-min-32-chars")
    with pytest.raises(ValueError, match="PLATFORM_ENCRYPTION_KEY"):
        _prod(PLATFORM_ENCRYPTION_KEY="short")
    with pytest.raises(ValueError, match="must all differ"):
        _prod(PLATFORM_SECRET_KEY="a" * 40)  # same as the tenant signing key
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        _prod(PLATFORM_COOKIE_SECURE=False)
    with pytest.raises(ValueError):
        _prod(PLATFORM_IP_ALLOWLIST="not-a-cidr")


def test_defaults_suit_a_small_operator_team():
    """Password + authenticator at sign-in; then an hour between re-checks, and no second approver unless switched on."""
    from app.core.config import Settings
    defaults = {name: field.default for name, field in Settings.model_fields.items()}
    assert defaults["PLATFORM_FOUR_EYES"] is False
    assert defaults["PLATFORM_STEP_UP_MINUTES"] == 60
    assert (defaults["PLATFORM_SESSION_IDLE_MINUTES"], defaults["PLATFORM_SESSION_ABSOLUTE_HOURS"]) == (60, 12)
