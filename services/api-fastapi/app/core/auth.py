"""
Authentication dependencies and role-based access control.

Permission matrix (enforced with `require_roles` on each route):

| Action                                   | Roles                                              |
|------------------------------------------|----------------------------------------------------|
| Catalog / floor / branch writes, staff   | owner, manager                                     |
| 86 toggle                                | owner, manager, captain, kitchen                   |
| Create order, add items, fire KOT        | owner, manager, captain, waiter, cashier           |
| KOT status update                        | owner, manager, kitchen                            |
| KOT void, order/item cancel, bill void   | manager+ directly; others with a manager approver_pin |
| Bills and payments                       | owner, manager, cashier                            |
| Shifts                                   | own shift: cashier, waiter, captain, manager        |
| Analytics, audit log                     | owner, manager (password session)                  |
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, FrozenSet, Iterable, Optional

import redis.asyncio as aioredis
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, scope_session
from app.core.entitlements import SAFE_METHODS, enforce_access, enforce_module, get_entitlements
from app.core.enums import MANAGER_ROLES, ROLE_RANK, Role
from app.core.errors import ApprovalRequired, Forbidden, NotFound, Unauthorized
from app.core.redis import get_redis
from app.core.security import DUMMY_HASH, compute_pin_lookup, decode_access_token, verify_pin
from app.models.floor import Branch
from app.models.staff import StaffUser

bearer = HTTPBearer(auto_error=False)

# A device token proves a tablet was linked to a branch by a manager. It is not a user session:
# it carries no user, so it only unlocks the pre-login staff list.
DEVICE_TOKEN_TYPE = "device"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    branch_id: Optional[uuid.UUID]
    role: Role
    username: str
    full_name: str
    auth_method: str  # "pwd" | "pin"
    organization_id: uuid.UUID
    # Owners act across branches, but only those of their own organization.
    org_branch_ids: FrozenSet[uuid.UUID] = field(default_factory=frozenset)
    # Set when a platform operator is looking at this account (support impersonation): strictly read-only.
    impersonated_by: Optional[uuid.UUID] = None

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGER_ROLES

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER


def token_claims(user: StaffUser, auth_method: str) -> Dict[str, Any]:
    return {
        "org": str(user.organization_id),
        "role": user.role,
        "branch_id": str(user.branch_id) if user.branch_id else None,
        "tv": user.token_version,
        "amr": auth_method,
    }


def _impersonator(payload: Dict[str, Any]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(payload["imp"]) if payload.get("imp") else None
    except ValueError:
        return None


async def resolve_token(db: AsyncSession, token: Optional[str]) -> CurrentUser:
    """Validate a bearer token and load its active user. Shared by HTTP and WebSocket auth."""
    if not token:
        raise Unauthorized("Missing bearer token")
    payload = decode_access_token(token)
    if payload is None or payload.get("typ") == DEVICE_TOKEN_TYPE:
        raise Unauthorized("Invalid or expired token")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized("Invalid token subject") from exc

    user = await db.get(StaffUser, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise Unauthorized("Session is no longer valid")

    claimed_org = payload.get("org")
    if claimed_org is not None and claimed_org != str(user.organization_id):
        raise Unauthorized("Session is no longer valid")

    org_branch_ids: FrozenSet[uuid.UUID] = frozenset()
    if user.role == Role.OWNER.value:
        org_branch_ids = frozenset(
            await db.scalars(select(Branch.id).where(Branch.organization_id == user.organization_id))
        )

    return CurrentUser(
        id=user.id,
        organization_id=user.organization_id,
        org_branch_ids=org_branch_ids,
        branch_id=user.branch_id,
        role=Role(user.role),
        username=user.username,
        full_name=user.full_name,
        auth_method=payload.get("amr", "pwd"),
        impersonated_by=_impersonator(payload),
    )


async def require_device_branch(x_device_token: Optional[str] = Header(default=None)) -> uuid.UUID:
    """Branch of the linked device making this pre-login request (X-Device-Token)."""
    payload = decode_access_token(x_device_token) if x_device_token else None
    if payload is None or payload.get("typ") != DEVICE_TOKEN_TYPE:
        raise Unauthorized("This device is not linked to a branch", code="device_not_linked")
    try:
        return uuid.UUID(payload["branch_id"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized("This device is not linked to a branch", code="device_not_linked") from exc


async def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> CurrentUser:
    """Authenticate, then apply the organization's subscription state (suspended / read-only)."""
    user = await resolve_token(db, creds.credentials if creds else None)
    await scope_session(db, user.organization_id)  # database-level backstop: this request can only see its own organization
    if user.impersonated_by is not None and request.method.upper() not in SAFE_METHODS:
        raise Forbidden("Support sessions are read-only", code="impersonation_read_only")
    enforce_access(await get_entitlements(db, user.organization_id, redis_client), request.method)
    return user


def require_module(module_key: str) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """Dependency factory: the caller's organization must have `module_key` enabled."""

    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis_client: aioredis.Redis = Depends(get_redis),
    ) -> CurrentUser:
        enforce_module(await get_entitlements(db, user.organization_id, redis_client), str(module_key))
        return user

    return dependency


def module_flag(module_key: str) -> Callable[..., Coroutine[Any, Any, bool]]:
    """Dependency factory yielding whether the caller's organization has `module_key` (never raises)."""

    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis_client: aioredis.Redis = Depends(get_redis),
    ) -> bool:
        return (await get_entitlements(db, user.organization_id, redis_client)).has_module(str(module_key))

    return dependency


def require_roles_by_module(
    roles: Iterable[Role], *, module: str, also_without: Iterable[Role] = (), password_session: bool = False
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """
    `roles` may always act. The extra `also_without` roles may act only while the organization does NOT have `module`:
    e.g. waiters may take payment on a plan without the kitchen display, where nobody else is at the counter.
    """
    base, extra = set(roles), set(also_without)

    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis_client: aioredis.Redis = Depends(get_redis),
    ) -> CurrentUser:
        allowed = set(base)
        if extra and not (await get_entitlements(db, user.organization_id, redis_client)).has_module(str(module)):
            allowed |= extra
        if user.role not in allowed:
            raise Forbidden("Your role is not allowed to perform this action")
        if password_session and user.auth_method != "pwd":
            raise Forbidden("This action requires a password login", code="password_login_required")
        return user

    return dependency


def require_roles(*roles: Role, password_session: bool = False) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """Dependency factory: the current user must hold one of `roles`."""
    allowed = set(roles)

    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise Forbidden("Your role is not allowed to perform this action")
        if password_session and user.auth_method != "pwd":
            raise Forbidden("This action requires a password login", code="password_login_required")
        return user

    return dependency


def can_access_branch(user: CurrentUser, branch_id: uuid.UUID) -> bool:
    if user.is_owner:
        return branch_id in user.org_branch_ids
    return user.branch_id == branch_id


def require_branch_access(user: CurrentUser, branch_id: uuid.UUID, *, hide: bool = True) -> None:
    """Owners may act on any branch; everyone else only on their own. Reads raise 404 to avoid leaking existence."""
    if not can_access_branch(user, branch_id):
        if hide:
            raise NotFound("Resource not found")
        raise Forbidden("You do not have access to this branch")


def resolve_branch_id(user: CurrentUser, requested: Optional[uuid.UUID]) -> uuid.UUID:
    """Branch a request applies to: explicit (authorized) or the user's own."""
    if requested is not None:
        require_branch_access(user, requested, hide=False)
        return requested
    if user.branch_id is None:
        raise Forbidden("branch_id is required for users without a home branch", code="branch_required")
    return user.branch_id


def outranks(actor: CurrentUser, target_role: Role) -> bool:
    return actor.is_owner or ROLE_RANK[actor.role] > ROLE_RANK[target_role]


async def find_staff_by_pin(db: AsyncSession, branch_id: uuid.UUID, pin: str) -> Optional[StaffUser]:
    """Look up an active staff member by PIN within a branch, with constant-ish timing on misses."""
    lookup = compute_pin_lookup(branch_id, pin)
    user = await db.scalar(
        select(StaffUser).where(
            StaffUser.branch_id == branch_id,
            StaffUser.pin_lookup == lookup,
            StaffUser.is_active.is_(True),
        )
    )
    if user is None:
        verify_pin(pin, DUMMY_HASH)
        return None
    if not verify_pin(pin, user.hashed_pin):
        return None
    return user


async def resolve_approver(
    db: AsyncSession,
    user: CurrentUser,
    branch_id: uuid.UUID,
    approver_pin: Optional[str],
    redis_client: Any = None,
) -> uuid.UUID:
    """
    Return the id of the manager authorizing a sensitive action.
    Managers/owners approve their own actions; others must supply a manager's PIN for the branch.
    """
    if user.is_manager:
        return user.id
    if not approver_pin:
        raise ApprovalRequired("Manager approval is required for this action")

    from app.core.rate_limit import LoginRateLimiter  # local import avoids a cycle via redis

    limiter = LoginRateLimiter(redis_client, scope="approval", key=str(branch_id)) if redis_client else None
    if limiter:
        await limiter.check()
    approver = await find_staff_by_pin(db, branch_id, approver_pin)
    if approver is None or Role(approver.role) not in MANAGER_ROLES:
        if limiter:
            await limiter.record_failure()
        raise ApprovalRequired("Manager approval PIN is invalid")
    if limiter:
        await limiter.reset()
    return approver.id
