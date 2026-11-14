import uuid
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_device, require_roles
from app.core.database import get_db
from app.core.enums import Role
from app.core.redis import get_redis
from app.models.devices import Device
from app.modules.devices import service
from app.schemas.devices import DeviceRead, DeviceUpdate, OfflineLeaseRead

# Included before the staff router, which also has paths under /staff/devices (setup codes and linking).
router = APIRouter(prefix="/staff/devices", tags=["Devices"])

managers = require_roles(Role.OWNER, Role.MANAGER, password_session=True)


@router.post("/lease", response_model=OfflineLeaseRead)
async def offline_lease(
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    The device's permission to bill offline for a while (X-Device-Token only, no staff session). Refused with 403
    `offline_not_allowed` and `details.reason`: `subscription_inactive`, `module_disabled`, `business_disabled` or
    `device_disabled`. A revoked device gets 401 `device_revoked`.
    """
    return await service.issue_lease(db, redis_client, device)


@router.get("", response_model=List[DeviceRead])
async def list_devices(
    branch_id: Optional[uuid.UUID] = None,
    include_revoked: bool = Query(default=False),
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
):
    """The branch's linked devices, newest first. Revoked devices only with `include_revoked=true`."""
    return await service.list_devices(db, user, branch_id, include_revoked)


@router.patch("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: uuid.UUID,
    payload: DeviceUpdate,
    user: CurrentUser = Depends(managers),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    Name a device, or switch offline billing on or off for it. Switching on needs the `offline_billing` module (403
    `module_disabled`), a device that is not revoked (409 `device_revoked`) and a branch code of at most 4 characters (422
    `branch_code_too_long`); the first time, it gives the device its bill series letter (409 `no_series_left` when none is left).
    """
    return await service.update_device(db, redis_client, user, device_id, payload)


@router.post("/{device_id}/revoke", response_model=DeviceRead)
async def revoke_device(device_id: uuid.UUID, user: CurrentUser = Depends(managers), db: AsyncSession = Depends(get_db)):
    """Cut a device off (lost or stolen): its token stops working at once. Final; revoking again returns the record."""
    return await service.revoke_device(db, user, device_id)
