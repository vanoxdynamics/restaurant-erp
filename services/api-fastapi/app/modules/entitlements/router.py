import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.entitlements import get_entitlements
from app.core.redis import get_redis
from app.schemas.entitlements import EntitlementsRead

router = APIRouter(prefix="/me", tags=["Subscription"])


@router.get("/entitlements", response_model=EntitlementsRead)
async def my_entitlements(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Modules, limits and subscription state of the caller's organization."""
    ent = await get_entitlements(db, user.organization_id, redis_client)
    return EntitlementsRead(**{**ent.__dict__, "modules": sorted(ent.modules),
                               "impersonation": user.impersonated_by is not None})
