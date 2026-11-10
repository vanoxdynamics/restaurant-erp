"""Platform API: subscribers, subscriptions, modules, plans and the dashboard."""

import uuid
from typing import Any, Awaitable, Callable, List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business import CATEGORY_LABELS, PRESETS, SETTING_LABELS
from app.core.database import get_db
from app.core.enums import BillingTerm, BusinessCategory, PlatformRole
from app.core.idempotency import IdempotencyGuard
from app.core.redis import get_redis
from app.modules.platform import approvals_service, module_service, org_service
from app.modules.platform.deps import (
    PlatformPrincipal,
    assert_step_up,
    get_platform_principal,
    request_meta,
    require_platform_roles,
    require_step_up,
)
from app.schemas.common import Page
from app.schemas.platform_orgs import (
    ActivateBody,
    ApprovalRead,
    ActivateResult,
    BulkModuleBody,
    BulkModuleResult,
    BusinessCategories,
    CancelBody,
    ChangePreviewRead,
    Dashboard,
    ExtendBody,
    GlobalModuleBody,
    GlobalModuleRead,
    ImpersonateBody,
    ImpersonationToken,
    ModuleOverrideBody,
    OrganizationCreate,
    OrganizationCreated,
    OrganizationDetail,
    OrganizationSummary,
    OrganizationUpdate,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    ReasonBody,
    SubscriptionChange,
    SubscriptionRead,
    SubscriptionUpdate,
    SubscriptionUpdated,
)

SUPER, BILLING, SUPPORT, READ = (PlatformRole.SUPER_ADMIN, PlatformRole.BILLING, PlatformRole.SUPPORT, PlatformRole.READ_ONLY)
any_role = require_platform_roles(SUPER, BILLING, SUPPORT, READ)
money_roles = require_platform_roles(SUPER, BILLING)
money_step_up = require_step_up(SUPER, BILLING)

router = APIRouter(prefix="/platform", tags=["Platform: Subscribers & Modules"])


# --- idempotency for money-affecting calls -----------------------------------------------------------------------


def idempotent(scope: str) -> Callable[..., Awaitable[IdempotencyGuard]]:
    async def dependency(
        idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key", min_length=8, max_length=100),
        redis_client: aioredis.Redis = Depends(get_redis),
        principal: PlatformPrincipal = Depends(get_platform_principal),
    ) -> IdempotencyGuard:
        return IdempotencyGuard(redis_client, scope=f"platform:{scope}", key=idempotency_key, user_id=principal.admin.id, required=True)
    return dependency


async def run_once(guard: IdempotencyGuard, payload: Any, status_code: int, work: Callable[[], Awaitable[BaseModel]]):
    """Replay the original response for a retried request instead of applying the change twice."""
    cached = await guard.begin(payload)
    if cached:
        return cached.as_response()
    try:
        result = await work()
    except Exception:
        await guard.release()
        raise
    return await guard.complete(status_code, result)


# --- dashboard / plans / modules -----------------------------------------------------------------------------------


@router.get("/dashboard", response_model=Dashboard, dependencies=[Depends(any_role)])
async def dashboard(db: AsyncSession = Depends(get_db)):
    return await module_service.dashboard(db)


@router.get("/plans", response_model=List[PlanRead], dependencies=[Depends(any_role)])
async def list_plans(db: AsyncSession = Depends(get_db)):
    return await module_service.list_plans(db)


@router.post("/plans", response_model=PlanRead, status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanCreate, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                      db: AsyncSession = Depends(get_db)):
    return await module_service.create_plan(db, actor, body, request_meta(request))


@router.patch("/plans/{plan_id}", response_model=PlanRead)
async def update_plan(plan_id: uuid.UUID, body: PlanUpdate, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                      db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Edit prices, limits or the module list. Subscribers on the plan see module changes immediately."""
    return await module_service.update_plan(db, redis_client, actor, plan_id, body, request_meta(request))


@router.get("/modules", response_model=List[GlobalModuleRead], dependencies=[Depends(any_role)])
async def list_modules(db: AsyncSession = Depends(get_db)):
    return await module_service.list_global_modules(db)


@router.patch("/modules/{module_key}", status_code=status.HTTP_204_NO_CONTENT, responses={202: {"model": ApprovalRead}})
async def switch_module_globally(module_key: str, body: GlobalModuleBody, request: Request,
                                 actor: PlatformPrincipal = Depends(require_step_up(SUPER)),
                                 db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """
    Kill switch: turns a module off (or back on) for every subscriber at once. Switching one off needs a second super admin
    to approve (202 with the approval), unless you are the only one.
    """
    meta = request_meta(request)

    async def execute():
        await module_service.set_global_module(db, redis_client, actor, module_key, body, meta)

    if body.enabled:
        await execute()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    mode, result = await approvals_service.gate(
        db, redis_client, actor, action="module.global_switch", summary=f"Switch off the {module_key} module for every subscriber",
        payload={"module_key": module_key, "enabled": False, "reason": body.reason}, reason=body.reason, meta=meta, execute=execute)
    if mode == "pending":
        return JSONResponse(ApprovalRead(**result).model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/modules/bulk", response_model=BulkModuleResult, responses={202: {"model": ApprovalRead}})
async def bulk_modules(body: BulkModuleBody, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                       db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Switch a module for many subscribers (or everyone on a plan). dry_run=true (default) only shows the diff."""
    meta = request_meta(request)
    if body.dry_run:
        return await module_service.bulk_module_change(db, redis_client, actor, body, meta)
    assert_step_up(actor)
    # Applying changes to many subscribers at once is exactly what a second pair of eyes is for.
    scope = f"plan {body.plan_id}" if body.plan_id else f"{len(body.organization_ids or [])} subscriber(s)"
    mode, result = await approvals_service.gate(
        db, redis_client, actor, action="module.bulk_apply",
        summary=f"Switch {body.module_key.value} {'on' if body.enabled else 'off'} for {scope}",
        payload=body.model_dump(mode="json", exclude={"dry_run"}), reason=body.reason, meta=meta,
        execute=lambda: module_service.bulk_module_change(db, redis_client, actor, body, meta))
    if mode == "pending":
        return JSONResponse(ApprovalRead(**result).model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)
    return result


# --- subscribers -----------------------------------------------------------------------------------------------------


@router.get("/business-categories", response_model=BusinessCategories, dependencies=[Depends(any_role)])
async def business_categories():
    """The categories a subscriber can be, with the workflow settings each one presets."""
    return BusinessCategories(
        categories=[{"key": c.value, "name": CATEGORY_LABELS[c.value], "settings": PRESETS[c.value]} for c in BusinessCategory],
        setting_labels=SETTING_LABELS,
    )


@router.get("/organizations", response_model=Page[OrganizationSummary], dependencies=[Depends(any_role)])
async def list_organizations(
    q: Optional[str] = Query(None, max_length=100), org_status: Optional[str] = Query(None, alias="status"),
    category: Optional[BusinessCategory] = None,
    plan_id: Optional[uuid.UUID] = None, term: Optional[BillingTerm] = None,
    subscription_status: Optional[str] = Query(None, max_length=20),
    expiring_within_days: Optional[int] = Query(None, ge=0, le=3650),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db),
):
    items, total = await org_service.list_organizations(
        db, q_text=q, status=org_status, plan_id=plan_id, term=term.value if term else None, sub_status=subscription_status,
        expiring_within_days=expiring_within_days, limit=limit, offset=offset, category=category.value if category else None)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(body: OrganizationCreate, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                              guard: IdempotencyGuard = Depends(idempotent("org-create")),
                              db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Onboard a subscriber with its plan, billing term, first branch and owner account."""
    async def work() -> OrganizationCreated:
        org, invoice = await org_service.create_organization(db, redis_client, actor, body, request_meta(request))
        return OrganizationCreated(organization=OrganizationDetail(**await org_service.organization_detail(db, org.id)),
                                   amount_due=str(invoice.amount_due) if invoice else None,
                                   invoice_number=invoice.number if invoice else None)
    return await run_once(guard, body, status.HTTP_201_CREATED, work)


@router.get("/organizations/{org_id}", response_model=OrganizationDetail, dependencies=[Depends(any_role)])
async def get_organization(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await org_service.organization_detail(db, org_id)


@router.patch("/organizations/{org_id}", response_model=OrganizationDetail)
async def update_organization(org_id: uuid.UUID, body: OrganizationUpdate, request: Request,
                              actor: PlatformPrincipal = Depends(money_roles), db: AsyncSession = Depends(get_db),
                              redis_client: aioredis.Redis = Depends(get_redis)):
    await org_service.update_organization(db, redis_client, actor, org_id, body, request_meta(request))
    return await org_service.organization_detail(db, org_id)


@router.post("/organizations/{org_id}/suspend", response_model=OrganizationDetail)
async def suspend(org_id: uuid.UUID, body: ReasonBody, request: Request, actor: PlatformPrincipal = Depends(money_step_up),
                  db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Cut the subscriber off now: every session is revoked and open realtime connections are closed."""
    await org_service.suspend_organization(db, redis_client, actor, org_id, body.reason, request_meta(request))
    return await org_service.organization_detail(db, org_id)


@router.post("/organizations/{org_id}/reinstate", response_model=OrganizationDetail)
async def reinstate(org_id: uuid.UUID, body: ReasonBody, request: Request, actor: PlatformPrincipal = Depends(money_step_up),
                    db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    await org_service.reinstate_organization(db, redis_client, actor, org_id, body.reason, request_meta(request))
    return await org_service.organization_detail(db, org_id)


@router.post("/organizations/{org_id}/cancel", response_model=OrganizationDetail, responses={202: {"model": ApprovalRead}})
async def cancel(org_id: uuid.UUID, body: CancelBody, request: Request, actor: PlatformPrincipal = Depends(money_step_up),
                 db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """
    Cancel at the end of the paid period (default) or immediately (read-only from now on). Cancelling immediately needs a
    second super admin to approve (202 with the approval), unless you are the only one.
    """
    meta = request_meta(request)

    async def execute():
        await org_service.cancel_subscription(db, redis_client, actor, org_id, body, meta)
        return await org_service.organization_detail(db, org_id)

    if body.at_period_end:
        return await execute()
    mode, result = await approvals_service.gate(
        db, redis_client, actor, action="subscription.cancel_immediately", summary=f"Cancel the subscription of organization {org_id} immediately",
        payload={"organization_id": str(org_id), "reason": body.reason}, reason=body.reason, meta=meta, execute=execute)
    if mode == "pending":
        return JSONResponse(ApprovalRead(**result).model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)
    return result


@router.post("/organizations/{org_id}/activate")
async def activate(org_id: uuid.UUID, body: ActivateBody, request: Request, actor: PlatformPrincipal = Depends(money_roles),
                   guard: IdempotencyGuard = Depends(idempotent("activate")),
                   db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Convert a trial (or revive an expired subscription) into a paid period starting now."""
    async def work() -> ActivateResult:
        invoice = await org_service.activate_subscription(db, redis_client, actor, org_id, body, request_meta(request))
        return ActivateResult(amount_due=str(invoice.amount_due), currency=invoice.currency, invoice_number=invoice.number,
                              credit_applied=str(invoice.credit_applied))
    return await run_once(guard, {"org": str(org_id), **body.model_dump(mode="json")}, status.HTTP_200_OK, work)


# --- subscription: term / plan / price ---------------------------------------------------------------------------------


@router.post("/organizations/{org_id}/subscription/preview-change", response_model=ChangePreviewRead,
             dependencies=[Depends(any_role)])
async def preview_change(org_id: uuid.UUID, body: SubscriptionChange, db: AsyncSession = Depends(get_db)):
    """What changing plan, term or price would cost: credit for unused time, amount due, and the new period."""
    return await org_service.preview_change(db, org_id, body)


@router.put("/organizations/{org_id}/subscription")
async def update_subscription(org_id: uuid.UUID, body: SubscriptionUpdate, request: Request,
                              actor: PlatformPrincipal = Depends(money_roles),
                              guard: IdempotencyGuard = Depends(idempotent("subscription")),
                              db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Change plan, billing term (monthly/yearly), price, auto-renew or limits. Needs the current `version`."""
    async def work() -> SubscriptionUpdated:
        sub, preview, invoice = await org_service.update_subscription(db, redis_client, actor, org_id, body, request_meta(request))
        return SubscriptionUpdated(subscription=SubscriptionRead.model_validate(sub),
                                   change=ChangePreviewRead(**preview.__dict__) if preview else None,
                                   invoice_number=invoice.number if invoice else None)
    return await run_once(guard, {"org": str(org_id), **body.model_dump(mode="json")}, status.HTTP_200_OK, work)


@router.post("/organizations/{org_id}/subscription/extend")
async def extend_subscription(org_id: uuid.UUID, body: ExtendBody, request: Request,
                              actor: PlatformPrincipal = Depends(money_roles),
                              guard: IdempotencyGuard = Depends(idempotent("extend")),
                              db: AsyncSession = Depends(get_db), redis_client: aioredis.Redis = Depends(get_redis)):
    """Add days to the current period (goodwill credit, payment delay). Reason is mandatory and audited."""
    async def work() -> SubscriptionRead:
        sub = await org_service.extend_subscription(db, redis_client, actor, org_id, body, request_meta(request))
        return SubscriptionRead.model_validate(sub)
    return await run_once(guard, {"org": str(org_id), **body.model_dump(mode="json")}, status.HTTP_200_OK, work)


# --- per-subscriber modules / impersonation -------------------------------------------------------------------------------


@router.put("/organizations/{org_id}/modules/{module_key}", response_model=OrganizationDetail)
async def set_module(org_id: uuid.UUID, module_key: str, body: ModuleOverrideBody, request: Request,
                     actor: PlatformPrincipal = Depends(money_roles), db: AsyncSession = Depends(get_db),
                     redis_client: aioredis.Redis = Depends(get_redis)):
    """Force a module on or off for this subscriber, optionally until `expires_at`."""
    await module_service.set_module_override(db, redis_client, actor, org_id, module_key, body, request_meta(request))
    return await org_service.organization_detail(db, org_id)


@router.delete("/organizations/{org_id}/modules/{module_key}", response_model=OrganizationDetail)
async def clear_module(org_id: uuid.UUID, module_key: str, request: Request,
                       reason: str = Query(..., min_length=3, max_length=500),
                       actor: PlatformPrincipal = Depends(money_roles), db: AsyncSession = Depends(get_db),
                       redis_client: aioredis.Redis = Depends(get_redis)):
    """Remove the override so the plan decides again."""
    await module_service.clear_module_override(db, redis_client, actor, org_id, module_key, reason.strip(), request_meta(request))
    return await org_service.organization_detail(db, org_id)


@router.post("/organizations/{org_id}/impersonate", response_model=ImpersonationToken)
async def impersonate(org_id: uuid.UUID, body: ImpersonateBody, request: Request,
                      actor: PlatformPrincipal = Depends(require_step_up(SUPER, SUPPORT)), db: AsyncSession = Depends(get_db)):
    """A read-only tenant token (max 30 minutes) to see what the subscriber sees. Audited."""
    return await org_service.impersonate(db, actor, org_id, body, request_meta(request))
