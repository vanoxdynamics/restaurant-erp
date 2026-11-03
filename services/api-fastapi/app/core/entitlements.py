"""
What an organization is currently allowed to do, derived from its status, subscription, plan and module overrides.

Access levels:
    full       everything its modules allow
    read_only  reads only (subscription expired, paused, cancelled or missing; fails closed)
    blocked    nothing (the organization is suspended or cancelled by the operator)

Results are cached in Redis for a short time and dropped explicitly (`invalidate_entitlements`) whenever
an operator changes an organization, so a change takes effect on the next request.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, FrozenSet, Optional

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import BillingTerm, OrgStatus, SubscriptionStatus
from app.core.errors import Forbidden
from app.core.modules import ALL_MODULES, CORE_MODULES, resolve_dependencies
from app.models.organization import Organization
from app.models.subscription import OrganizationModule, Plan, PlanModule, PlatformModule, Subscription

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LEGACY_PLAN_CODE = "legacy_all_modules"

FULL, READ_ONLY, BLOCKED = "full", "read_only", "blocked"


def _cache_key(org_id: uuid.UUID) -> str:
    return f"entitlements:{org_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Entitlements:
    organization_id: str
    org_status: str
    access: str
    modules: FrozenSet[str]
    plan_code: Optional[str] = None
    subscription_status: Optional[str] = None  # effective (past_due once the period has lapsed)
    billing_term: Optional[str] = None
    current_period_end: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None
    grace_until: Optional[datetime] = None
    max_branches: Optional[int] = None
    max_staff: Optional[int] = None
    valid_until: Optional[datetime] = None  # next instant at which this result may change by itself

    def has_module(self, key: str) -> bool:
        return self.access != BLOCKED and key in self.modules

    def to_json(self) -> str:
        data: Dict[str, Any] = asdict(self)
        data["modules"] = sorted(self.modules)
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> "Entitlements":
        data = json.loads(raw)
        data["modules"] = frozenset(data["modules"])
        for k in ("current_period_end", "trial_ends_at", "grace_until", "valid_until"):
            if data.get(k):
                data[k] = datetime.fromisoformat(data[k])
        return cls(**data)


def _earliest(*candidates: Optional[datetime]) -> Optional[datetime]:
    future = [c for c in candidates if c is not None]
    return min(future) if future else None


def _evaluate_subscription(sub: Subscription, now: datetime) -> tuple[str, str, Optional[datetime], Optional[datetime]]:
    """Return (access, effective_status, grace_until, next_change_at) for a subscription at `now`."""
    status = sub.status
    if status in (SubscriptionStatus.CANCELLED.value, SubscriptionStatus.EXPIRED.value, SubscriptionStatus.PAUSED.value):
        return READ_ONLY, status, None, None

    if status == SubscriptionStatus.TRIALING.value:
        if sub.trial_ends_at is not None and now > sub.trial_ends_at:
            return READ_ONLY, SubscriptionStatus.EXPIRED.value, None, None
        return FULL, status, None, sub.trial_ends_at

    # active / past_due: paid through current_period_end, then a grace window.
    end = sub.current_period_end
    if end is None or now <= end:
        return FULL, status, None, end
    if sub.cancel_at_period_end:  # the customer asked to leave: no grace once the paid period is over
        return READ_ONLY, SubscriptionStatus.EXPIRED.value, None, None
    grace_until = sub.grace_until or end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
    if now <= grace_until:
        return FULL, SubscriptionStatus.PAST_DUE.value, grace_until, grace_until
    return READ_ONLY, SubscriptionStatus.EXPIRED.value, grace_until, None


async def compute_entitlements(db: AsyncSession, org_id: uuid.UUID) -> Entitlements:
    now = _now()
    org = await db.get(Organization, org_id)
    if org is None or org.status in (OrgStatus.SUSPENDED.value, OrgStatus.CANCELLED.value):
        return Entitlements(
            organization_id=str(org_id), org_status=org.status if org else OrgStatus.SUSPENDED.value,
            access=BLOCKED, modules=frozenset(),
        )

    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    if sub is None:  # no subscription on record: fail closed to core, read-only
        return Entitlements(
            organization_id=str(org_id), org_status=org.status, access=READ_ONLY, modules=CORE_MODULES,
        )

    plan = await db.get(Plan, sub.plan_id)
    plan_keys = frozenset(
        (await db.scalars(select(PlanModule.module_key).where(PlanModule.plan_id == sub.plan_id))).all()
    ) if plan is not None and plan.is_active else frozenset()

    enabled = set(plan_keys)
    expiries = []
    for override in (await db.scalars(select(OrganizationModule).where(OrganizationModule.organization_id == org_id))).all():
        if override.expires_at is not None:
            if override.expires_at <= now:
                continue  # lapsed override: the plan decides again
            expiries.append(override.expires_at)
        (enabled.add if override.enabled else enabled.discard)(override.module_key)
    # Platform-wide kill switch beats plans and overrides (but never core modules).
    killed = frozenset((await db.scalars(select(PlatformModule.module_key).where(PlatformModule.enabled.is_(False)))).all())
    # Core modules cannot be switched off; everything else must have its dependencies on.
    modules = resolve_dependencies((frozenset(enabled) - killed) | CORE_MODULES)

    access, effective_status, grace_until, change_at = _evaluate_subscription(sub, now)
    return Entitlements(
        organization_id=str(org_id),
        org_status=org.status,
        access=access,
        modules=modules,
        plan_code=plan.code if plan else None,
        subscription_status=effective_status,
        billing_term=sub.billing_term,
        current_period_end=sub.current_period_end,
        trial_ends_at=sub.trial_ends_at,
        grace_until=grace_until,
        max_branches=sub.max_branches_override if sub.max_branches_override is not None else (plan.max_branches if plan else None),
        max_staff=sub.max_staff_override if sub.max_staff_override is not None else (plan.max_staff if plan else None),
        valid_until=_earliest(change_at, *expiries),
    )


async def get_entitlements(
    db: AsyncSession, org_id: uuid.UUID, redis_client: Optional[aioredis.Redis] = None
) -> Entitlements:
    """Cached lookup. Redis trouble never blocks a request: it falls back to the database."""
    if redis_client is not None:
        try:
            raw = await redis_client.get(_cache_key(org_id))
            if raw:
                cached = Entitlements.from_json(raw)
                if cached.valid_until is None or cached.valid_until > _now():
                    return cached
        except (aioredis.RedisError, ValueError, TypeError, KeyError):
            pass

    result = await compute_entitlements(db, org_id)
    if redis_client is not None:
        ttl = settings.ENTITLEMENT_CACHE_SECONDS
        if result.valid_until is not None:
            ttl = max(1, min(ttl, int((result.valid_until - _now()).total_seconds())))
        try:
            await redis_client.set(_cache_key(org_id), result.to_json(), ex=ttl)
        except aioredis.RedisError:
            pass
    return result


async def invalidate_entitlements(redis_client: aioredis.Redis, org_id: uuid.UUID) -> None:
    """Call after any change to an organization, its subscription, plan or module overrides."""
    await redis_client.delete(_cache_key(org_id))


async def invalidate_all_entitlements(redis_client: aioredis.Redis) -> None:
    """Drop every organization's cached entitlements (platform-wide changes: kill switch, plan edits)."""
    keys = [k async for k in redis_client.scan_iter(match="entitlements:*", count=500)]
    if keys:
        await redis_client.delete(*keys)


def enforce_access(ent: Entitlements, method: str) -> None:
    if ent.access == BLOCKED:
        raise Forbidden("This organization is suspended", code="organization_suspended")
    if ent.access == READ_ONLY and method.upper() not in SAFE_METHODS:
        raise Forbidden("The subscription is not active; this account is read-only", code="subscription_inactive",
                        details={"subscription_status": ent.subscription_status})


def enforce_module(ent: Entitlements, module_key: str) -> None:
    if not ent.has_module(module_key):
        raise Forbidden("This module is not enabled for your subscription", code="module_disabled",
                        details={"module": module_key})


def enforce_limit(current: int, limit: Optional[int], what: str) -> None:
    if limit is not None and current >= limit:
        raise Forbidden(f"Your plan allows at most {limit} {what}", code="plan_limit_reached",
                        details={"limit": limit, "resource": what})


async def ensure_legacy_subscription(db: AsyncSession, org: Organization) -> Subscription:
    """Attach the all-modules, non-expiring legacy plan to an organization that has no subscription."""
    existing = await db.scalar(select(Subscription).where(Subscription.organization_id == org.id))
    if existing is not None:
        return existing
    plan = await db.scalar(select(Plan).where(Plan.code == LEGACY_PLAN_CODE))
    if plan is None:
        plan = Plan(code=LEGACY_PLAN_CODE, name="Legacy (all modules)")
        db.add(plan)
        await db.flush()
        db.add_all(PlanModule(plan_id=plan.id, module_key=key) for key in sorted(ALL_MODULES))
    sub = Subscription(
        organization_id=org.id, plan_id=plan.id, billing_term=BillingTerm.YEARLY.value,
        status=SubscriptionStatus.ACTIVE.value, price_amount=Decimal("0.00"),
    )
    db.add(sub)
    await db.flush()
    return sub
