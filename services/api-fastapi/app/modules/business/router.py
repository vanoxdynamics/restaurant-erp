from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.enums import Role
from app.modules.business import service
from app.schemas.business import BusinessProfileRead, BusinessProfileUpdate, LogoUpload

router = APIRouter(prefix="/business", tags=["Business profile"], dependencies=[Depends(get_current_user)])

# The business's own staff in charge decide how it presents itself; the platform's registered name is not touched.
editors = require_roles(Role.OWNER, Role.MANAGER)


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
