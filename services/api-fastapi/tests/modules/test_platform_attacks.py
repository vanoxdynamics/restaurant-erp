"""Attack-style tests against the platform surface: forged tokens, hostile input, escalation, replay and evasion."""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import update

from app.core.config import settings
from app.core.platform_security import create_platform_token
from app.core.security import create_access_token
from app.models import PlatformAdmin
from tests.factories import auth_headers
from tests.flows import API
from tests.platform_helpers import PWD, P, create_org, idem, make_admin, make_plan, operator, org_body, sign_in, tenant_login


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def unsigned(claims) -> str:
    """alg=none: the classic 'no signature needed' forgery."""
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."


def claims(admin_id, **extra):
    now = datetime.now(timezone.utc)
    return {"sub": str(admin_id), "typ": "access", "aud": "platform", "iss": settings.PLATFORM_JWT_ISSUER,
            "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=5)).timestamp()), "tv": 0, **extra}


@pytest.fixture
async def pro(db):
    return await make_plan(db, "pro")


# --- forged and stolen tokens ---------------------------------------------------------------------------------------------------


async def test_platform_api_rejects_forged_tokens(client, db):
    admin, secret = await make_admin(db, email="alice@example.com")
    headers = await sign_in(client, db, admin, secret)
    good = headers["Authorization"].split()[1]
    sid = jwt.decode(good, options={"verify_signature": False})["sid"]
    forged = {
        "alg none": unsigned(claims(admin.id, sid=sid)),
        "signed with the tenant key": jwt.encode(claims(admin.id, sid=sid), settings.SECRET_KEY, algorithm="HS256"),
        "signed with a guessed key": jwt.encode(claims(admin.id, sid=sid), "secret", algorithm="HS256"),
        "wrong audience": jwt.encode({**claims(admin.id, sid=sid), "aud": "tenant"}, settings.PLATFORM_SECRET_KEY, algorithm="HS256"),
        "wrong issuer": jwt.encode({**claims(admin.id, sid=sid), "iss": "someone-else"}, settings.PLATFORM_SECRET_KEY, algorithm="HS256"),
        "no audience": jwt.encode({k: v for k, v in claims(admin.id, sid=sid).items() if k != "aud"}, settings.PLATFORM_SECRET_KEY, algorithm="HS256"),
        "expired": jwt.encode({**claims(admin.id, sid=sid), "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())},
                              settings.PLATFORM_SECRET_KEY, algorithm="HS256"),
        "tampered payload": good.rsplit(".", 1)[0].rsplit(".", 1)[0] + "." + b64({**jwt.decode(good, options={"verify_signature": False}), "sub": "0"}) + "." + good.rsplit(".", 1)[1],
        "garbage": "not.a.jwt",
    }
    for name, token in forged.items():
        r = await client.get(f"{P}/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, name
    assert (await client.get(f"{P}/me", headers=headers)).status_code == 200  # the genuine one still works


async def test_valid_signature_is_not_enough_without_a_live_session(client, db):
    admin, _ = await make_admin(db, email="alice@example.com")
    ghost_session = create_platform_token(admin.id, "access", timedelta(minutes=5), {"sid": "11111111-1111-1111-1111-111111111111", "tv": 0})
    assert (await client.get(f"{P}/me", headers={"Authorization": f"Bearer {ghost_session}"})).status_code == 401
    no_sid = create_platform_token(admin.id, "access", timedelta(minutes=5), {"tv": 0})
    assert (await client.get(f"{P}/me", headers={"Authorization": f"Bearer {no_sid}"})).status_code == 401


async def test_tokens_die_with_the_account(client, db):
    boss, boss_secret = await make_admin(db, email="alice@example.com")
    victim, victim_secret = await make_admin(db, role="support", email="sam@example.com")
    stolen = await sign_in(client, db, victim, victim_secret)
    assert (await client.get(f"{P}/me", headers=stolen)).status_code == 200
    await db.execute(update(PlatformAdmin).where(PlatformAdmin.id == victim.id).values(is_active=False))
    await db.commit()
    assert (await client.get(f"{P}/me", headers=stolen)).status_code == 401
    await db.execute(update(PlatformAdmin).where(PlatformAdmin.id == victim.id).values(is_active=True, token_version=5))
    await db.commit()
    assert (await client.get(f"{P}/me", headers=stolen)).status_code == 401  # a version bump revokes what was already issued
    assert boss_secret


async def test_tenant_api_rejects_forged_tokens(client, db, owner):
    forged = {
        "alg none": unsigned({"sub": str(owner.id), "iss": settings.JWT_ISSUER, "iat": 1, "exp": 4102444800, "tv": 0, "role": "owner"}),
        "signed with the platform key": jwt.encode({"sub": str(owner.id), "iss": settings.JWT_ISSUER, "iat": 1, "exp": 4102444800, "tv": 0},
                                                   settings.PLATFORM_SECRET_KEY, algorithm="HS256"),
        "role escalation, wrong key": jwt.encode({"sub": str(owner.id), "iss": settings.JWT_ISSUER, "iat": 1, "exp": 4102444800, "tv": 0,
                                                  "role": "owner"}, "x" * 40, algorithm="HS256"),
    }
    for name, token in forged.items():
        assert (await client.get(f"{API}/staff/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401, name


async def test_a_tenant_cannot_mint_support_access_by_claiming_it(client, db, pro):
    """An `imp` claim must come from a signed token; adding it to a real tenant token (wrong key) changes nothing."""
    ops, _ = await operator(client, db, email="alice@example.com")
    org = await create_org(client, ops, pro, start="active", key="atk-key-0001")
    tenant = await tenant_login(client, org_body(pro))
    token = tenant["Authorization"].split()[1]
    forged = jwt.encode({**jwt.decode(token, options={"verify_signature": False}), "imp": "someone", "amr": "pwd"}, "k" * 40, algorithm="HS256")
    assert (await client.get(f"{API}/staff/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
    assert org["id"]


# --- cross-origin and cookies --------------------------------------------------------------------------------------------------


async def test_cors_preflight_from_an_untrusted_origin_gets_nothing(client):
    evil = await client.options(f"{P}/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET",
                                                    "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in evil.headers
    trusted = await client.options(f"{P}/me", headers={"Origin": settings.ALLOWED_ORIGINS[0], "Access-Control-Request-Method": "GET",
                                                       "Access-Control-Request-Headers": "authorization"})
    assert trusted.headers.get("access-control-allow-origin") == settings.ALLOWED_ORIGINS[0]
    simple = await client.get(f"{P}/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


async def test_production_cookies_are_secure(client, db, monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_COOKIE_SECURE", True)
    admin, secret = await make_admin(db, email="alice@example.com")
    from app.core.platform_security import totp_code
    login = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD})
    r = await client.post(f"{P}/auth/2fa", json={"mfa_token": login.json()["mfa_token"], "code": totp_code(secret)})
    cookies = [c.lower() for c in r.headers.get_list("set-cookie")]
    assert len(cookies) == 2 and all("secure" in c and "samesite=strict" in c for c in cookies)
    assert any("httponly" in c for c in cookies if c.startswith("pa_refresh"))


# --- hostile input --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["' OR 1=1 --", "%", "_", "a\\", "\"; DROP TABLE organizations; --", "<script>alert(1)</script>", "ü" * 500])
async def test_search_input_is_inert(client, db, pro, payload):
    ops, _ = await operator(client, db, email="alice@example.com")
    await create_org(client, ops, pro, key="atk-key-0002")
    r = await client.get(f"{P}/organizations", params={"q": payload[:100]}, headers=ops)
    assert r.status_code == 200, r.text
    assert len((await client.get(f"{P}/organizations", headers=ops)).json()["items"]) == 1  # nothing was dropped


async def test_nul_bytes_and_control_characters_are_refused_not_crashed(client, db):
    ops, _ = await operator(client, db, email="alice@example.com")
    for value in ("a\x00b", "line\nbreak", "tab\there"):
        r = await client.get(f"{P}/organizations", params={"q": value}, headers=ops)
        assert r.status_code in (200, 422), (repr(value), r.status_code)  # never a 500
    r = await client.post(f"{P}/auth/login", json={"email": "a@b.co", "password": "x\x00y"})
    assert r.status_code in (401, 422)


async def test_oversized_bodies_are_refused_early(client):
    big = "A" * (settings.PLATFORM_MAX_BODY_BYTES + 10)
    r = await client.post(f"{P}/auth/login", content=json.dumps({"email": "a@b.co", "password": big}),
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 413


# --- escalation, replay and evasion --------------------------------------------------------------------------------------------


async def test_extra_fields_cannot_escalate_privileges(client, db, pro):
    ops, _ = await operator(client, db, email="alice@example.com")
    support, sam = await operator(client, db, role="support", email="sam@example.com")
    # Mass assignment: smuggling role / status / token fields into requests that do not accept them.
    r = await client.patch(f"{P}/admins/{sam.id}", json={"role": "super_admin"}, headers=support)
    assert r.status_code == 403
    me = (await client.get(f"{P}/me", headers=support)).json()
    assert me["role"] == "support"
    org = await create_org(client, ops, pro, start="active", key="atk-key-0003")
    await client.post(f"{P}/organizations/{org['id']}/suspend", json={"reason": "stop for test"}, headers=ops)
    r = await client.patch(f"{P}/organizations/{org['id']}", json={"reason": "rename", "name": "New Name", "status": "active",
                                                                    "suspended_at": None}, headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "suspended" and r.json()["name"] == "New Name"
    login = await client.post(f"{P}/auth/login", json={"email": "sam@example.com", "password": PWD, "role": "super_admin"})
    assert login.status_code == 200 and "access_token" not in login.json()  # extra field ignored; 2FA still required


async def test_idempotency_keys_do_not_cross_operators(client, db, pro):
    a, _ = await operator(client, db, email="alice@example.com")
    b, _ = await operator(client, db, role="billing", email="bea@example.com")
    first = await client.post(f"{P}/organizations", json=org_body(pro), headers={**a, **idem("shared-key-0001")})
    assert first.status_code == 201
    other = await client.post(f"{P}/organizations", json=org_body(pro, slug="other-diner"), headers={**b, **idem("shared-key-0001")})
    assert other.status_code == 201 and other.headers.get("idempotent-replay") is None  # B never receives A's cached response
    assert other.json()["organization"]["slug"] == "other-diner"


async def test_lockout_cannot_be_dodged_by_spoofing_the_client_address(client, db):
    admin, _ = await make_admin(db, email="alice@example.com")
    for i in range(5):
        r = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": "wrong-password-1A!"},
                              headers={"X-Forwarded-For": f"203.0.113.{i}", "X-Real-IP": f"198.51.100.{i}"})
        assert r.status_code == 401
    r = await client.post(f"{P}/auth/login", json={"email": admin.email, "password": PWD}, headers={"X-Forwarded-For": "192.0.2.77"})
    assert r.status_code == 429  # per-account lockout holds whatever address the attacker claims


async def test_tenant_routes_do_not_hang_off_the_platform_prefix(client, db, owner, pro):
    ops, _ = await operator(client, db, email="alice@example.com")
    for path in ("/staff", "/orders", "/catalog/items"):
        assert (await client.get(f"{P}{path}", headers=ops)).status_code == 404
        assert (await client.get(f"{API}{path}", headers={"Authorization": ops["Authorization"]})).status_code == 401  # platform token is useless here
    assert auth_headers(owner)


async def test_error_responses_do_not_leak_internals(client, db):
    ops, _ = await operator(client, db, email="alice@example.com")
    for method, path in (("GET", f"{P}/organizations/not-a-uuid"), ("GET", f"{P}/organizations/00000000-0000-0000-0000-000000000000"),
                         ("POST", f"{P}/invoices/00000000-0000-0000-0000-000000000000/payments")):
        r = await client.request(method, path, headers=ops, json={} if method == "POST" else None)
        text = r.text.lower()
        assert r.status_code in (400, 404, 422) and "traceback" not in text and "sqlalchemy" not in text and "asyncpg" not in text
    r = await client.get(f"{P}/organizations", params={"limit": 100000}, headers=ops)
    assert r.status_code == 422  # pagination is capped
    assert create_access_token  # (imported to document that tenant tokens are minted elsewhere)


@pytest.mark.parametrize("wildcard", ["%", "_", "a%a", "___"])
async def test_search_wildcards_are_literal(client, db, pro, wildcard):
    ops, _ = await operator(client, db, email="alice@example.com")
    await create_org(client, ops, pro, key="atk-key-0004")
    r = await client.get(f"{P}/organizations", params={"q": wildcard}, headers=ops)
    assert r.status_code == 200 and r.json()["items"] == []  # '%' and '_' are not 'match everything'
    assert len((await client.get(f"{P}/organizations", params={"q": "acme"}, headers=ops)).json()["items"]) == 1
