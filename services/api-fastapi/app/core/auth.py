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
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import MANAGER_ROLES, ROLE_RANK, Role
from app.core.errors import ApprovalRequired, Forbidden, NotFound, Unauthorized
from app.core.security import DUMMY_HASH, compute_pin_lookup, decode_access_token, verify_pin
from app.models.staff import StaffUser

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    branch_id: Optional[uuid.UUID]
    role: Role
    username: str
    full_name: str
    auth_method: str  # "pwd" | "pin"

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGER_ROLES

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER


def token_claims(user: StaffUser, auth_method: str) -> Dict[str, Any]:
    return {
        "role": user.role,
        "branch_id": str(user.branch_id) if user.branch_id else None,
        "tv": user.token_version,
        "amr": auth_method,
    }


async def resolve_token(db: AsyncSession, token: Optional[str]) -> CurrentUser:
    """Validate a bearer token and load its active user. Shared by HTTP and WebSocket auth."""
    if not token:
        raise Unauthorized("Missing bearer token")
    payload = decode_access_token(token)
    if payload is None:
        raise Unauthorized("Invalid or expired token")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized("Invalid token subject") from exc

    user = await db.get(StaffUser, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise Unauthorized("Session is no longer valid")

    return CurrentUser(
        id=user.id,
        branch_id=user.branch_id,
        role=Role(user.role),
        username=user.username,
        full_name=user.full_name,
        auth_method=payload.get("amr", "pwd"),
    )


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    return await resolve_token(db, creds.credentials if creds else None)


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
    return user.is_owner or user.branch_id == branch_id


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
