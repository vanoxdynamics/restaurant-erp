"""FastAPI dependencies for platform endpoints: bearer auth bound to a live session, RBAC, step-up, CSRF."""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.enums import PlatformRole
from app.core.errors import Forbidden, Unauthorized
from app.core.platform_security import TOKEN_ACCESS, decode_platform_token
from app.models.platform import PlatformAdmin, PlatformSession
from app.modules.platform.audit import RequestMeta

bearer = HTTPBearer(auto_error=False)

REFRESH_COOKIE = "pa_refresh"
CSRF_COOKIE = "pa_csrf"
CSRF_HEADER = "x-csrf-token"
TOUCH_INTERVAL = timedelta(seconds=30)


@dataclass(frozen=True)
class PlatformPrincipal:
    admin: PlatformAdmin
    session: PlatformSession

    @property
    def role(self) -> PlatformRole:
        return PlatformRole(self.admin.role)


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta.from_request(request)


async def get_platform_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> PlatformPrincipal:
    claims = decode_platform_token(creds.credentials, TOKEN_ACCESS) if creds else None
    if claims is None:
        raise Unauthorized("Invalid or expired token")
    try:
        session = await db.get(PlatformSession, uuid.UUID(claims["sid"]))
        admin_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized("Invalid or expired token") from exc

    now = datetime.now(timezone.utc)
    if session is None or session.admin_id != admin_id or session.revoked_at is not None or now >= session.expires_at:
        raise Unauthorized("Session ended; sign in again", code="session_ended")
    if now - session.last_seen_at > timedelta(minutes=settings.PLATFORM_SESSION_IDLE_MINUTES):
        session.revoked_at, session.revoked_reason = now, "idle"
        await db.commit()
        raise Unauthorized("Session ended; sign in again", code="session_ended")

    admin = await db.get(PlatformAdmin, admin_id)
    if admin is None or not admin.is_active or admin.token_version != claims.get("tv"):
        raise Unauthorized("Session ended; sign in again", code="session_ended")

    if now - session.last_seen_at > TOUCH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    return PlatformPrincipal(admin=admin, session=session)


def require_platform_roles(*roles: PlatformRole) -> Callable[..., Coroutine[Any, Any, PlatformPrincipal]]:
    allowed = set(roles)

    async def dependency(principal: PlatformPrincipal = Depends(get_platform_principal)) -> PlatformPrincipal:
        if principal.role not in allowed:
            raise Forbidden("Your platform role is not allowed to perform this action")
        return principal

    return dependency


def assert_step_up(principal: PlatformPrincipal) -> None:
    """Raise unless this session re-authenticated (password + fresh TOTP) within PLATFORM_STEP_UP_MINUTES."""
    stepped = principal.session.step_up_at
    window = timedelta(minutes=settings.PLATFORM_STEP_UP_MINUTES)
    if stepped is None or datetime.now(timezone.utc) - stepped > window:
        raise Forbidden("Re-authenticate to continue", code="step_up_required",
                        details={"valid_for_minutes": settings.PLATFORM_STEP_UP_MINUTES})


def require_step_up(*roles: PlatformRole) -> Callable[..., Coroutine[Any, Any, PlatformPrincipal]]:
    """Role check plus a recent password+TOTP re-authentication on this session."""
    role_check = require_platform_roles(*roles)

    async def dependency(principal: PlatformPrincipal = Depends(role_check)) -> PlatformPrincipal:
        assert_step_up(principal)
        return principal

    return dependency


def check_csrf(request: Request) -> None:
    """Double-submit check for the endpoints that rely on the refresh cookie."""
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie or not secrets.compare_digest(header, cookie):
        raise Forbidden("Missing or invalid CSRF token", code="csrf_failed")
