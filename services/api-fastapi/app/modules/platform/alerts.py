"""
Security alerts. Every alert is a database row (so nothing is lost), a log line (so existing log shipping sees it) and,
when PLATFORM_ALERT_WEBHOOK_URL is set, a signed JSON POST (Slack-compatible `text` plus structured fields).

`raise_alert` joins the caller's transaction: the alert exists only if the change it describes was committed, and it is
delivered right after that commit. A webhook outage never blocks or fails the request; `dispatch_pending_alerts`
(run by `python -m app.cli dispatch-alerts`) retries anything that was not delivered.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import httpx
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.enums import AlertSeverity
from app.models.platform_security import PlatformAlert

logger = logging.getLogger("platform.alerts")
_LEVELS = {AlertSeverity.INFO: logging.INFO, AlertSeverity.WARNING: logging.WARNING, AlertSeverity.CRITICAL: logging.ERROR}
_tasks: Set[asyncio.Task] = set()  # strong references so fire-and-forget deliveries are not garbage collected
_INFO_KEY = "alert_ids"


async def raise_alert(db: AsyncSession, kind: str, severity: AlertSeverity, message: str,
                      data: Optional[Dict[str, Any]] = None) -> PlatformAlert:
    alert = PlatformAlert(kind=kind, severity=severity.value, message=message[:500], data=_jsonable(data or {}))
    db.add(alert)
    await db.flush()
    db.info.setdefault(_INFO_KEY, []).append(alert.id)
    logger.log(_LEVELS[severity], "PLATFORM ALERT [%s] %s: %s %s", severity.value, kind, message, alert.data)
    return alert


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    return value


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(settings.PLATFORM_ALERT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


async def _post(client: httpx.AsyncClient, alert: PlatformAlert) -> bool:
    body = json.dumps({
        "text": f"[{alert.severity.upper()}] {alert.message}", "kind": alert.kind, "severity": alert.severity,
        "data": alert.data, "at": alert.created_at.isoformat(), "environment": settings.ENVIRONMENT, "id": str(alert.id),
    }).encode()
    headers = {"Content-Type": "application/json"}
    if settings.PLATFORM_ALERT_WEBHOOK_SECRET:
        headers["X-Alert-Signature"] = sign(body)
    try:
        response = await client.post(settings.PLATFORM_ALERT_WEBHOOK_URL, content=body, headers=headers)
        return response.status_code < 300
    except httpx.HTTPError:
        logger.warning("Alert webhook delivery failed for %s", alert.id)
        return False


async def deliver_alerts(ids: Optional[List[uuid.UUID]] = None, limit: int = 100) -> Dict[str, int]:
    """Send undelivered alerts to the webhook. Returns counts; does nothing when no webhook is configured."""
    if not settings.PLATFORM_ALERT_WEBHOOK_URL:
        return {"sent": 0, "failed": 0}
    sent = failed = 0
    async with AsyncSessionLocal() as db:
        stmt = select(PlatformAlert).where(PlatformAlert.notified_at.is_(None)).order_by(PlatformAlert.created_at).limit(limit)
        if ids is not None:
            stmt = stmt.where(PlatformAlert.id.in_(ids))
        alerts = (await db.scalars(stmt.with_for_update(skip_locked=True))).all()
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            for alert in alerts:
                if await _post(client, alert):
                    alert.notified_at = datetime.now(timezone.utc)
                    sent += 1
                else:
                    failed += 1
        await db.commit()
    return {"sent": sent, "failed": failed}


async def dispatch_pending_alerts() -> Dict[str, int]:
    return await deliver_alerts(None, limit=500)


@event.listens_for(Session, "after_commit")
def _schedule_delivery(session: Session) -> None:
    ids = session.info.pop(_INFO_KEY, None)
    if not ids or not settings.PLATFORM_ALERT_WEBHOOK_URL:
        return
    try:
        task = asyncio.get_running_loop().create_task(deliver_alerts(ids))
    except RuntimeError:  # no running loop (sync context): the retry job will pick it up
        return
    _tasks.add(task)
    task.add_done_callback(lambda t: (_tasks.discard(t), t.cancelled() or t.exception()))
