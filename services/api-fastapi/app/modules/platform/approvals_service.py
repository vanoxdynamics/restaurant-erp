"""
Four-eyes approval for dangerous platform actions.

The requester never executes the action themselves: it is stored as a pending approval and a *different* super admin
(re-authenticating with password + TOTP) approves it, which runs exactly the stored payload. Pending requests expire.

If only one active super admin exists there is nobody to approve; the action then runs directly, is flagged in the audit
log and raises an alert, so a small team is never locked out by its own safety net. Set PLATFORM_FOUR_EYES=false to turn
the mechanism off entirely.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import AlertSeverity, ApprovalStatus, PlatformRole
from app.core.errors import Conflict, Forbidden, NotFound
from app.models.platform import PlatformAdmin
from app.models.platform_security import PlatformApproval
from app.modules.platform.alerts import raise_alert
from app.modules.platform.audit import RequestMeta, append_audit
from app.modules.platform.deps import PlatformPrincipal

Handler = Callable[[AsyncSession, aioredis.Redis, PlatformPrincipal, Dict[str, Any], RequestMeta], Awaitable[Any]]

# --- what can be approved ------------------------------------------------------------------------------------------


async def _global_switch(db, redis_client, actor, payload, meta):
    from app.modules.platform import module_service
    from app.schemas.platform_orgs import GlobalModuleBody

    await module_service.set_global_module(db, redis_client, actor, payload["module_key"],
                                           GlobalModuleBody(enabled=payload["enabled"], reason=payload["reason"]), meta)
    return {"module": payload["module_key"], "enabled": payload["enabled"]}


async def _bulk_apply(db, redis_client, actor, payload, meta):
    from app.modules.platform import module_service
    from app.schemas.platform_orgs import BulkModuleBody

    result = await module_service.bulk_module_change(db, redis_client, actor, BulkModuleBody(**{**payload, "dry_run": False}), meta)
    return {"changed": len(result["changed"]), "unchanged": result["unchanged"], "blocked": len(result["blocked"])}


async def _cancel_now(db, redis_client, actor, payload, meta):
    from app.modules.platform import org_service
    from app.schemas.platform_orgs import CancelBody

    sub = await org_service.cancel_subscription(db, redis_client, actor, uuid.UUID(payload["organization_id"]),
                                                CancelBody(reason=payload["reason"], at_period_end=False), meta)
    return {"organization_id": payload["organization_id"], "status": sub.status}


HANDLERS: Dict[str, Handler] = {
    "module.global_switch": _global_switch,
    "module.bulk_apply": _bulk_apply,
    "subscription.cancel_immediately": _cancel_now,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def four_eyes_available(db: AsyncSession, actor: PlatformPrincipal) -> bool:
    """True when the feature is on and at least one *other* active super admin could approve."""
    if not settings.PLATFORM_FOUR_EYES:
        return False
    others = await db.scalar(select(func.count()).select_from(PlatformAdmin).where(
        PlatformAdmin.role == PlatformRole.SUPER_ADMIN.value, PlatformAdmin.is_active.is_(True), PlatformAdmin.id != actor.admin.id))
    return bool(others)


async def submit(db: AsyncSession, actor: PlatformPrincipal, action: str, summary: str, payload: Dict[str, Any], reason: str,
                 meta: RequestMeta) -> PlatformApproval:
    approval = PlatformApproval(
        action=action, summary=summary[:300], payload=payload, reason=reason[:500], requested_by=actor.admin.id,
        expires_at=_now() + timedelta(hours=settings.PLATFORM_APPROVAL_TTL_HOURS))
    db.add(approval)
    await db.flush()
    await append_audit(db, action="approval.request", admin_id=actor.admin.id, target_type="approval", target_id=approval.id,
                       after={"action": action, "summary": summary, "payload": payload}, reason=reason, meta=meta)
    await raise_alert(db, "approval.requested", AlertSeverity.WARNING,
                      f"{actor.admin.email} needs a second super admin to approve: {summary}",
                      {"approval_id": approval.id, "action": action, "by": actor.admin.email})
    await db.commit()
    return approval


async def note_direct_execution(db: AsyncSession, actor: PlatformPrincipal, action: str, summary: str) -> None:
    """The action runs without approval because no second super admin exists (or four-eyes is off): leave a trail."""
    if not settings.PLATFORM_FOUR_EYES:
        return
    await raise_alert(db, "approval.unavailable", AlertSeverity.WARNING,
                      f"{summary} ran without a second approver because {actor.admin.email} is the only active super admin",
                      {"action": action, "by": actor.admin.email})


async def _load(db: AsyncSession, approval_id: uuid.UUID, *, lock: bool = True) -> PlatformApproval:
    stmt = select(PlatformApproval).where(PlatformApproval.id == approval_id)
    approval = await db.scalar(stmt.with_for_update() if lock else stmt)
    if approval is None:
        raise NotFound("Approval not found")
    return approval


async def approve(db: AsyncSession, redis_client: aioredis.Redis, approver: PlatformPrincipal, approval_id: uuid.UUID,
                  note: Optional[str], meta: RequestMeta) -> PlatformApproval:
    approval = await _load(db, approval_id)
    if approval.status != ApprovalStatus.PENDING.value:
        raise Conflict(f"This request is already {approval.status}", code="approval_not_pending")
    if approval.requested_by == approver.admin.id:
        raise Forbidden("You cannot approve your own request; ask another super admin", code="four_eyes_self_approval")
    if approval.expires_at <= _now():
        approval.status = ApprovalStatus.EXPIRED.value
        await append_audit(db, action="approval.expire", admin_id=approver.admin.id, target_type="approval", target_id=approval.id, meta=meta)
        await db.commit()
        raise Conflict("This request expired; ask for it again", code="approval_expired")
    handler = HANDLERS.get(approval.action)
    if handler is None:
        raise Conflict("This request type is no longer supported", code="approval_unknown_action")

    # Mark it executed in the same transaction the action commits in: either both happen or neither does.
    approval.status, approval.decided_by, approval.decided_at, approval.decision_note = (
        ApprovalStatus.EXECUTED.value, approver.admin.id, _now(), note)
    await append_audit(db, action="approval.approve", admin_id=approver.admin.id, target_type="approval", target_id=approval.id,
                       after={"action": approval.action, "requested_by": str(approval.requested_by), "payload": approval.payload},
                       reason=note, meta=meta)
    await raise_alert(db, "approval.executed", AlertSeverity.WARNING, f"{approver.admin.email} approved and ran: {approval.summary}",
                      {"approval_id": approval.id, "approver": approver.admin.email})
    approver_id = approver.admin.id  # read now: a rollback expires ORM objects
    try:
        result = await handler(db, redis_client, approver, approval.payload, meta)
    except Exception as exc:
        await db.rollback()
        failed = await _load(db, approval_id)
        failed.status, failed.decided_by, failed.decided_at = ApprovalStatus.FAILED.value, approver_id, _now()
        failed.result = {"error": str(getattr(exc, "message", exc))[:300]}
        await append_audit(db, action="approval.failed", admin_id=approver_id, target_type="approval", target_id=failed.id,
                           after=failed.result, meta=meta)
        await db.commit()
        raise
    # The handler committed the action together with the "executed" status; record what it did.
    approval.result = result
    await db.commit()
    await db.refresh(approval)
    return approval


async def reject(db: AsyncSession, actor: PlatformPrincipal, approval_id: uuid.UUID, note: str, meta: RequestMeta) -> PlatformApproval:
    approval = await _load(db, approval_id)
    if approval.status != ApprovalStatus.PENDING.value:
        raise Conflict(f"This request is already {approval.status}", code="approval_not_pending")
    if approval.requested_by == actor.admin.id:
        raise Forbidden("Use cancel to withdraw your own request", code="four_eyes_self_decision")
    approval.status, approval.decided_by, approval.decided_at, approval.decision_note = (
        ApprovalStatus.REJECTED.value, actor.admin.id, _now(), note)
    await append_audit(db, action="approval.reject", admin_id=actor.admin.id, target_type="approval", target_id=approval.id,
                       after={"action": approval.action}, reason=note, meta=meta)
    await db.commit()
    return approval


async def cancel(db: AsyncSession, actor: PlatformPrincipal, approval_id: uuid.UUID, meta: RequestMeta) -> PlatformApproval:
    approval = await _load(db, approval_id)
    if approval.requested_by != actor.admin.id:
        raise Forbidden("Only the requester can withdraw a request", code="not_requester")
    if approval.status != ApprovalStatus.PENDING.value:
        raise Conflict(f"This request is already {approval.status}", code="approval_not_pending")
    approval.status, approval.decided_by, approval.decided_at = ApprovalStatus.CANCELLED.value, actor.admin.id, _now()
    await append_audit(db, action="approval.cancel", admin_id=actor.admin.id, target_type="approval", target_id=approval.id, meta=meta)
    await db.commit()
    return approval


async def list_approvals(db: AsyncSession, status: Optional[str], limit: int) -> List[Dict[str, Any]]:
    stmt = select(PlatformApproval).order_by(PlatformApproval.requested_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(PlatformApproval.status == status)
    approvals = (await db.scalars(stmt)).all()
    ids = {a.requested_by for a in approvals} | {a.decided_by for a in approvals if a.decided_by}
    emails = dict((await db.execute(select(PlatformAdmin.id, PlatformAdmin.email).where(PlatformAdmin.id.in_(ids)))).all()) if ids else {}
    return [view(a, emails) for a in approvals]


def view(a: PlatformApproval, emails: Dict[uuid.UUID, str]) -> Dict[str, Any]:
    now = _now()
    return {
        "id": a.id, "action": a.action, "summary": a.summary, "payload": a.payload, "reason": a.reason,
        "status": ApprovalStatus.EXPIRED.value if a.status == ApprovalStatus.PENDING.value and a.expires_at <= now else a.status,
        "requested_by": emails.get(a.requested_by, str(a.requested_by)), "requested_at": a.requested_at, "expires_at": a.expires_at,
        "decided_by": emails.get(a.decided_by) if a.decided_by else None, "decided_at": a.decided_at,
        "decision_note": a.decision_note, "result": a.result,
    }


async def get_view(db: AsyncSession, approval_id: uuid.UUID) -> Dict[str, Any]:
    approval = await _load(db, approval_id, lock=False)
    emails = dict((await db.execute(select(PlatformAdmin.id, PlatformAdmin.email).where(
        PlatformAdmin.id.in_([i for i in (approval.requested_by, approval.decided_by) if i])))).all())
    return view(approval, emails)


async def gate(
    db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, *, action: str, summary: str, payload: Dict[str, Any],
    reason: str, meta: RequestMeta, execute: Callable[[], Awaitable[Any]],
) -> Tuple[str, Any]:
    """Run `execute` now when no second approver exists, otherwise file an approval. Returns ("executed", result) or ("pending", view)."""
    if await four_eyes_available(db, actor):
        approval = await submit(db, actor, action, summary, payload, reason, meta)
        return "pending", await get_view(db, approval.id)
    await note_direct_execution(db, actor, action, summary)
    return "executed", await execute()
