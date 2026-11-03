import uuid
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.enums import PlatformRole
from app.core.redis import get_redis
from app.modules.platform import admin_service, auth_service
from app.modules.platform.audit import verify_chain
from app.modules.platform.auth_service import IssuedTokens
from app.modules.platform.deps import (
    CSRF_COOKIE,
    REFRESH_COOKIE,
    PlatformPrincipal,
    check_csrf,
    get_platform_principal,
    request_meta,
    require_platform_roles,
    require_step_up,
)
from app.schemas.platform import (
    AuditVerifyResult,
    LoginRequest,
    MfaChallenge,
    PlatformAdminCreate,
    PlatformAdminCreated,
    PlatformAdminRead,
    PlatformAdminUpdate,
    PlatformAuditRead,
    PlatformSessionRead,
    PlatformTokenResponse,
    StepUpRequest,
    TwoFactorRequest,
)

SUPER = PlatformRole.SUPER_ADMIN
auth_router = APIRouter(prefix="/platform/auth", tags=["Platform: Authentication"])
router = APIRouter(prefix="/platform", tags=["Platform: Administration"])

COOKIE_PATH = f"{settings.API_V1_STR}/platform/auth"


def _set_cookies(response: Response, issued: IssuedTokens) -> None:
    # The refresh token is unreadable by scripts and only ever sent to the auth endpoints.
    response.set_cookie(REFRESH_COOKIE, issued.refresh_token, max_age=issued.refresh_max_age, httponly=True,
                        secure=settings.PLATFORM_COOKIE_SECURE, samesite="strict", path=COOKIE_PATH)
    # The CSRF token must be readable by the console's JavaScript to echo back in a header.
    response.set_cookie(CSRF_COOKIE, issued.csrf_token, max_age=issued.refresh_max_age, httponly=False,
                        secure=settings.PLATFORM_COOKIE_SECURE, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"


def _token_response(issued: IssuedTokens) -> PlatformTokenResponse:
    return PlatformTokenResponse(
        access_token=issued.access_token, expires_in=issued.expires_in, csrf_token=issued.csrf_token,
        admin=PlatformAdminRead.model_validate(issued.admin),
    )


@auth_router.post("/login", response_model=MfaChallenge)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db),
                redis_client: aioredis.Redis = Depends(get_redis)):
    """Step 1: email and password. Returns a short-lived challenge to complete with a TOTP or recovery code."""
    return await auth_service.login(db, redis_client, payload, request_meta(request))


@auth_router.post("/2fa", response_model=PlatformTokenResponse)
async def second_factor(payload: TwoFactorRequest, request: Request, response: Response,
                        db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Step 2: complete sign-in. Sets the refresh cookie and returns a short-lived access token."""
    issued = await auth_service.verify_second_factor(db, redis_client, payload, request_meta(request))
    _set_cookies(response, issued)
    return _token_response(issued)


@auth_router.post("/refresh", response_model=PlatformTokenResponse, dependencies=[Depends(check_csrf)])
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Rotate the refresh token and mint a new access token. Needs the CSRF header."""
    issued = await auth_service.refresh(db, request.cookies.get(REFRESH_COOKIE), request_meta(request))
    _set_cookies(response, issued)
    return _token_response(issued)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(check_csrf)])
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    await auth_service.logout(db, request.cookies.get(REFRESH_COOKIE), request_meta(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@auth_router.post("/step-up", status_code=status.HTTP_204_NO_CONTENT)
async def step_up(payload: StepUpRequest, request: Request, principal: PlatformPrincipal = Depends(get_platform_principal),
                  db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Re-enter password and a fresh TOTP code to unlock sensitive actions for a few minutes."""
    await auth_service.step_up(db, redis_client, principal.admin, principal.session, payload, request_meta(request))


@router.get("/me", response_model=PlatformAdminRead)
async def me(principal: PlatformPrincipal = Depends(get_platform_principal)):
    return principal.admin


@router.get("/admins", response_model=List[PlatformAdminRead], dependencies=[Depends(require_platform_roles(SUPER))])
async def list_admins(db: AsyncSession = Depends(get_db)):
    return await admin_service.list_admins(db)


@router.post("/admins", response_model=PlatformAdminCreated, status_code=status.HTTP_201_CREATED)
async def create_admin(payload: PlatformAdminCreate, request: Request,
                       actor: PlatformPrincipal = Depends(require_step_up(SUPER)), db: AsyncSession = Depends(get_db)):
    """Create an operator. Returns their TOTP secret and recovery codes once."""
    return await admin_service.create_admin(db, actor, payload, request_meta(request))


@router.patch("/admins/{admin_id}", response_model=PlatformAdminRead)
async def update_admin(admin_id: uuid.UUID, payload: PlatformAdminUpdate, request: Request,
                       actor: PlatformPrincipal = Depends(require_step_up(SUPER)), db: AsyncSession = Depends(get_db)):
    """Change role or deactivate. Ends that admin's sessions immediately."""
    return await admin_service.update_admin(db, actor, admin_id, payload, request_meta(request))


@router.get("/sessions", response_model=List[PlatformSessionRead])
async def list_sessions(actor: PlatformPrincipal = Depends(get_platform_principal), db: AsyncSession = Depends(get_db)):
    """Your active sessions (all sessions for super admins)."""
    return await admin_service.list_sessions(db, actor)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: uuid.UUID, request: Request,
                         actor: PlatformPrincipal = Depends(get_platform_principal), db: AsyncSession = Depends(get_db)):
    await admin_service.revoke_session_by_id(db, actor, session_id, request_meta(request))


@router.get("/audit-logs", response_model=List[PlatformAuditRead],
            dependencies=[Depends(require_platform_roles(SUPER, PlatformRole.READ_ONLY))])
async def audit_logs(limit: int = Query(50, ge=1, le=200), before_seq: Optional[int] = None,
                     action: Optional[str] = Query(None, max_length=60), db: AsyncSession = Depends(get_db)):
    return await admin_service.list_audit(db, limit, before_seq, action)


@router.get("/audit-logs/verify", response_model=AuditVerifyResult,
            dependencies=[Depends(require_platform_roles(SUPER))])
async def audit_verify(db: AsyncSession = Depends(get_db)):
    """Recompute the whole hash chain. Export `head_hash` regularly to storage the app cannot write."""
    return await verify_chain(db)

