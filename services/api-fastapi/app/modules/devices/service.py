"""
Devices as records (offline billing, phase 1): list, name, switch offline on or off, revoke, and the offline lease.

A device's record is made when it is linked (or, for a device linked before records existed, on its first request; see
`app.core.auth.load_device`). Revoking is final: the device's token stops working on every device path, and the device is
linked again with a new setup code, which gives it a new record.
"""

import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_branch_access, resolve_branch_id
from app.core.clock import utcnow
from app.core.database import scope_session
from app.core.entitlements import FULL, enforce_module, get_entitlements
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.core.events import commit
from app.core.modules import ModuleKey
from app.core.security import OFFLINE_LEASE_TYPE, create_access_token, decode_offline_lease
from app.models.devices import Device
from app.models.floor import Branch
from app.models.organization import Organization
from app.schemas.devices import DeviceUpdate, OfflineLeaseRead

# A device's bill number is {CODE}{LETTER}/{FY}/{00001}: len(CODE) + 12 characters, and GST allows at most 16.
MAX_OFFLINE_BRANCH_CODE = 4
SERIES_LETTERS = string.ascii_uppercase


async def list_devices(
    db: AsyncSession, user: CurrentUser, branch_id: Optional[uuid.UUID], include_revoked: bool
) -> List[Device]:
    branch_id = resolve_branch_id(user, branch_id)
    stmt = select(Device).where(Device.branch_id == branch_id)
    if not include_revoked:
        stmt = stmt.where(Device.revoked_at.is_(None))
    return list((await db.scalars(stmt.order_by(Device.created_at.desc(), Device.id))).all())


async def _device_for_change(db: AsyncSession, user: CurrentUser, device_id: uuid.UUID) -> Device:
    device = await db.scalar(
        select(Device).where(Device.id == device_id).with_for_update().execution_options(populate_existing=True))
    if device is None:
        raise NotFound("Device not found")
    require_branch_access(user, device.branch_id)  # another branch or business: 404, never 403
    return device


async def _give_series_letter(db: AsyncSession, branch_id: uuid.UUID) -> str:
    """The first letter no device of the branch has ever had, revoked devices included. The branch row is locked meanwhile."""
    await db.scalar(select(Branch.id).where(Branch.id == branch_id).with_for_update())
    taken = set((await db.scalars(
        select(Device.series_letter).where(Device.branch_id == branch_id, Device.series_letter.is_not(None)))).all())
    for letter in SERIES_LETTERS:
        if letter not in taken:
            return letter
    raise Conflict("Every bill series letter of this branch has been given out", code="no_series_left")


async def _enable_offline(db: AsyncSession, redis_client: aioredis.Redis, user: CurrentUser, device: Device) -> None:
    if device.revoked_at is not None:
        raise Conflict("This device has been revoked", code="device_revoked")
    enforce_module(await get_entitlements(db, user.organization_id, redis_client), ModuleKey.OFFLINE_BILLING.value)
    branch = await db.get(Branch, device.branch_id)
    if branch is None:
        raise NotFound("Device not found")
    if len(branch.code) > MAX_OFFLINE_BRANCH_CODE:
        raise ValidationFailed(
            f"Offline bill numbers need a branch code of at most {MAX_OFFLINE_BRANCH_CODE} characters",
            code="branch_code_too_long", details={"branch_code": branch.code, "max_length": MAX_OFFLINE_BRANCH_CODE},
        )
    if device.series_letter is None:  # given once, never changed or cleared afterwards
        device.series_letter = await _give_series_letter(db, device.branch_id)
    device.offline_enabled = True


async def update_device(
    db: AsyncSession, redis_client: aioredis.Redis, user: CurrentUser, device_id: uuid.UUID, payload: DeviceUpdate
) -> Device:
    device = await _device_for_change(db, user, device_id)
    fields = payload.model_fields_set
    if "name" in fields:
        device.name = payload.name
    if "offline_enabled" in fields and payload.offline_enabled != device.offline_enabled:
        if payload.offline_enabled:
            await _enable_offline(db, redis_client, user, device)
        else:
            device.offline_enabled = False  # the letter stays with the device
    await commit(db)
    return device


async def revoke_device(db: AsyncSession, user: CurrentUser, device_id: uuid.UUID) -> Device:
    device = await _device_for_change(db, user, device_id)
    if device.revoked_at is None:
        device.revoked_at = utcnow()
        device.revoked_by_id = user.staff_id
        device.offline_enabled = False
        await commit(db)
    return device


def _refused(reason: str, message: str) -> Forbidden:
    return Forbidden(message, code="offline_not_allowed", details={"reason": reason})


async def issue_lease(db: AsyncSession, redis_client: aioredis.Redis, device: Device) -> OfflineLeaseRead:
    """
    A fresh offline lease for `device`, when all three switches are on and the business has full access. Nothing is stored:
    the lease is signed, and simply not renewing it is what makes a switch, a lapse or a suspension reach a device offline.
    """
    branch = await db.get(Branch, device.branch_id)
    if branch is None or not branch.is_active:
        raise Unauthorized("This device is not linked to a branch", code="device_not_linked")
    await scope_session(db, branch.organization_id)

    ent = await get_entitlements(db, branch.organization_id, redis_client)
    if ent.access != FULL:
        raise _refused("subscription_inactive", "The subscription is not active")
    if not ent.has_module(ModuleKey.OFFLINE_BILLING.value):
        raise _refused("module_disabled", "Offline billing is not part of this subscription")
    org = await db.get(Organization, branch.organization_id)
    if org is None or not org.offline_allowed:
        raise _refused("business_disabled", "The business has not allowed offline billing")
    if not device.offline_enabled or device.series_letter is None:
        raise _refused("device_disabled", "Offline billing is not enabled on this device")

    lease = create_access_token(
        device.device_id,
        claims={
            "typ": OFFLINE_LEASE_TYPE,
            "branch_id": str(device.branch_id),
            "org": str(org.id),
            "series": device.series_letter,
            "max_sales": org.offline_max_sales,
            "max_cash": str(org.offline_max_cash),
        },
        expires_delta=timedelta(hours=org.offline_max_hours),
    )
    claims = decode_offline_lease(lease) or {}
    return OfflineLeaseRead(
        lease=lease,
        device_id=device.device_id,
        branch_id=device.branch_id,
        series_letter=device.series_letter,
        issued_at=datetime.fromtimestamp(claims["iat"], tz=timezone.utc),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
        max_sales=org.offline_max_sales,
        max_cash=org.offline_max_cash,
        server_time=utcnow(),
    )
