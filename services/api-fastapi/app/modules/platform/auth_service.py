"""Platform console authentication: password -> TOTP/recovery code -> short access token + rotating refresh token."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Forbidden, Unauthorized
from app.core.platform_security import (
    MFA_TOKEN_TTL_SECONDS,
    TOKEN_ACCESS,
    TOKEN_MFA,
    create_platform_token,
    decode_platform_token,
    decrypt_secret,
    recovery_code_hashes,
    hash_refresh_token,
    new_csrf_token,
    new_refresh_token,
    verify_totp,
)
from app.core.rate_limit import LoginRateLimiter
from app.core.security import DUMMY_HASH, verify_password
from app.models.platform import PlatformAdmin, PlatformRecoveryCode, PlatformSession
from app.core.enums import AlertSeverity
from app.modules.platform.alerts import raise_alert
from app.modules.platform.audit import RequestMeta, append_audit
from app.schemas.platform import LoginRequest, MfaChallenge, StepUpRequest, TwoFactorRequest

GENERIC_LOGIN_ERROR = "Invalid credentials"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    csrf_token: str
    refresh_max_age: int  # seconds until the session's absolute expiry
    admin: PlatformAdmin


def _access_token(admin: PlatformAdmin, session: PlatformSession) -> Tuple[str, int]:
    ttl = timedelta(minutes=settings.PLATFORM_ACCESS_TOKEN_MINUTES)
    token = create_platform_token(admin.id, TOKEN_ACCESS, ttl, {"sid": str(session.id), "tv": admin.token_version})
    return token, int(ttl.total_seconds())


def _issue(admin: PlatformAdmin, session: PlatformSession, refresh_token: str) -> IssuedTokens:
    access, expires_in = _access_token(admin, session)
    return IssuedTokens(
        access_token=access, expires_in=expires_in, refresh_token=refresh_token, csrf_token=new_csrf_token(),
        refresh_max_age=max(int((session.expires_at - _now()).total_seconds()), 1), admin=admin,
    )


async def revoke_session(db: AsyncSession, session: PlatformSession, reason: str) -> None:
    if session.revoked_at is None:
        session.revoked_at = _now()
        session.revoked_reason = reason


async def revoke_all_sessions(db: AsyncSession, admin_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(PlatformSession)
        .where(PlatformSession.admin_id == admin_id, PlatformSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason)
    )


# --- step 1: password ------------------------------------------------------------------------------


async def login(db: AsyncSession, redis_client: aioredis.Redis, payload: LoginRequest, meta: RequestMeta) -> MfaChallenge:
    email = payload.email.lower()
    ip_limiter = LoginRateLimiter(redis_client, scope="platform-ip", key=meta.ip or "unknown")
    acct_limiter = LoginRateLimiter(redis_client, scope="platform-acct", key=email)
    await ip_limiter.check()
    await acct_limiter.check()

    admin = await db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == email))
    password = payload.password.get_secret_value()
    # Always run one hash verification so timing does not reveal whether the account exists.
    password_ok = verify_password(password, admin.hashed_password if admin else DUMMY_HASH)
    if admin is None or not admin.is_active or not password_ok:
        tripped_ip = await ip_limiter.record_failure()
        tripped_acct = await acct_limiter.record_failure()
        await append_audit(db, action="auth.login_failed", admin_id=admin.id if admin else None,
                           after={"email": email[:254]}, meta=meta)
        if tripped_ip or tripped_acct:  # the failure that triggers the lockout is the interesting one
            await raise_alert(db, "auth.lockout", AlertSeverity.WARNING, f"Console sign-in locked after repeated failures for {email}",
                              {"email": email, "ip": meta.ip, "known_account": admin is not None})
        await db.commit()
        raise Unauthorized(GENERIC_LOGIN_ERROR, code="invalid_credentials")

    token = create_platform_token(admin.id, TOKEN_MFA, timedelta(seconds=MFA_TOKEN_TTL_SECONDS), {"tv": admin.token_version})
    return MfaChallenge(mfa_token=token, expires_in=MFA_TOKEN_TTL_SECONDS)


# --- step 2: second factor -----------------------------------------------------------------------------


async def _consume_recovery_code(db: AsyncSession, admin_id: uuid.UUID, code: str) -> bool:
    row = await db.scalar(
        select(PlatformRecoveryCode)
        .where(PlatformRecoveryCode.admin_id == admin_id, PlatformRecoveryCode.code_hash.in_(recovery_code_hashes(code)),
               PlatformRecoveryCode.used_at.is_(None))
        .with_for_update()
    )
    if row is None:
        return False
    row.used_at = _now()
    return True


async def _check_second_factor(db: AsyncSession, admin: PlatformAdmin, code: Optional[str], recovery_code: Optional[str]) -> bool:
    if recovery_code is not None:
        return await _consume_recovery_code(db, admin.id, recovery_code)
    step = verify_totp(decrypt_secret(admin.totp_secret_enc), code or "", admin.totp_last_step)
    if step is None:
        return False
    admin.totp_last_step = step  # a code can be used once
    return True


async def verify_second_factor(
    db: AsyncSession, redis_client: aioredis.Redis, payload: TwoFactorRequest, meta: RequestMeta
) -> IssuedTokens:
    claims = decode_platform_token(payload.mfa_token, TOKEN_MFA)
    if claims is None:
        raise Unauthorized("The sign-in step expired; start again", code="mfa_expired")
    admin = await db.scalar(select(PlatformAdmin).where(PlatformAdmin.id == uuid.UUID(claims["sub"])).with_for_update())
    if admin is None or not admin.is_active or admin.token_version != claims.get("tv"):
        raise Unauthorized(GENERIC_LOGIN_ERROR, code="invalid_credentials")

    acct_limiter = LoginRateLimiter(redis_client, scope="platform-2fa", key=str(admin.id))
    ip_limiter = LoginRateLimiter(redis_client, scope="platform-ip", key=meta.ip or "unknown")
    await acct_limiter.check()
    await ip_limiter.check()

    if not await _check_second_factor(db, admin, payload.code, payload.recovery_code):
        tripped_acct = await acct_limiter.record_failure()
        tripped_ip = await ip_limiter.record_failure()
        await append_audit(db, action="auth.2fa_failed", admin_id=admin.id, meta=meta)
        if tripped_acct or tripped_ip:
            await raise_alert(db, "auth.lockout_2fa", AlertSeverity.CRITICAL,
                              f"Second factor locked for {admin.email}: someone has the password but not the code",
                              {"admin": admin.email, "ip": meta.ip})
        await db.commit()
        raise Unauthorized("Invalid verification code", code="invalid_code")

    await acct_limiter.reset()
    await ip_limiter.reset()
    refresh_token, refresh_hash = new_refresh_token()
    session = PlatformSession(
        admin_id=admin.id, refresh_hash=refresh_hash, ip=meta.ip, user_agent=meta.user_agent,
        expires_at=_now() + timedelta(hours=settings.PLATFORM_SESSION_ABSOLUTE_HOURS),
    )
    db.add(session)
    admin.last_login_at = _now()
    await db.flush()
    await append_audit(db, action="auth.login", admin_id=admin.id, target_type="session", target_id=session.id,
                       after={"used_recovery_code": payload.recovery_code is not None}, meta=meta)
    await db.commit()
    return _issue(admin, session, refresh_token)


# --- refresh / logout --------------------------------------------------------------------------------------


async def refresh(db: AsyncSession, refresh_token: Optional[str], meta: RequestMeta) -> IssuedTokens:
    if not refresh_token:
        raise Unauthorized("No active session", code="no_session")
    digest = hash_refresh_token(refresh_token)
    session = await db.scalar(select(PlatformSession).where(PlatformSession.refresh_hash == digest).with_for_update())
    if session is None:
        stolen = await db.scalar(
            select(PlatformSession).where(PlatformSession.prev_refresh_hash == digest).with_for_update()
        )
        if stolen is not None:
            # An already-rotated token came back: either it was stolen or the client is broken. Kill the session.
            await revoke_session(db, stolen, "refresh_reuse")
            await append_audit(db, action="auth.refresh_reuse_detected", admin_id=stolen.admin_id,
                               target_type="session", target_id=stolen.id, meta=meta)
            await raise_alert(db, "auth.refresh_reuse", AlertSeverity.CRITICAL,
                              "A used refresh token was replayed: possible session theft. The session was ended.",
                              {"admin_id": stolen.admin_id, "session_id": stolen.id, "ip": meta.ip})
            await db.commit()
        raise Unauthorized("No active session", code="no_session")

    now = _now()
    admin = await db.get(PlatformAdmin, session.admin_id)
    idle_limit = timedelta(minutes=settings.PLATFORM_SESSION_IDLE_MINUTES)
    reason = None
    if session.revoked_at is not None:
        reason = "revoked"
    elif now >= session.expires_at:
        reason = "expired"
    elif now - session.last_seen_at > idle_limit:
        reason = "idle"
    elif admin is None or not admin.is_active:
        reason = "admin_inactive"
    if reason:
        if session.revoked_at is None:
            await revoke_session(db, session, reason)
            await db.commit()
        raise Unauthorized("Session ended; sign in again", code="session_ended")

    new_token, new_hash = new_refresh_token()
    session.prev_refresh_hash = session.refresh_hash
    session.refresh_hash = new_hash
    session.last_seen_at = now
    await db.commit()
    return _issue(admin, session, new_token)


async def logout(db: AsyncSession, refresh_token: Optional[str], meta: RequestMeta) -> None:
    if not refresh_token:
        return
    session = await db.scalar(
        select(PlatformSession).where(PlatformSession.refresh_hash == hash_refresh_token(refresh_token)).with_for_update()
    )
    if session is None:
        return
    await revoke_session(db, session, "logout")
    await append_audit(db, action="auth.logout", admin_id=session.admin_id, target_type="session",
                       target_id=session.id, meta=meta)
    await db.commit()


# --- step-up -------------------------------------------------------------------------------------------------


async def step_up(
    db: AsyncSession, redis_client: aioredis.Redis, admin: PlatformAdmin, session: PlatformSession,
    payload: StepUpRequest, meta: RequestMeta,
) -> datetime:
    """Re-prove identity (password + fresh TOTP) before a sensitive action. Valid for PLATFORM_STEP_UP_MINUTES."""
    limiter = LoginRateLimiter(redis_client, scope="platform-stepup", key=str(admin.id))
    await limiter.check()
    locked = await db.scalar(select(PlatformAdmin).where(PlatformAdmin.id == admin.id).with_for_update())
    password_ok = verify_password(payload.password.get_secret_value(), locked.hashed_password)
    step = verify_totp(decrypt_secret(locked.totp_secret_enc), payload.code, locked.totp_last_step)
    if not password_ok or step is None:
        tripped = await limiter.record_failure()
        await append_audit(db, action="auth.step_up_failed", admin_id=admin.id, target_type="session",
                           target_id=session.id, meta=meta)
        if tripped:
            await raise_alert(db, "auth.lockout_step_up", AlertSeverity.CRITICAL,
                              f"Repeated failed re-authentication in a signed-in session of {admin.email}",
                              {"admin": admin.email, "ip": meta.ip})
        await db.commit()
        raise Forbidden("Re-authentication failed", code="step_up_failed")
    await limiter.reset()
    locked.totp_last_step = step
    session.step_up_at = _now()
    await append_audit(db, action="auth.step_up", admin_id=admin.id, target_type="session", target_id=session.id, meta=meta)
    await db.commit()
    return session.step_up_at
