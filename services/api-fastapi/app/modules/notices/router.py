import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles, resolve_branch_id
from app.core.database import get_db
from app.core.enums import Role
from app.modules.notices import service
from app.schemas.notices import NoticesRead

router = APIRouter(prefix="/notices", tags=["Floor notices"], dependencies=[Depends(get_current_user)])

floor_staff = require_roles(Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.WAITER, Role.CASHIER)


@router.get("", response_model=NoticesRead)
async def list_notices(
    since: Optional[datetime] = None,
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(floor_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    What happened since `since` (the `now` of the previous answer) that floor staff act on: food ready, a KOT cancelled by
    the kitchen or printed at the counter, a cancel request answered, a bill paid, an item sold out. Waiters get their own
    orders; others the whole branch. Without `since` the answer is empty and only sets the clock.
    """
    return await service.notices(db, user, resolve_branch_id(user, branch_id), since)
