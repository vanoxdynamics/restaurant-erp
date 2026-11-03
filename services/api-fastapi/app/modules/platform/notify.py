"""Commit a platform change so tenants see it immediately: drop caches first, then tell connected clients."""

import uuid
from typing import Any, Dict, Iterable, Optional

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements import invalidate_all_entitlements, invalidate_entitlements
from app.core.enums import Topic
from app.core.events import publish_pending, queue_event
from app.models.floor import Branch
from app.models.subscription import SubscriptionEvent


def add_subscription_event(db: AsyncSession, org_id: uuid.UUID, event_type: str, actor_id: Optional[uuid.UUID],
                           data: Optional[Dict[str, Any]] = None) -> None:
    db.add(SubscriptionEvent(organization_id=org_id, event_type=event_type, actor_admin_id=actor_id, data=data or {}))


async def queue_entitlement_events(db: AsyncSession, org_ids: Iterable[uuid.UUID], *, revoked: bool = False) -> None:
    """Queue `entitlements.changed` (or `access.revoked`, which also closes open sockets) for every branch."""
    org_ids = list(org_ids)
    if not org_ids:
        return
    rows = (await db.execute(select(Branch.id, Branch.organization_id).where(Branch.organization_id.in_(org_ids)))).all()
    event_type = "access.revoked" if revoked else "entitlements.changed"
    for branch_id, org_id in rows:
        queue_event(db, branch_id, Topic.ENTITLEMENTS, event_type, org_id)


async def commit_and_notify(db: AsyncSession, redis_client: aioredis.Redis, org_ids: Iterable[uuid.UUID], *,
                            revoked: bool = False, everyone: bool = False) -> None:
    """
    Queue events, commit, invalidate cached entitlements, then publish. The order matters: invalidating after the
    commit means no request can re-cache the old state, and publishing last means a client that reacts to the event
    reads the new state.
    """
    org_ids = list(org_ids)
    await queue_entitlement_events(db, org_ids, revoked=revoked)
    await db.commit()
    if everyone:
        await invalidate_all_entitlements(redis_client)
    else:
        for org_id in org_ids:
            await invalidate_entitlements(redis_client, org_id)
    await publish_pending(db)
