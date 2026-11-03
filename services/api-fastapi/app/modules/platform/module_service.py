"""Module switches (per subscriber, platform-wide, in bulk), plans and the operator dashboard."""

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements import compute_entitlements
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.modules import ALL_MODULES, MODULES, ModuleKey
from app.core.money import ZERO, q
from app.models.invoicing import Invoice
from app.models.organization import Organization
from app.models.subscription import OrganizationModule, Plan, PlanModule, PlatformModule, Subscription
from app.modules.platform.audit import RequestMeta, append_audit
from app.modules.platform.deps import PlatformPrincipal
from app.modules.platform.notify import add_subscription_event, commit_and_notify
from app.modules.platform.org_service import load_org
from app.schemas.platform_orgs import (
    BulkModuleBody,
    GlobalModuleBody,
    ModuleOverrideBody,
    PlanCreate,
    PlanUpdate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _definition(key: str):
    if key not in ALL_MODULES:
        raise NotFound("Unknown module")
    return MODULES[ModuleKey(key)]


def _dependency_problem(key: str, enabling: bool, effective: frozenset) -> Optional[Dict[str, Any]]:
    """Why switching `key` would leave the organization with a broken module set, or None."""
    if enabling:
        missing = [d.value for d in _definition(key).depends_on if d.value not in effective]
        return {"reason": "requires", "modules": missing} if missing else None
    dependents = [m.key.value for m in MODULES.values() if ModuleKey(key) in m.depends_on and m.key.value in effective]
    return {"reason": "required_by", "modules": dependents} if dependents else None


# --- per-organization overrides -------------------------------------------------------------------------------------


async def set_module_override(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, org_id: uuid.UUID,
                              key: str, body: ModuleOverrideBody, meta: RequestMeta) -> None:
    definition = _definition(key)
    if definition.is_core:
        raise ValidationFailed("Core modules cannot be switched off or overridden", code="core_module")
    if body.expires_at is not None and body.expires_at <= _now():
        raise ValidationFailed("expires_at must be in the future", code="expiry_in_past")
    await load_org(db, org_id, lock=True)
    ent = await compute_entitlements(db, org_id)
    problem = _dependency_problem(key, body.enabled, ent.modules)
    if problem:
        raise Conflict("That change would break module dependencies", code="module_dependency", details=problem)

    row = await db.get(OrganizationModule, (org_id, key))
    before = {"enabled": row.enabled, "expires_at": row.expires_at.isoformat() if row.expires_at else None} if row else None
    if row is None:
        row = OrganizationModule(organization_id=org_id, module_key=key, enabled=body.enabled)
        db.add(row)
    row.enabled, row.expires_at, row.reason, row.set_by = body.enabled, body.expires_at, body.reason[:255], actor.admin.id
    add_subscription_event(db, org_id, "module_override", actor.admin.id,
                           {"module": key, "enabled": body.enabled, "reason": body.reason,
                            "expires_at": body.expires_at.isoformat() if body.expires_at else None})
    await append_audit(db, action="org.module_override", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before=before, after={"module": key, "enabled": body.enabled,
                                             "expires_at": body.expires_at.isoformat() if body.expires_at else None},
                       reason=body.reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])


async def clear_module_override(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, org_id: uuid.UUID,
                                key: str, reason: str, meta: RequestMeta) -> None:
    _definition(key)
    await load_org(db, org_id, lock=True)
    row = await db.get(OrganizationModule, (org_id, key))
    if row is None:
        raise NotFound("No override for that module")
    await db.delete(row)
    await db.flush()
    ent = await compute_entitlements(db, org_id)  # what the plan alone now says
    add_subscription_event(db, org_id, "module_override_cleared", actor.admin.id, {"module": key, "reason": reason})
    await append_audit(db, action="org.module_override_cleared", admin_id=actor.admin.id, target_type="organization",
                       target_id=org_id, before={"enabled": row.enabled}, after={"module": key, "now_enabled": key in ent.modules},
                       reason=reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])


# --- platform-wide kill switch -----------------------------------------------------------------------------------------


async def list_global_modules(db: AsyncSession) -> List[Dict[str, Any]]:
    killed = {m.module_key: m for m in (await db.scalars(select(PlatformModule).where(PlatformModule.enabled.is_(False)))).all()}
    org_count = await db.scalar(select(func.count()).select_from(Organization)) or 0
    now = _now()
    overrides = defaultdict(list)
    for o in (await db.scalars(select(OrganizationModule))).all():
        overrides[o.module_key].append(o)
    result = []
    for key, d in MODULES.items():
        k = key.value
        if d.is_core:
            with_access = org_count
        elif k in killed:
            with_access = 0
        else:
            orgs = set((await db.scalars(
                select(Subscription.organization_id).join(PlanModule, PlanModule.plan_id == Subscription.plan_id)
                .where(PlanModule.module_key == k))).all())
            for o in overrides[k]:
                if o.expires_at is not None and o.expires_at <= now:
                    continue
                (orgs.add if o.enabled else orgs.discard)(o.organization_id)
            with_access = len(orgs)
        result.append({"key": k, "name": d.name, "description": d.description, "is_core": d.is_core,
                       "depends_on": [x.value for x in d.depends_on], "globally_enabled": k not in killed,
                       "reason": killed[k].reason if k in killed else None, "organizations_with_access": with_access})
    return result


async def set_global_module(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, key: str,
                            body: GlobalModuleBody, meta: RequestMeta) -> None:
    if _definition(key).is_core:
        raise ValidationFailed("Core modules cannot be switched off", code="core_module")
    row = await db.get(PlatformModule, key)
    before = {"enabled": row.enabled} if row else {"enabled": True}
    if row is None:
        row = PlatformModule(module_key=key, enabled=body.enabled)
        db.add(row)
    row.enabled, row.reason, row.set_by = body.enabled, body.reason[:255], actor.admin.id
    org_ids = list((await db.scalars(select(Organization.id))).all())
    await append_audit(db, action="platform.module_switch", admin_id=actor.admin.id, target_type="module", target_id=key,
                       before=before, after={"enabled": body.enabled}, reason=body.reason, meta=meta)
    await commit_and_notify(db, redis_client, org_ids, everyone=True)


# --- bulk -------------------------------------------------------------------------------------------------------------------


async def bulk_module_change(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, body: BulkModuleBody,
                             meta: RequestMeta) -> Dict[str, Any]:
    key = body.module_key.value
    if _definition(key).is_core:
        raise ValidationFailed("Core modules cannot be switched off", code="core_module")
    if bool(body.organization_ids) == bool(body.plan_id):
        raise ValidationFailed("Provide exactly one of organization_ids or plan_id", code="bad_target")
    if body.expires_at is not None and body.expires_at <= _now():
        raise ValidationFailed("expires_at must be in the future", code="expiry_in_past")
    if body.plan_id:
        org_ids = list((await db.scalars(select(Subscription.organization_id).where(Subscription.plan_id == body.plan_id))).all())
    else:
        org_ids = list(dict.fromkeys(body.organization_ids or []))
        found = set((await db.scalars(select(Organization.id).where(Organization.id.in_(org_ids)))).all())
        if len(found) != len(org_ids):
            raise NotFound("One or more organizations were not found")

    changed, blocked, unchanged = [], [], 0
    for org_id in org_ids:
        ent = await compute_entitlements(db, org_id)
        now_on = key in ent.modules
        if now_on == body.enabled:
            unchanged += 1
            continue
        problem = _dependency_problem(key, body.enabled, ent.modules)
        if problem:
            blocked.append({"organization_id": str(org_id), **problem})
            continue
        changed.append({"organization_id": str(org_id), "before": now_on, "after": body.enabled})

    if not body.dry_run and changed:
        for item in changed:
            org_id = uuid.UUID(item["organization_id"])
            row = await db.get(OrganizationModule, (org_id, key))
            if row is None:
                row = OrganizationModule(organization_id=org_id, module_key=key, enabled=body.enabled)
                db.add(row)
            row.enabled, row.expires_at, row.reason, row.set_by = body.enabled, body.expires_at, body.reason[:255], actor.admin.id
            add_subscription_event(db, org_id, "module_override", actor.admin.id,
                                   {"module": key, "enabled": body.enabled, "reason": body.reason, "bulk": True})
        await append_audit(db, action="platform.module_bulk", admin_id=actor.admin.id, target_type="module", target_id=key,
                           after={"enabled": body.enabled, "organizations": [c["organization_id"] for c in changed],
                                  "blocked": len(blocked)}, reason=body.reason, meta=meta)
        await commit_and_notify(db, redis_client, [uuid.UUID(c["organization_id"]) for c in changed])
    return {"dry_run": body.dry_run, "changed": changed, "unchanged": unchanged, "blocked": blocked}


# --- plans ---------------------------------------------------------------------------------------------------------------------


async def _plan_view(db: AsyncSession, plan: Plan) -> Dict[str, Any]:
    keys = sorted((await db.scalars(select(PlanModule.module_key).where(PlanModule.plan_id == plan.id))).all())
    subscribers = await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.plan_id == plan.id)) or 0
    return {"id": plan.id, "code": plan.code, "name": plan.name, "is_active": plan.is_active,
            "max_branches": plan.max_branches, "max_staff": plan.max_staff, "currency": plan.currency,
            "price_monthly": plan.price_monthly, "price_yearly": plan.price_yearly, "module_keys": keys, "subscribers": subscribers}


def _full_module_set(keys: List[ModuleKey]) -> set:
    chosen = {k.value for k in keys}
    chosen |= {m.key.value for m in MODULES.values() if m.is_core}
    for k in list(chosen):
        missing = [d.value for d in MODULES[ModuleKey(k)].depends_on if d.value not in chosen]
        if missing:
            raise ValidationFailed(f"Module {k} requires {', '.join(missing)}", code="module_dependency")
    return chosen


async def list_plans(db: AsyncSession) -> List[Dict[str, Any]]:
    return [await _plan_view(db, p) for p in (await db.scalars(select(Plan).order_by(Plan.code))).all()]


async def create_plan(db: AsyncSession, actor: PlatformPrincipal, body: PlanCreate, meta: RequestMeta) -> Dict[str, Any]:
    modules = _full_module_set(body.module_keys)
    plan = Plan(code=body.code, name=body.name, max_branches=body.max_branches, max_staff=body.max_staff,
                currency=body.currency, price_monthly=body.price_monthly, price_yearly=body.price_yearly)
    db.add(plan)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("A plan with that code already exists", code="plan_code_taken") from exc
    db.add_all(PlanModule(plan_id=plan.id, module_key=m) for m in sorted(modules))
    await append_audit(db, action="plan.create", admin_id=actor.admin.id, target_type="plan", target_id=plan.id,
                       after={"code": plan.code, "modules": sorted(modules), "price_monthly": str(body.price_monthly),
                              "price_yearly": str(body.price_yearly)}, meta=meta)
    await db.commit()
    return await _plan_view(db, plan)


async def update_plan(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, plan_id: uuid.UUID,
                      body: PlanUpdate, meta: RequestMeta) -> Dict[str, Any]:
    plan = await db.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
    if plan is None:
        raise NotFound("Plan not found")
    before = await _plan_view(db, plan)
    changes = body.model_dump(exclude_unset=True, exclude={"reason", "module_keys"})
    for k, v in changes.items():
        setattr(plan, k, v)
    if body.module_keys is not None:
        modules = _full_module_set(body.module_keys)
        current = set(before["module_keys"])
        for key in current - modules:
            await db.delete(await db.get(PlanModule, (plan.id, key)))
        db.add_all(PlanModule(plan_id=plan.id, module_key=m) for m in sorted(modules - current))
    await db.flush()
    after = await _plan_view(db, plan)
    subscribers = list((await db.scalars(select(Subscription.organization_id).where(Subscription.plan_id == plan.id))).all())
    for org_id in subscribers:
        add_subscription_event(db, org_id, "plan_edited", actor.admin.id, {"plan": plan.code, "reason": body.reason})
    await append_audit(db, action="plan.update", admin_id=actor.admin.id, target_type="plan", target_id=plan.id,
                       before={k: str(v) for k, v in before.items()}, after={k: str(v) for k, v in after.items()},
                       reason=body.reason, meta=meta)
    await commit_and_notify(db, redis_client, subscribers)
    return after


# --- dashboard ---------------------------------------------------------------------------------------------------------------------


async def dashboard(db: AsyncSession) -> Dict[str, Any]:
    now = _now()
    by_org = Counter(dict((await db.execute(select(Organization.status, func.count()).group_by(Organization.status))).all()))
    rows = (await db.execute(
        select(Subscription, Organization.name, Organization.slug).join(Organization, Organization.id == Subscription.organization_id)
    )).all()
    by_status, by_term = Counter(), Counter()
    mrr: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    renewals, trials = [], []
    past_due = 0
    for sub, name, slug in rows:
        by_status[sub.status] += 1
        by_term[sub.billing_term] += 1
        if sub.status == "active":  # only paying customers count towards recurring revenue
            monthly = sub.price_amount if sub.billing_term == "monthly" else sub.price_amount / 12
            mrr[sub.currency] += monthly
        if sub.status == "past_due" or (sub.status == "active" and sub.current_period_end and sub.current_period_end < now):
            past_due += 1
        entry = {"organization_id": str(sub.organization_id), "name": name, "slug": slug,
                 "billing_term": sub.billing_term, "price": str(sub.price_amount), "currency": sub.currency}
        if sub.status == "active" and sub.current_period_end and now <= sub.current_period_end <= now + timedelta(days=30):
            renewals.append({**entry, "renews_at": sub.current_period_end.isoformat(), "auto_renew": sub.auto_renew})
        if sub.status == "trialing" and sub.trial_ends_at and now <= sub.trial_ends_at <= now + timedelta(days=7):
            trials.append({**entry, "trial_ends_at": sub.trial_ends_at.isoformat()})
    outstanding: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    open_count = overdue_count = 0
    for inv in (await db.scalars(select(Invoice).where(Invoice.status == "open"))).all():
        outstanding[inv.currency] += max(inv.amount_due - inv.amount_paid, ZERO)
        open_count += 1
        overdue_count += inv.due_at < now
    renewals.sort(key=lambda r: r["renews_at"])
    trials.sort(key=lambda r: r["trial_ends_at"])
    return {
        "organizations_by_status": dict(by_org), "subscriptions_by_status": dict(by_status),
        "subscriptions_by_term": dict(by_term),
        "mrr": {c: str(q(v)) for c, v in mrr.items()}, "arr": {c: str(q(v * 12)) for c, v in mrr.items()},
        "renewals_next_30_days": renewals[:50], "trials_ending_next_7_days": trials[:50], "past_due": past_due,
        "outstanding": {c: str(q(v)) for c, v in outstanding.items()}, "open_invoices": open_count, "overdue_invoices": overdue_count,
    }
