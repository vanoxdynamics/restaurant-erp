import uuid
from datetime import date, datetime
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_module, require_roles, resolve_branch_id
from app.core.errors import Forbidden
from app.core.database import get_db
from app.core.enums import AuditAction, Role
from app.core.modules import ModuleKey
from app.core.pagination import PageParams, page_params
from app.core.redis import get_redis
from app.modules.analytics import service
from app.modules.analytics.overview import Period, overview
from app.schemas.analytics import AuditLogRead, LiveKPIsResponse, OverviewResponse, SalesBreakdownResponse, ZReportResponse
from app.schemas.common import Page

analysts = require_roles(Role.OWNER, Role.MANAGER, password_session=True)

router = APIRouter(
    prefix="/analytics",
    tags=["Analytics & Fraud Detection"],
    dependencies=[Depends(require_module(ModuleKey.ANALYTICS))],
)


@router.get("/kpis/live", response_model=LiveKPIsResponse)
async def get_live_kpis(
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(analysts),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Real-time restaurant KPIs (active tables, open orders, daily sales) for the admin app. Cached 10s."""
    return await service.live_kpis(db, redis_client, resolve_branch_id(user, branch_id))


@router.get("/sales/today", response_model=SalesBreakdownResponse)
async def get_sales_breakdown(
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(analysts),
    db: AsyncSession = Depends(get_db),
):
    """Today's sales by hour, top items and payment methods."""
    return await service.sales_breakdown(db, resolve_branch_id(user, branch_id))


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    period: Period = Period.LAST_7,
    branch_id: Optional[uuid.UUID] = None,
    all_branches: bool = False,
    user: CurrentUser = Depends(analysts),
    db: AsyncSession = Depends(get_db),
):
    """
    The business over a period (today, yesterday, 7d, 30d, 90d) against the period before: headline figures, the sales trend,
    busiest hours and weekdays, categories, top items, staff, order types and payment methods. Owners may ask for all their
    branches together.
    """
    if all_branches:
        if not user.is_owner:
            raise Forbidden("Only owners can see all branches together", code="owner_only")
        branch_ids = sorted(user.org_branch_ids)
        if not branch_ids:
            raise Forbidden("This business has no branches yet", code="branch_required")
    else:
        branch_ids = [resolve_branch_id(user, branch_id)]
    return await overview(db, branch_ids, period)


@router.get("/reports/z-report", response_model=ZReportResponse)
async def get_z_report(
    branch_id: Optional[uuid.UUID] = None,
    report_date: Optional[date] = None,
    user: CurrentUser = Depends(analysts),
    db: AsyncSession = Depends(get_db),
):
    """End-of-Day Z-Report summarizing sales, taxes, tenders, discounts and voids for a business date."""
    return await service.z_report(db, resolve_branch_id(user, branch_id), report_date)


@router.post("/reports/z-report/close", response_model=ZReportResponse)
async def close_z_report(
    report_date: date,
    branch_id: Optional[uuid.UUID] = None,
    user: CurrentUser = Depends(analysts),
    db: AsyncSession = Depends(get_db),
):
    """Compute and archive the Z-report for a business date (safe to rerun)."""
    return await service.archive_z_report(db, resolve_branch_id(user, branch_id), report_date)


@router.get("/audit-log", response_model=Page[AuditLogRead])
async def list_audit_logs(
    branch_id: Optional[uuid.UUID] = None,
    action_type: Optional[AuditAction] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    params: PageParams = Depends(page_params),
    user: CurrentUser = Depends(analysts),
    db: AsyncSession = Depends(get_db),
):
    """Audit log of high-risk actions (KOT voids, bill voids, high discounts, transfers), newest first."""
    items, total = await service.list_audit_logs(
        db, resolve_branch_id(user, branch_id), params, action_type, since, until
    )
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)
