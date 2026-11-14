from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, module_flag, require_roles
from app.core.database import get_db
from app.core.enums import Role
from app.core.lookups import get_or_404
from app.core.modules import ModuleKey
from app.models.organization import Organization
from app.modules.business import service
from app.schemas.business import (
    BusinessProfileRead,
    BusinessProfileUpdate,
    LogoUpload,
    OfflineSettingsRead,
    OfflineSettingsUpdate,
)

router = APIRouter(prefix="/business", tags=["Business profile"], dependencies=[Depends(get_current_user)])

# The business's own staff in charge decide how it presents itself; the platform's registered name is not touched.
editors = require_roles(Role.OWNER, Role.MANAGER)
owners = require_roles(Role.OWNER)
has_offline_module = module_flag(ModuleKey.OFFLINE_BILLING.value)


@router.get("/profile", response_model=BusinessProfileRead)
async def get_profile(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The name and logo printed on this business's bills."""
    return service.profile(await service.load_with_logo(db, user.organization_id))


@router.patch("/profile", response_model=BusinessProfileRead)
async def update_profile(payload: BusinessProfileUpdate, user: CurrentUser = Depends(editors), db: AsyncSession = Depends(get_db)):
    """Set the name printed on bills (owners and managers). `display_name: null` goes back to the registered name."""
    return await service.update_display_name(db, user.organization_id, payload.display_name)


@router.put("/logo", response_model=BusinessProfileRead)
async def upload_logo(payload: LogoUpload, user: CurrentUser = Depends(editors), db: AsyncSession = Depends(get_db)):
    """Set the logo printed on bills: a PNG or JPEG under 500 KB, base64 encoded. 422 `invalid_logo` / `logo_too_large`."""
    return await service.set_logo(db, user.organization_id, payload.image_base64)


@router.delete("/logo", response_model=BusinessProfileRead)
async def remove_logo(user: CurrentUser = Depends(editors), db: AsyncSession = Depends(get_db)):
    """Remove the logo from bills."""
    return await service.set_logo(db, user.organization_id, None)


@router.get("/offline", response_model=OfflineSettingsRead)
async def get_offline_settings(
    user: CurrentUser = Depends(editors),
    module_enabled: bool = Depends(has_offline_module),
    db: AsyncSession = Depends(get_db),
):
    """Whether the business allows offline billing, and the limits of each device's offline lease (owners and managers)."""
    return service.offline_settings(await get_or_404(db, Organization, user.organization_id), module_enabled)


@router.patch("/offline", response_model=OfflineSettingsRead)
async def update_offline_settings(
    payload: OfflineSettingsUpdate,
    user: CurrentUser = Depends(owners),
    module_enabled: bool = Depends(has_offline_module),
    db: AsyncSession = Depends(get_db),
):
    """
    Owners only. Only the fields sent change. Turning `offline_allowed` on without the `offline_billing` module is 403
    `module_disabled`; changing the limits or turning it off is always allowed.
    """
    changes = payload.model_dump(exclude_unset=True)
    return await service.update_offline_settings(db, user.organization_id, changes, module_enabled)
