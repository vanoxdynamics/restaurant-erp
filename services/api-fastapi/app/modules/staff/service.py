import uuid
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.auth import CurrentUser, find_staff_by_pin, outranks, require_branch_access, token_claims
from app.core.clock import utcnow
from app.core.config import settings
from app.core.enums import AuditAction, PaymentMode, PaymentStatus, Role, ShiftStatus
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized
from app.core.events import commit
from app.core.lookups import get_or_404
from app.core.pagination import PageParams, paginate
from app.core.rate_limit import LoginRateLimiter
from app.core.security import (
    DUMMY_HASH,
    compute_pin_lookup,
    create_access_token,
    get_password_hash,
    get_pin_hash,
    verify_and_update_password,
    verify_password,
)
from app.models.billing import Payment
from app.models.floor import Branch
from app.models.staff import Shift, StaffUser
from app.schemas.staff import (
    PasswordLoginRequest,
    PINAuthRequest,
    StaffUserCreate,
    StaffUserUpdate,
    TokenResponse,
)


def _issue_token(user: StaffUser, auth_method: str) -> TokenResponse:
    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(user.id, claims=token_claims(user, auth_method), expires_delta=expires)
    return TokenResponse(
        access_token=token,
        expires_in=int(expires.total_seconds()),
        role=Role(user.role),
        user_id=user.id,
        branch_id=user.branch_id,
        username=user.username,
        full_name=user.full_name,
    )


async def _record_failed_login(
    db: AsyncSession, limiter: LoginRateLimiter, branch_id: Optional[uuid.UUID], details: dict
) -> None:
    tripped = await limiter.record_failure()
    if tripped and branch_id is not None:
        record_audit(
            db, branch_id=branch_id, action=AuditAction.LOGIN_LOCKOUT, actor_id=None,
            entity_type="login", details=details,
        )
        await commit(db)


async def login_with_pin(
    db: AsyncSession, redis_client: aioredis.Redis, payload: PINAuthRequest, client_ip: str
) -> TokenResponse:
    device = payload.device_id or client_ip
    limiter = LoginRateLimiter(redis_client, scope="pin", key=f"{payload.branch_id}:{device}")
    await limiter.check()

    user = await find_staff_by_pin(db, payload.branch_id, payload.pin)
    if user is None:
        await _record_failed_login(db, limiter, payload.branch_id, {"method": "pin", "device": device})
        raise Unauthorized("Invalid PIN")

    await limiter.reset()
    return _issue_token(user, "pin")


async def login_with_password(
    db: AsyncSession, redis_client: aioredis.Redis, payload: PasswordLoginRequest, client_ip: str
) -> TokenResponse:
    user_limiter = LoginRateLimiter(redis_client, scope="pwd-user", key=payload.username)
    ip_limiter = LoginRateLimiter(redis_client, scope="pwd-ip", key=client_ip)
    await user_limiter.check()
    await ip_limiter.check()

    password = payload.password.get_secret_value()
    user = await db.scalar(
        select(StaffUser).where(StaffUser.username == payload.username, StaffUser.is_active.is_(True))
    )
    if user is None or not user.hashed_password:
        verify_password(password, DUMMY_HASH)
        await ip_limiter.record_failure()
        await _record_failed_login(db, user_limiter, user.branch_id if user else None,
                                   {"method": "password", "username": payload.username})
        raise Unauthorized("Invalid username or password")

    ok, new_hash = verify_and_update_password(password, user.hashed_password)
    if not ok:
        await ip_limiter.record_failure()
        await _record_failed_login(db, user_limiter, user.branch_id,
                                   {"method": "password", "username": payload.username})
        raise Unauthorized("Invalid username or password")

    if new_hash:
        user.hashed_password = new_hash
        await commit(db)
    await user_limiter.reset()
    return _issue_token(user, "pwd")


def _check_can_manage(actor: CurrentUser, role: Role, branch_id: Optional[uuid.UUID]) -> None:
    if not actor.is_owner:
        if branch_id is None or branch_id != actor.branch_id:
            raise Forbidden("Managers can only manage staff in their own branch")
        if not outranks(actor, role):
            raise Forbidden("You cannot assign a role at or above your own")


async def _ensure_branch(db: AsyncSession, branch_id: Optional[uuid.UUID]) -> None:
    if branch_id is not None and await db.get(Branch, branch_id) is None:
        raise NotFound("Branch not found")


def _map_staff_integrity(exc: IntegrityError) -> Conflict:
    text = str(exc.orig)
    if "pin_lookup" in text:
        return Conflict("PIN already in use at this branch; choose another", code="pin_taken")
    if "email" in text:
        return Conflict("Email already registered", code="email_taken")
    return Conflict("Username already taken", code="username_taken")


async def create_staff(db: AsyncSession, actor: CurrentUser, payload: StaffUserCreate) -> StaffUser:
    branch_id = payload.branch_id if payload.branch_id is not None else actor.branch_id
    _check_can_manage(actor, payload.role, branch_id)
    if branch_id is None and payload.role != Role.OWNER:
        raise Forbidden("Only owners may exist without a branch", code="branch_required")
    await _ensure_branch(db, branch_id)

    user = StaffUser(
        branch_id=branch_id,
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role.value,
        is_active=payload.is_active,
        hashed_pin=get_pin_hash(payload.pin),
        pin_lookup=compute_pin_lookup(branch_id, payload.pin) if branch_id else None,
        hashed_password=get_password_hash(payload.password.get_secret_value()) if payload.password else None,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _map_staff_integrity(exc) from exc
    await commit(db)
    return user


async def list_staff(
    db: AsyncSession, actor: CurrentUser, params: PageParams,
    role: Optional[Role] = None, is_active: Optional[bool] = None, branch_id: Optional[uuid.UUID] = None,
) -> Tuple[List[StaffUser], int]:
    stmt = select(StaffUser).order_by(StaffUser.full_name)
    if not actor.is_owner:
        stmt = stmt.where(StaffUser.branch_id == actor.branch_id)
    elif branch_id is not None:
        stmt = stmt.where(StaffUser.branch_id == branch_id)
    if role is not None:
        stmt = stmt.where(StaffUser.role == role.value)
    if is_active is not None:
        stmt = stmt.where(StaffUser.is_active.is_(is_active))
    return await paginate(db, stmt, params)


async def _load_managed_staff(db: AsyncSession, actor: CurrentUser, staff_id: uuid.UUID) -> StaffUser:
    user = await get_or_404(db, StaffUser, staff_id)
    if not actor.is_owner:
        if user.branch_id != actor.branch_id:
            raise NotFound("StaffUser not found")
        if not outranks(actor, Role(user.role)):
            raise Forbidden("You cannot manage a user at or above your own role")
    return user


async def update_staff(db: AsyncSession, actor: CurrentUser, staff_id: uuid.UUID, payload: StaffUserUpdate) -> StaffUser:
    user = await _load_managed_staff(db, actor, staff_id)
    changes = payload.model_dump(exclude_unset=True)

    new_role = Role(changes.get("role", user.role))
    new_branch = changes.get("branch_id", user.branch_id)
    _check_can_manage(actor, new_role, new_branch)
    if "branch_id" in changes and changes["branch_id"] != user.branch_id:
        await _ensure_branch(db, new_branch)
        # PIN lookup is branch-scoped; the user must set a new PIN for the new branch.
        user.pin_lookup = None

    for field, value in changes.items():
        setattr(user, field, value.value if isinstance(value, Role) else value)
    if changes.get("is_active") is False or "role" in changes or "branch_id" in changes:
        user.token_version += 1

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _map_staff_integrity(exc) from exc
    await commit(db)
    return user


async def reset_pin(db: AsyncSession, actor: CurrentUser, staff_id: uuid.UUID, pin: str) -> StaffUser:
    user = await _load_managed_staff(db, actor, staff_id)
    user.hashed_pin = get_pin_hash(pin)
    user.pin_lookup = compute_pin_lookup(user.branch_id, pin) if user.branch_id else None
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _map_staff_integrity(exc) from exc
    await commit(db)
    return user


async def revoke_sessions(db: AsyncSession, actor: CurrentUser, staff_id: uuid.UUID) -> StaffUser:
    user = await _load_managed_staff(db, actor, staff_id)
    user.token_version += 1
    await commit(db)
    return user


# --- Shifts -----------------------------------------------------------------


async def get_open_shift(db: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> Optional[Shift]:
    stmt = select(Shift).where(Shift.user_id == user_id, Shift.status == ShiftStatus.OPEN.value)
    if for_update:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


async def open_shift(db: AsyncSession, actor: CurrentUser, opening_cash: Decimal) -> Shift:
    if actor.branch_id is None:
        raise Forbidden("Shifts require a home branch", code="branch_required")
    shift = Shift(
        user_id=actor.id,
        branch_id=actor.branch_id,
        start_time=utcnow(),
        opening_cash=opening_cash,
        status=ShiftStatus.OPEN.value,
    )
    db.add(shift)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("You already have an open shift", code="shift_already_open") from exc
    await commit(db)
    return shift


async def _close(db: AsyncSession, actor: CurrentUser, shift: Shift, blind_drop_cash: Decimal) -> Shift:
    cash_taken = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.shift_id == shift.id,
            Payment.payment_mode == PaymentMode.CASH.value,
            Payment.status == PaymentStatus.SUCCESS.value,
        )
    )
    expected = shift.opening_cash + Decimal(cash_taken or 0)
    shift.end_time = utcnow()
    shift.blind_drop_cash = blind_drop_cash
    shift.closing_cash = blind_drop_cash
    shift.expected_cash = expected
    shift.cash_variance = blind_drop_cash - expected
    shift.status = ShiftStatus.CLOSED.value

    if abs(shift.cash_variance) > settings.CASH_VARIANCE_ALERT_THRESHOLD:
        record_audit(
            db, branch_id=shift.branch_id, action=AuditAction.CASH_VARIANCE, actor_id=actor.id,
            entity_type="shift", entity_id=shift.id, amount=shift.cash_variance,
            details={"shift_user_id": shift.user_id, "expected": expected, "dropped": blind_drop_cash},
        )
    await commit(db)
    return shift


async def close_own_shift(db: AsyncSession, actor: CurrentUser, blind_drop_cash: Decimal) -> Shift:
    shift = await get_open_shift(db, actor.id, for_update=True)
    if shift is None:
        raise NotFound("You have no open shift")
    return await _close(db, actor, shift, blind_drop_cash)


async def close_shift_by_id(db: AsyncSession, actor: CurrentUser, shift_id: uuid.UUID, blind_drop_cash: Decimal) -> Shift:
    shift = await get_or_404(db, Shift, shift_id, for_update=True)
    require_branch_access(actor, shift.branch_id)
    if shift.status != ShiftStatus.OPEN.value:
        raise Conflict("Shift is already closed", code="shift_closed")
    return await _close(db, actor, shift, blind_drop_cash)


async def list_shifts(
    db: AsyncSession,
    branch_id: uuid.UUID,
    params: PageParams,
    status: Optional[ShiftStatus] = None,
    user_id: Optional[uuid.UUID] = None,
) -> Tuple[List[dict], int]:
    """Shift history for a branch, newest first, with the staff member's name."""
    conditions = [Shift.branch_id == branch_id]
    if status is not None:
        conditions.append(Shift.status == status.value)
    if user_id is not None:
        conditions.append(Shift.user_id == user_id)

    total = await db.scalar(select(func.count(Shift.id)).where(*conditions)) or 0
    rows = (await db.execute(
        select(Shift, StaffUser.full_name)
        .join(StaffUser, Shift.user_id == StaffUser.id)
        .where(*conditions)
        .order_by(Shift.start_time.desc())
        .limit(params.limit)
        .offset(params.offset)
    )).all()

    items = []
    for shift, name in rows:
        cash_sales = shift.expected_cash - shift.opening_cash if shift.expected_cash is not None else None
        items.append({
            **{c: getattr(shift, c) for c in (
                "id", "user_id", "branch_id", "start_time", "end_time", "opening_cash", "closing_cash",
                "blind_drop_cash", "expected_cash", "cash_variance", "status", "created_at",
            )},
            "user_name": name,
            "cash_sales": cash_sales,
        })
    return items, int(total)
