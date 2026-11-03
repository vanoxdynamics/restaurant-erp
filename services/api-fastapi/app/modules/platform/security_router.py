"""Platform API: four-eyes approvals and security alerts."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ApprovalStatus, PlatformRole
from app.core.errors import NotFound
from app.core.redis import get_redis
from app.models.platform_security import PlatformAlert
from app.modules.platform import approvals_service
from app.modules.platform.deps import PlatformPrincipal, request_meta, require_platform_roles, require_step_up
from app.schemas.platform_orgs import AlertRead, ApprovalDecision, ApprovalRead, ApprovalRejection

SUPER, READ = PlatformRole.SUPER_ADMIN, PlatformRole.READ_ONLY
router = APIRouter(prefix="/platform", tags=["Platform: Approvals & Alerts"])
overseers = require_platform_roles(SUPER, READ)


@router.get("/approvals", response_model=List[ApprovalRead], dependencies=[Depends(overseers)])
async def list_approvals(approval_status: Optional[ApprovalStatus] = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200),
                         db: AsyncSession = Depends(get_db)):
    return await approvals_service.list_approvals(db, approval_status.value if approval_status else None, limit)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRead)
async def approve(approval_id: uuid.UUID, body: ApprovalDecision, request: Request,
                  actor: PlatformPrincipal = Depends(require_step_up(SUPER)), db: AsyncSession = Depends(get_db),
                  redis_client: aioredis.Redis = Depends(get_redis)):
    """Approve and run a request made by another super admin. You cannot approve your own."""
    await approvals_service.approve(db, redis_client, actor, approval_id, body.note, request_meta(request))
    return await approvals_service.get_view(db, approval_id)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalRead)
async def reject(approval_id: uuid.UUID, body: ApprovalRejection, request: Request,
                 actor: PlatformPrincipal = Depends(require_step_up(SUPER)), db: AsyncSession = Depends(get_db)):
    await approvals_service.reject(db, actor, approval_id, body.note, request_meta(request))
    return await approvals_service.get_view(db, approval_id)


@router.post("/approvals/{approval_id}/cancel", response_model=ApprovalRead)
async def cancel(approval_id: uuid.UUID, request: Request, actor: PlatformPrincipal = Depends(require_platform_roles(SUPER)),
                 db: AsyncSession = Depends(get_db)):
    """Withdraw your own pending request."""
    await approvals_service.cancel(db, actor, approval_id, request_meta(request))
    return await approvals_service.get_view(db, approval_id)


@router.get("/alerts", response_model=List[AlertRead], dependencies=[Depends(overseers)])
async def list_alerts(open_only: bool = False, severity: Optional[str] = Query(None, pattern="^(info|warning|critical)$"),
                      limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    stmt = select(PlatformAlert).order_by(PlatformAlert.created_at.desc()).limit(limit)
    if open_only:
        stmt = stmt.where(PlatformAlert.acknowledged_at.is_(None))
    if severity:
        stmt = stmt.where(PlatformAlert.severity == severity)
    return list((await db.scalars(stmt)).all())


@router.get("/alerts/summary", dependencies=[Depends(overseers)])
async def alert_summary(db: AsyncSession = Depends(get_db)):
    """Counts for the console badge: unacknowledged alerts by severity and pending approvals."""
    rows = (await db.execute(select(PlatformAlert.severity, func.count()).where(PlatformAlert.acknowledged_at.is_(None))
                             .group_by(PlatformAlert.severity))).all()
    pending = await approvals_service.list_approvals(db, ApprovalStatus.PENDING.value, 200)
    return {"open_alerts": dict(rows), "pending_approvals": len(pending)}


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertRead, status_code=status.HTTP_200_OK)
async def acknowledge(alert_id: uuid.UUID, actor: PlatformPrincipal = Depends(require_platform_roles(SUPER)),
                      db: AsyncSession = Depends(get_db)):
    alert = await db.get(PlatformAlert, alert_id)
    if alert is None:
        raise NotFound("Alert not found")
    if alert.acknowledged_at is None:
        alert.acknowledged_at, alert.acknowledged_by = datetime.now(timezone.utc), actor.admin.id
        await db.commit()
    return alert
