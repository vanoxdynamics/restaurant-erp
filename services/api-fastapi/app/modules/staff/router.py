import uuid
from typing import List, Optional, Union

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_device_branch, require_roles, resolve_branch_id
from app.core.database import get_db
from app.core.enums import Role, ShiftStatus
from app.core.errors import NotFound
from app.core.pagination import PageParams, page_params
from app.core.redis import get_redis
from app.models.staff import Shift, StaffUser
from app.modules.staff import service
from app.schemas.common import Page
from app.schemas.staff import (
    DeviceLinkRequest,
    DeviceSetupCode,
    DeviceSetupCodeCreate,
    DeviceTokenResponse,
    PINAuthRequest,
    PasswordLoginRequest,
    PinResetRequest,
    RosterEntry,
    ShiftCloseRequest,
    ShiftHistoryRead,
    ShiftOpenRequest,
    ShiftRead,
    ShiftReadManager,
    StaffUserCreate,
    StaffUserRead,
    StaffUserUpdate,
    TokenResponse,
)

router = APIRouter(prefix="/staff", tags=["Staff, RBAC & Shifts"])

managers = require_roles(Role.OWNER, Role.MANAGER, password_session=True)
shift_roles = require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER, Role.CAPTAIN, Role.WAITER)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _shift_view(user: CurrentUser, shift: Shift) -> Union[ShiftRead, ShiftReadManager]:
    # Cashiers never see expected cash: keeps the drop blind.
    if user.is_manager:
        return ShiftReadManager.model_validate(shift)
    return ShiftRead.model_validate(shift)


@router.post("/auth/pin", response_model=TokenResponse)
async def login_with_pin(
    payload: PINAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Fast PIN-based authentication for captain handhelds and POS terminals."""
    return await service.login_with_pin(db, redis_client, payload, _client_ip(request))


@router.get("/auth/roster", response_model=List[RosterEntry])
async def sign_in_roster(
    branch_id: uuid.UUID = Depends(require_device_branch),
    db: AsyncSession = Depends(get_db),
):
    """Names and roles for the POS 'who is working' screen. Needs the X-Device-Token a manager issued."""
    return await service.staff_roster(db, branch_id)


@router.post("/devices/setup-codes", response_model=DeviceSetupCode, status_code=status.HTTP_201_CREATED)
async def create_device_setup_code(
    payload: DeviceSetupCodeCreate,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Make a one-time code (valid 15 minutes) that links a new tablet to a branch."""
    return await service.create_device_setup_code(db, redis_client, user, payload)


@router.post("/devices/link", response_model=DeviceTokenResponse)
async def link_device(
    payload: DeviceLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """A new tablet trades its setup code for a device token. No sign-in: the code is the proof."""
    return await service.link_device(db, redis_client, payload, _client_ip(request))


@router.post("/auth/login", response_model=TokenResponse)
async def login_with_password(
    payload: PasswordLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Standard username/password authentication for managers and owners."""
    return await service.login_with_password(db, redis_client, payload, _client_ip(request))


@router.get("/me", response_model=StaffUserRead)
async def get_me(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Current user profile."""
    return await db.get(StaffUser, user.id)


@router.get("", response_model=Page[StaffUserRead])
async def list_staff(
    role: Optional[Role] = None,
    is_active: Optional[bool] = None,
    branch_id: Optional[uuid.UUID] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """List staff members and their RBAC roles."""
    items, total = await service.list_staff(db, user, params, role, is_active, branch_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("", response_model=StaffUserRead, status_code=status.HTTP_201_CREATED)
async def create_staff(
    payload: StaffUserCreate,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Register a new staff member."""
    return await service.create_staff(db, user, payload)


@router.patch("/{staff_id}", response_model=StaffUserRead)
async def update_staff(
    staff_id: uuid.UUID,
    payload: StaffUserUpdate,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Update a staff member's profile, role, branch or active flag."""
    return await service.update_staff(db, user, staff_id, payload)


@router.post("/{staff_id}/reset-pin", response_model=StaffUserRead)
async def reset_pin(
    staff_id: uuid.UUID,
    payload: PinResetRequest,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Set a new login PIN for a staff member."""
    return await service.reset_pin(db, user, staff_id, payload.pin)


@router.post("/{staff_id}/revoke-sessions", response_model=StaffUserRead)
async def revoke_sessions(
    staff_id: uuid.UUID,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate every issued token for a staff member."""
    return await service.revoke_sessions(db, user, staff_id)


@router.get("/shifts", response_model=Page[ShiftHistoryRead])
async def list_shifts(
    branch_id: Optional[uuid.UUID] = None,
    status: Optional[ShiftStatus] = None,
    user_id: Optional[uuid.UUID] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Shift history with cash reconciliation, newest first."""
    items, total = await service.list_shifts(db, resolve_branch_id(user, branch_id), params, status, user_id)
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post("/shifts/open", response_model=ShiftRead, status_code=status.HTTP_201_CREATED)
async def open_shift(
    payload: ShiftOpenRequest,
    user: CurrentUser = Depends(shift_roles),
    db: AsyncSession = Depends(get_db),
):
    """Open a new cashier/waiter cash register shift."""
    return await service.open_shift(db, user, payload.opening_cash)


@router.get("/shifts/current", response_model=Union[ShiftReadManager, ShiftRead])
async def current_shift(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The caller's open shift."""
    shift = await service.get_open_shift(db, user.id)
    if shift is None:
        raise NotFound("You have no open shift")
    return _shift_view(user, shift)


@router.post("/shifts/close", response_model=Union[ShiftReadManager, ShiftRead])
async def close_shift(
    payload: ShiftCloseRequest,
    user: CurrentUser = Depends(shift_roles),
    db: AsyncSession = Depends(get_db),
):
    """Close the caller's shift with blind drop cash reconciliation."""
    shift = await service.close_own_shift(db, user, payload.blind_drop_cash)
    return _shift_view(user, shift)


@router.post("/shifts/{shift_id}/close", response_model=ShiftReadManager)
async def close_shift_for_user(
    shift_id: uuid.UUID,
    payload: ShiftCloseRequest,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """Manager closes another staff member's shift."""
    shift = await service.close_shift_by_id(db, user, shift_id, payload.blind_drop_cash)
    return ShiftReadManager.model_validate(shift)
