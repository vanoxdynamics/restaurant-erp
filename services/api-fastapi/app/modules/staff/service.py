import secrets
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.auth import (
    DEVICE_TOKEN_TYPE,
    CurrentUser,
    find_staff_by_pin,
    outranks,
    require_branch_access,
    resolve_branch_id,
    token_claims,
)
from app.core.clock import business_date, utcnow
from app.core.config import settings
from app.core.enums import AuditAction, PaymentMode, PaymentStatus, Role, ShiftStatus
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized
from app.core.entitlements import compute_entitlements, enforce_limit
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
    verify_pin,
)
from app.models.billing import Bill, Payment
from app.models.devices import Device
from app.models.floor import Branch
from app.models.staff import Shift, StaffUser
from app.schemas.staff import (
    DeviceLinkRequest,
    DeviceSetupCode,
    DeviceSetupCodeCreate,
    DeviceTokenResponse,
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

    if payload.staff_id is not None:
        return await _login_staff_with_pin(db, redis_client, payload, limiter, device)

    user = await find_staff_by_pin(db, payload.branch_id, payload.pin)
    if user is None:
        await _record_failed_login(db, limiter, payload.branch_id, {"method": "pin", "device": device})
        raise Unauthorized("Invalid PIN")

    await limiter.reset()
    return _issue_token(user, "pin")


async def _login_staff_with_pin(
    db: AsyncSession, redis_client: aioredis.Redis, payload: PINAuthRequest,
    device_limiter: LoginRateLimiter, device: str,
) -> TokenResponse:
    """
    Check the PIN against one chosen person. The lockout follows that person, not the tablet, so
    switching device ids or tablets does not give an attacker a fresh set of guesses.
    """
    staff_limiter = LoginRateLimiter(redis_client, scope="pin-staff", key=str(payload.staff_id))
    await staff_limiter.check()

    user = await db.get(StaffUser, payload.staff_id)
    usable = (
        user is not None and user.is_active and user.branch_id == payload.branch_id and user.pin_lookup is not None
    )
    if not usable or not verify_pin(payload.pin, user.hashed_pin):
        if not usable:
            verify_pin(payload.pin, DUMMY_HASH)
        await device_limiter.record_failure()
        details = {"method": "pin", "device": device, "staff_id": str(payload.staff_id)}
        await _record_failed_login(db, staff_limiter, payload.branch_id, details)
        raise Unauthorized("Wrong PIN")

    await device_limiter.reset()
    await staff_limiter.reset()
    return _issue_token(user, "pin")


# Unambiguous characters only (no 0/O, 1/I/L) so a code can be read aloud or typed from a screen.
SETUP_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SETUP_CODE_TTL_SECONDS = 15 * 60


def _setup_code_key(code: str) -> str:
    return f"device-setup:{code.replace('-', '').upper()}"


async def create_device_setup_code(
    db: AsyncSession, redis_client: aioredis.Redis, actor: CurrentUser, payload: DeviceSetupCodeCreate
) -> DeviceSetupCode:
    """A manager makes a short-lived, single-use code that links one tablet to a branch."""
    branch_id = resolve_branch_id(actor, payload.branch_id)
    branch = await get_or_404(db, Branch, branch_id)
    raw = "".join(secrets.choice(SETUP_CODE_ALPHABET) for _ in range(8))
    await redis_client.set(_setup_code_key(raw), str(branch.id), ex=SETUP_CODE_TTL_SECONDS)
    return DeviceSetupCode(
        code=f"{raw[:4]}-{raw[4:]}", branch_id=branch.id, branch_name=branch.name, expires_in=SETUP_CODE_TTL_SECONDS
    )


async def link_device(
    db: AsyncSession, redis_client: aioredis.Redis, payload: DeviceLinkRequest, client_ip: str
) -> DeviceTokenResponse:
    """Exchange a setup code for a device token. Codes work once and guessing is rate-limited per IP."""
    limiter = LoginRateLimiter(redis_client, scope="device-setup", key=client_ip)
    await limiter.check()

    key = _setup_code_key(payload.code)
    branch_id = await redis_client.get(key)
    if branch_id is None:
        await limiter.record_failure()
        raise Unauthorized("That setup code is wrong or has expired", code="invalid_setup_code")

    branch = await get_or_404(db, Branch, uuid.UUID(branch_id))
    # The device id names the device's record. One already used by a revoked device or another branch cannot be linked
    # again; the code is left usable, so the device can try again with a fresh id.
    device = await db.scalar(select(Device).where(Device.device_id == payload.device_id))
    if device is not None and (device.revoked_at is not None or device.branch_id != branch.id):
        raise Conflict("This device id is already in use; link the device with a new id", code="device_id_taken")

    if await redis_client.getdel(key) != branch_id:  # atomic: one use only (another request may have just used it)
        await limiter.record_failure()
        raise Unauthorized("That setup code is wrong or has expired", code="invalid_setup_code")
    if device is None:
        await db.execute(
            insert(Device).values(id=uuid.uuid4(), branch_id=branch.id, device_id=payload.device_id)
            .on_conflict_do_nothing(index_elements=[Device.device_id])
        )
        device = await db.scalar(
            select(Device).where(Device.device_id == payload.device_id).execution_options(populate_existing=True))
        if device is None or device.revoked_at is not None or device.branch_id != branch.id:
            await db.rollback()
            raise Conflict("This device id is already in use; link the device with a new id", code="device_id_taken")
        await commit(db)

    token = create_access_token(
        payload.device_id,
        claims={"typ": DEVICE_TOKEN_TYPE, "branch_id": str(branch.id)},
        expires_delta=timedelta(days=365),
    )
    return DeviceTokenResponse(device_token=token, branch_id=branch.id, branch_name=branch.name)


async def staff_roster(db: AsyncSession, branch_id: uuid.UUID) -> List[StaffUser]:
    """Active staff who can sign in with a PIN at this branch, for the 'who is working' screen."""
    stmt = (
        select(StaffUser)
        .where(
            StaffUser.branch_id == branch_id,
            StaffUser.is_active.is_(True),
            StaffUser.pin_lookup.is_not(None),
        )
        .order_by(func.lower(StaffUser.full_name))
    )
    return list((await db.scalars(stmt)).all())


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


async def _ensure_branch(db: AsyncSession, actor: CurrentUser, branch_id: Optional[uuid.UUID]) -> None:
    if branch_id is None:
        return
    branch = await db.get(Branch, branch_id)
    if branch is None or branch.organization_id != actor.organization_id:
        raise NotFound("Branch not found")


def _map_staff_integrity(exc: IntegrityError) -> Conflict:
    text = str(exc.orig)
    if "pin_lookup" in text:
        return Conflict("PIN already in use at this branch; choose another", code="pin_taken")
    if "email" in text:
        return Conflict("Email already registered", code="email_taken")
    return Conflict("Username already taken", code="username_taken")


def _require_kitchen_display_for(role: Role, ent) -> None:
    """Kitchen accounts exist to use the kitchen screen; a plan without it has no use for them."""
    if role == Role.KITCHEN and not ent.has_module("kds"):
        raise Forbidden("The kitchen display is not part of your plan, so kitchen accounts cannot be created",
                        code="module_disabled", details={"module": "kds"})


async def create_staff(db: AsyncSession, actor: CurrentUser, payload: StaffUserCreate) -> StaffUser:
    branch_id = payload.branch_id if payload.branch_id is not None else actor.branch_id
    _check_can_manage(actor, payload.role, branch_id)
    if branch_id is None and payload.role != Role.OWNER:
        raise Forbidden("Only owners may exist without a branch", code="branch_required")
    await _ensure_branch(db, actor, branch_id)

    ent = await compute_entitlements(db, actor.organization_id)
    _require_kitchen_display_for(payload.role, ent)
    active_staff = await db.scalar(
        select(func.count()).select_from(StaffUser).where(
            StaffUser.organization_id == actor.organization_id, StaffUser.is_active.is_(True))
    )
    if payload.is_active:
        enforce_limit(active_staff or 0, ent.max_staff, "staff accounts")

    user = StaffUser(
        organization_id=actor.organization_id,
        branch_id=branch_id,
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role.value,
        is_active=payload.is_active,
        hashed_pin=get_pin_hash(payload.pin),
        pin_length=len(payload.pin),
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
    stmt = select(StaffUser).where(StaffUser.organization_id == actor.organization_id).order_by(StaffUser.full_name)
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
    if user.organization_id != actor.organization_id:
        raise NotFound("StaffUser not found")
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
    if "role" in changes and new_role == Role.KITCHEN and user.role != Role.KITCHEN.value:
        _require_kitchen_display_for(new_role, await compute_entitlements(db, actor.organization_id))
    if "branch_id" in changes and changes["branch_id"] != user.branch_id:
        await _ensure_branch(db, actor, new_branch)
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
    user.pin_length = len(pin)
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


async def delete_staff(db: AsyncSession, actor: CurrentUser, staff_id: uuid.UUID) -> None:
    """
    Remove a staff member who has no history. Anyone who has taken orders, payments or shifts stays on record
    (bills and the audit trail point at them), so those accounts get a 409 and should be deactivated instead.
    """
    user = await _load_managed_staff(db, actor, staff_id)
    if user.id == actor.id:
        raise Forbidden("You cannot delete your own account", code="cannot_delete_self")
    if Role(user.role) == Role.OWNER:
        owners = await db.scalar(
            select(func.count()).select_from(StaffUser).where(
                StaffUser.organization_id == actor.organization_id, StaffUser.role == Role.OWNER.value)
        )
        if (owners or 0) <= 1:
            raise Forbidden("The last owner cannot be deleted", code="last_owner")
    try:
        await db.delete(user)
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("This person has orders, payments or shifts on record, so they cannot be deleted. "
                       "Deactivate them instead.", code="staff_has_history") from exc
    await commit(db)


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


async def _close(db: AsyncSession, actor: CurrentUser, shift: Shift, blind_drop_cash: Decimal,
                 cash_taken: Optional[Decimal] = None) -> Shift:
    if cash_taken is None:  # a personal shift: the cash its own payments took
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


# --- Cash day (one per branch) ------------------------------------------------------------------------------------------------
#
# The counter opens the day with the cash in the drawer and closes it with a blind count. Unlike a personal shift it belongs to
# the branch: whoever is at the counter opens or closes it, and the expected cash is every cash payment taken at the branch
# since it opened, whoever took it. It is stored as a shift row (opened by `user_id`), so shift history keeps working.


async def current_cash_day(db: AsyncSession, branch_id: uuid.UUID, *, for_update: bool = False) -> Optional[Shift]:
    stmt = (select(Shift).where(Shift.branch_id == branch_id, Shift.status == ShiftStatus.OPEN.value)
            .order_by(Shift.start_time).limit(1))
    if for_update:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


async def cash_day_view(db: AsyncSession, actor: CurrentUser, branch: Branch, shift: Optional[Shift]) -> dict:
    today = business_date(branch.timezone)
    if shift is None:
        return {"status": "not_open", "business_date": today}
    opener = await db.get(StaffUser, shift.user_id)
    opened_on = shift.start_time.astimezone(ZoneInfo(branch.timezone)).date()
    view = {
        "id": shift.id, "status": shift.status, "business_date": today, "opened_at": shift.start_time,
        "opened_on": opened_on, "opened_by": opener.full_name if opener else None, "opening_cash": shift.opening_cash,
        "closed_at": shift.end_time, "counted_cash": shift.blind_drop_cash,
        # Left open from an earlier day: the app reminds the counter to close it.
        "stale": shift.status == ShiftStatus.OPEN.value and opened_on < today,
    }
    if actor.is_manager:  # the count stays blind for everyone else
        view.update(expected_cash=shift.expected_cash, cash_variance=shift.cash_variance)
    return view


async def open_cash_day(db: AsyncSession, actor: CurrentUser, branch_id: uuid.UUID, opening_cash: Decimal) -> Shift:
    await get_or_404(db, Branch, branch_id, for_update=True)  # serialises two counters opening at once
    if await current_cash_day(db, branch_id) is not None:
        raise Conflict("The day is already open", code="day_already_open")
    shift = Shift(user_id=actor.id, branch_id=branch_id, start_time=utcnow(), opening_cash=opening_cash,
                  status=ShiftStatus.OPEN.value)
    db.add(shift)
    try:
        await db.flush()
    except IntegrityError as exc:  # this person still has an old personal shift open
        await db.rollback()
        raise Conflict("Close your own open shift first", code="shift_already_open") from exc
    await commit(db)
    return shift


async def close_cash_day(db: AsyncSession, actor: CurrentUser, branch_id: uuid.UUID, counted_cash: Decimal) -> Shift:
    shift = await current_cash_day(db, branch_id, for_update=True)
    if shift is None:
        raise Conflict("The day is not open", code="day_not_open")
    cash_taken = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .join(Bill, Bill.id == Payment.bill_id)
        .where(
            Bill.branch_id == branch_id,
            Payment.payment_mode == PaymentMode.CASH.value,
            Payment.status == PaymentStatus.SUCCESS.value,
            Payment.created_at >= shift.start_time,
        )
    )
    return await _close(db, actor, shift, counted_cash, Decimal(cash_taken or 0))
