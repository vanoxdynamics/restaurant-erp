"""
Domain events published to realtime subscribers only after the database commit succeeds.

Services call `queue_event(db, ...)` while building a change and then `commit(db)` instead of
`db.commit()`. Rolled-back work never publishes.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.enums import Topic

logger = logging.getLogger(__name__)

Publisher = Callable[[uuid.UUID, Topic, Dict[str, Any]], Awaitable[None]]

_publisher: Optional[Publisher] = None
_PENDING_KEY = "pending_events"


def set_publisher(publisher: Optional[Publisher]) -> None:
    global _publisher
    _publisher = publisher


def queue_event(
    db: AsyncSession,
    branch_id: uuid.UUID,
    topic: Topic,
    event_type: str,
    entity_id: Optional[uuid.UUID] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    pending: List[tuple] = db.info.setdefault(_PENDING_KEY, [])
    envelope = {
        "type": event_type,
        "branch_id": str(branch_id),
        "entity_id": str(entity_id) if entity_id else None,
        "data": data or {},
        "ts": utcnow().isoformat(),
    }
    pending.append((branch_id, topic, envelope))


def discard_events(db: AsyncSession) -> None:
    db.info.pop(_PENDING_KEY, None)


async def commit(db: AsyncSession) -> None:
    """Commit the session, then publish any queued events. Publish failures are logged, never raised."""
    try:
        await db.commit()
    except Exception:
        discard_events(db)
        raise
    pending = db.info.pop(_PENDING_KEY, [])
    if _publisher is None:
        return
    for branch_id, topic, envelope in pending:
        try:
            await _publisher(branch_id, topic, envelope)
        except Exception:
            logger.exception("Failed to publish %s event for branch %s", envelope["type"], branch_id)
