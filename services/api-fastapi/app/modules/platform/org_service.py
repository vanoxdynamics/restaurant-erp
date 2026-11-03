"""Subscriber (organization) lifecycle and subscription management for platform operators."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import token_claims
from app.core.billing_periods import EFFECTIVE_NEXT_RENEWAL, ChangePreview, add_term, compute_change
from app.core.config import settings
from app.core.entitlements import compute_entitlements
from app.core.enums import BillingTerm, InvoiceKind, OrgStatus, Role, SubscriptionStatus
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.modules import MODULES
from app.core.money import q
from app.core.security import compute_pin_lookup, create_access_token, get_password_hash, get_pin_hash
from app.models.floor import Branch
from app.models.invoicing import Invoice
from app.models.organization import Organization
from app.models.staff import StaffUser
from app.models.subscription import OrganizationModule, Plan, PlanModule, PlatformModule, Subscription, SubscriptionEvent
from app.modules.platform import billing_service
from app.core.enums import AlertSeverity
from app.modules.platform.alerts import raise_alert
from app.modules.platform.audit import RequestMeta, append_audit
from app.modules.platform.deps import PlatformPrincipal
from app.modules.platform.notify import add_subscription_event, commit_and_notify
from app.schemas.platform_orgs import (
    ActivateBody,
    CancelBody,
    ExtendBody,
    ImpersonateBody,
    ImpersonationToken,
    OrganizationCreate,
    OrganizationUpdate,
    SubscriptionChange,
    SubscriptionUpdate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- loading ---------------------------------------------------------------------------------------


async def load_org(db: AsyncSession, org_id: uuid.UUID, *, lock: bool = False) -> Organization:
    stmt = select(Organization).where(Organization.id == org_id)
    org = await db.scalar(stmt.with_for_update() if lock else stmt)
    if org is None:
        raise NotFound("Organization not found")
    return org


async def load_subscription(db: AsyncSession, org_id: uuid.UUID, *, lock: bool = False) -> Subscription:
    stmt = select(Subscription).where(Subscription.organization_id == org_id)
    sub = await db.scalar(stmt.with_for_update() if lock else stmt)
    if sub is None:
        raise NotFound("This organization has no subscription")
    return sub


def check_version(sub: Subscription, version: int) -> None:
    if sub.version != version:
        raise Conflict("The subscription changed since you loaded it; reload and retry", code="stale_version",
                       details={"current_version": sub.version})


def plan_price(plan: Plan, term: str) -> Optional[Decimal]:
    return plan.price_monthly if term == BillingTerm.MONTHLY.value else plan.price_yearly


async def _load_plan(db: AsyncSession, plan_id: uuid.UUID, *, must_be_active: bool = True) -> Plan:
    plan = await db.get(Plan, plan_id)
    if plan is None or (must_be_active and not plan.is_active):
        raise NotFound("Plan not found")
    return plan


def _sub_snapshot(sub: Subscription) -> Dict[str, Any]:
    return {
        "plan_id": str(sub.plan_id), "billing_term": sub.billing_term, "status": sub.status,
        "price_amount": str(sub.price_amount), "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "auto_renew": sub.auto_renew, "cancel_at_period_end": sub.cancel_at_period_end,
        "pending_billing_term": sub.pending_billing_term, "version": sub.version,
        "max_branches_override": sub.max_branches_override, "max_staff_override": sub.max_staff_override,
    }


# --- create ----------------------------------------------------------------------------------------


async def create_organization(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal,
                              payload: OrganizationCreate, meta: RequestMeta) -> Tuple[Organization, Optional[Invoice]]:
    plan = await _load_plan(db, payload.plan_id)
    term = payload.billing_term.value
    price = payload.price_amount if payload.price_amount is not None else plan_price(plan, term)
    if price is None:
        raise ValidationFailed(f"The plan has no {term} price; set price_amount", code="price_required")

    now = _now()
    trial = payload.start == "trial"
    trial_ends = now + timedelta(days=payload.trial_days or settings.TRIAL_DAYS) if trial else None
    org = Organization(
        name=payload.name, slug=payload.slug, legal_name=payload.legal_name,
        billing_email=payload.billing_email.lower() if payload.billing_email else None, country=payload.country,
        status=OrgStatus.TRIAL.value if trial else OrgStatus.ACTIVE.value,
    )
    db.add(org)
    try:
        await db.flush()
        sub = Subscription(
            organization_id=org.id, plan_id=plan.id, billing_term=term, price_amount=q(price), currency=plan.currency,
            status=SubscriptionStatus.TRIALING.value if trial else SubscriptionStatus.ACTIVE.value,
            started_at=now, current_period_start=now,
            current_period_end=trial_ends if trial else add_term(now, term), trial_ends_at=trial_ends,
        )
        db.add(sub)
        owner = payload.owner
        branch = Branch(organization_id=org.id, code=owner.branch_code, name=owner.branch_name)
        branch.floors = []
        db.add(branch)
        await db.flush()
        db.add(StaffUser(
            organization_id=org.id, branch_id=branch.id, username=owner.username, full_name=owner.full_name,
            role=Role.OWNER.value, hashed_password=get_password_hash(owner.password.get_secret_value()),
            hashed_pin=get_pin_hash(owner.pin), pin_lookup=compute_pin_lookup(branch.id, owner.pin),
        ))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        text = str(exc.orig)
        if "organizations" in text or "slug" in text:
            raise Conflict("That slug is already taken", code="slug_taken") from exc
        if "branches" in text or "code" in text:
            raise Conflict("That branch code is already in use", code="branch_code_taken") from exc
        raise Conflict("That owner username is already taken", code="username_taken") from exc

    invoice = None
    if not trial:  # paid from day one: bill the first period (a trial is invoiced when it is activated)
        invoice = await billing_service.issue_period_invoice(db, sub, InvoiceKind.ACTIVATION, price, actor.admin.id, f"{term.capitalize()} subscription")
    add_subscription_event(db, org.id, "created", actor.admin.id,
                           {"plan": plan.code, "term": term, "price": str(q(price)), "start": payload.start,
                            "invoice": invoice.number if invoice else None})
    await append_audit(db, action="org.create", admin_id=actor.admin.id, target_type="organization", target_id=org.id,
                       after={"name": org.name, "slug": org.slug, "plan": plan.code, "term": term, "start": payload.start,
                              "owner_username": payload.owner.username}, meta=meta)
    await commit_and_notify(db, redis_client, [org.id])
    return org, invoice


# --- read ------------------------------------------------------------------------------------------


async def list_organizations(
    db: AsyncSession, *, q_text: Optional[str], status: Optional[str], plan_id: Optional[uuid.UUID],
    term: Optional[str], sub_status: Optional[str], expiring_within_days: Optional[int], limit: int, offset: int,
) -> Tuple[List[Dict[str, Any]], int]:
    stmt = (
        select(Organization, Subscription, Plan.code)
        .outerjoin(Subscription, Subscription.organization_id == Organization.id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
    )
    if q_text:
        escaped = q_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # % and _ are literal characters here
        like = f"%{escaped}%"
        stmt = stmt.where(or_(func.lower(Organization.name).like(like, escape="\\"), func.lower(Organization.slug).like(like, escape="\\"),
                              func.lower(Organization.billing_email).like(like, escape="\\")))
    if status:
        stmt = stmt.where(Organization.status == status)
    if plan_id:
        stmt = stmt.where(Subscription.plan_id == plan_id)
    if term:
        stmt = stmt.where(Subscription.billing_term == term)
    if sub_status:
        stmt = stmt.where(Subscription.status == sub_status)
    if expiring_within_days is not None:
        stmt = stmt.where(and_(Subscription.current_period_end.is_not(None),
                               Subscription.current_period_end <= _now() + timedelta(days=expiring_within_days)))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(stmt.order_by(Organization.created_at.desc()).limit(limit).offset(offset))).all()
    items = [
        {"id": o.id, "name": o.name, "slug": o.slug, "status": o.status, "billing_email": o.billing_email,
         "created_at": o.created_at, "plan_code": code, "billing_term": s.billing_term if s else None,
         "subscription_status": s.status if s else None, "current_period_end": s.current_period_end if s else None}
        for o, s, code in rows
    ]
    return items, int(total)


async def module_states(db: AsyncSession, org_id: uuid.UUID, ent=None) -> List[Dict[str, Any]]:
    """Per-module effective state and why: core, plan, override, or switched off (and by whom)."""
    ent = ent or await compute_entitlements(db, org_id)
    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    plan_keys = set((await db.scalars(select(PlanModule.module_key).where(PlanModule.plan_id == sub.plan_id))).all()) if sub else set()
    overrides = {o.module_key: o for o in (await db.scalars(
        select(OrganizationModule).where(OrganizationModule.organization_id == org_id))).all()}
    killed = set((await db.scalars(select(PlatformModule.module_key).where(PlatformModule.enabled.is_(False)))).all())
    now = _now()
    states = []
    for key, definition in MODULES.items():
        k = key.value
        ov = overrides.get(k)
        live_ov = ov if ov and (ov.expires_at is None or ov.expires_at > now) else None
        if definition.is_core:
            source = "core"
        elif k in killed:
            source = "platform_disabled"
        elif live_ov is not None:
            source = "override" if live_ov.enabled else "off"
        elif k in plan_keys:
            source = "plan"
        else:
            source = "off"
        enabled = k in ent.modules
        if not enabled and source in ("plan", "override"):
            source = "dependency_missing"
        states.append({
            "key": k, "name": definition.name, "is_core": definition.is_core, "enabled": enabled, "source": source,
            "override": ({"enabled": ov.enabled, "reason": ov.reason, "expires_at": ov.expires_at.isoformat() if ov.expires_at else None,
                          "active": live_ov is not None} if ov else None),
        })
    return states


async def organization_detail(db: AsyncSession, org_id: uuid.UUID) -> Dict[str, Any]:
    org = await load_org(db, org_id)
    ent = await compute_entitlements(db, org_id)
    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    plan = await db.get(Plan, sub.plan_id) if sub else None
    events = (await db.scalars(
        select(SubscriptionEvent).where(SubscriptionEvent.organization_id == org_id)
        .order_by(SubscriptionEvent.created_at.desc()).limit(20))).all()
    sub_view = None
    if sub:
        sub_view = {c.name: getattr(sub, c.name) for c in Subscription.__table__.columns if c.name != "organization_id"}
        sub_view.update(plan_code=plan.code if plan else None, effective_status=ent.subscription_status)
    return {
        "id": org.id, "name": org.name, "slug": org.slug, "legal_name": org.legal_name, "billing_email": org.billing_email,
        "country": org.country, "status": org.status, "suspended_at": org.suspended_at,
        "suspension_reason": org.suspension_reason, "created_at": org.created_at, "subscription": sub_view,
        "access": ent.access, "modules": await module_states(db, org_id, ent),
        "branches": await db.scalar(select(func.count()).select_from(Branch).where(Branch.organization_id == org_id)) or 0,
        "active_staff": await db.scalar(select(func.count()).select_from(StaffUser).where(
            StaffUser.organization_id == org_id, StaffUser.is_active.is_(True))) or 0,
        "recent_events": [{"type": e.event_type, "at": e.created_at.isoformat(), "by": str(e.actor_admin_id) if e.actor_admin_id else None,
                           "data": e.data} for e in events],
    }


# --- profile / lifecycle ---------------------------------------------------------------------------------


async def update_organization(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal,
                              org_id: uuid.UUID, payload: OrganizationUpdate, meta: RequestMeta) -> None:
    org = await load_org(db, org_id, lock=True)
    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})
    before = {k: getattr(org, k) for k in changes}
    for k, v in changes.items():
        setattr(org, k, v.lower() if k == "billing_email" and v else v)
    await append_audit(db, action="org.update", admin_id=actor.admin.id, target_type="organization", target_id=org.id,
                       before=before, after=changes, reason=payload.reason, meta=meta)
    await db.commit()


async def suspend_organization(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal,
                               org_id: uuid.UUID, reason: str, meta: RequestMeta) -> None:
    org = await load_org(db, org_id, lock=True)
    if org.status == OrgStatus.SUSPENDED.value:
        raise Conflict("The organization is already suspended", code="already_suspended")
    if org.status == OrgStatus.CANCELLED.value:
        raise Conflict("The organization is cancelled", code="already_cancelled")
    previous = org.status
    org.status, org.suspended_at, org.suspension_reason = OrgStatus.SUSPENDED.value, _now(), reason
    # Revoke every tenant session at once: tokens carry the user's token_version.
    revoked = await db.execute(update(StaffUser).where(StaffUser.organization_id == org_id)
                               .values(token_version=StaffUser.token_version + 1))
    add_subscription_event(db, org_id, "suspended", actor.admin.id, {"reason": reason, "previous_status": previous})
    await append_audit(db, action="org.suspend", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before={"status": previous}, after={"status": org.status, "sessions_revoked": revoked.rowcount},
                       reason=reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id], revoked=True)


async def reinstate_organization(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal,
                                 org_id: uuid.UUID, reason: str, meta: RequestMeta) -> None:
    org = await load_org(db, org_id, lock=True)
    if org.status != OrgStatus.SUSPENDED.value:
        raise Conflict("The organization is not suspended", code="not_suspended")
    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    org.status = OrgStatus.TRIAL.value if sub and sub.status == SubscriptionStatus.TRIALING.value else OrgStatus.ACTIVE.value
    org.suspended_at = org.suspension_reason = None
    add_subscription_event(db, org_id, "reinstated", actor.admin.id, {"reason": reason})
    await append_audit(db, action="org.reinstate", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before={"status": OrgStatus.SUSPENDED.value}, after={"status": org.status}, reason=reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])


async def cancel_subscription(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal,
                              org_id: uuid.UUID, payload: CancelBody, meta: RequestMeta) -> Subscription:
    await load_org(db, org_id, lock=True)
    sub = await load_subscription(db, org_id, lock=True)
    if sub.status == SubscriptionStatus.CANCELLED.value:
        raise Conflict("The subscription is already cancelled", code="already_cancelled")
    before = _sub_snapshot(sub)
    sub.auto_renew = False
    sub.pending_billing_term = sub.pending_price_amount = None
    if payload.at_period_end and sub.current_period_end and sub.current_period_end > _now():
        sub.cancel_at_period_end = True
    else:  # immediate: read-only from now on (data stays readable and exportable)
        sub.status = SubscriptionStatus.CANCELLED.value
        sub.cancel_at_period_end = False
    sub.version += 1
    add_subscription_event(db, org_id, "cancelled", actor.admin.id,
                           {"reason": payload.reason, "at_period_end": sub.cancel_at_period_end})
    await append_audit(db, action="subscription.cancel", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before=before, after=_sub_snapshot(sub), reason=payload.reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])
    return sub


async def activate_subscription(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal,
                                org_id: uuid.UUID, payload: ActivateBody, meta: RequestMeta) -> Invoice:
    """Start (or restart) a paid period now: converts a trial, or revives an expired/cancelled subscription."""
    org = await load_org(db, org_id, lock=True)
    sub = await load_subscription(db, org_id, lock=True)
    if org.status == OrgStatus.SUSPENDED.value:
        raise Conflict("Reinstate the organization first", code="org_suspended")
    plan = await _load_plan(db, sub.plan_id, must_be_active=False)
    price = payload.price_amount if payload.price_amount is not None else (sub.price_amount if sub.price_amount > 0 else plan_price(plan, sub.billing_term))
    if price is None:
        raise ValidationFailed("No price is set for this term; provide price_amount", code="price_required")
    before = _sub_snapshot(sub)
    now = _now()
    sub.status, sub.trial_ends_at, sub.grace_until = SubscriptionStatus.ACTIVE.value, None, None
    sub.price_amount, sub.current_period_start, sub.current_period_end = q(price), now, add_term(now, sub.billing_term)
    sub.auto_renew, sub.cancel_at_period_end = True, False
    sub.version += 1
    if org.status != OrgStatus.SUSPENDED.value:
        org.status = OrgStatus.ACTIVE.value
    invoice = await billing_service.issue_period_invoice(db, sub, InvoiceKind.ACTIVATION, price, actor.admin.id, f"{sub.billing_term.capitalize()} subscription")
    add_subscription_event(db, org_id, "activated", actor.admin.id,
                           {"reason": payload.reason, "amount_due": str(invoice.amount_due), "invoice": invoice.number})
    await append_audit(db, action="subscription.activate", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before=before, after=_sub_snapshot(sub), reason=payload.reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])
    return invoice


# --- term / plan / price changes ---------------------------------------------------------------------------


async def _resolve_change(db: AsyncSession, sub: Subscription, change: SubscriptionChange) -> Tuple[Plan, str, Decimal, ChangePreview]:
    if change.effective == EFFECTIVE_NEXT_RENEWAL and change.plan_id and change.plan_id != sub.plan_id:
        raise ValidationFailed("Plan changes apply immediately; schedule only term or price changes",
                               code="plan_change_immediate")
    plan = await _load_plan(db, change.plan_id or sub.plan_id, must_be_active=change.plan_id is not None)
    term = change.billing_term.value if change.billing_term else sub.billing_term
    if change.price_amount is not None:
        price = change.price_amount
    elif term == sub.billing_term and plan.id == sub.plan_id:
        price = sub.price_amount
    else:
        price = plan_price(plan, term)
        if price is None:
            raise ValidationFailed(f"The plan has no {term} price; set price_amount", code="price_required")
    preview = compute_change(
        now=_now(), effective=change.effective, current_term=sub.billing_term, current_price=sub.price_amount,
        period_start=sub.current_period_start, period_end=sub.current_period_end,
        paid=sub.status != SubscriptionStatus.TRIALING.value, new_term=term, new_price=q(price),
    )
    return plan, term, q(price), preview


async def preview_change(db: AsyncSession, org_id: uuid.UUID, change: SubscriptionChange) -> ChangePreview:
    sub = await load_subscription(db, org_id)
    return (await _resolve_change(db, sub, change))[3]


async def update_subscription(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, org_id: uuid.UUID,
                              payload: SubscriptionUpdate, meta: RequestMeta) -> Tuple[Subscription, Optional[ChangePreview], Optional[Invoice]]:
    await load_org(db, org_id, lock=True)
    sub = await load_subscription(db, org_id, lock=True)
    check_version(sub, payload.version)
    before = _sub_snapshot(sub)
    preview: Optional[ChangePreview] = None
    invoice: Optional[Invoice] = None
    events: List[Tuple[str, Dict[str, Any]]] = []

    if payload.plan_id or payload.billing_term or payload.price_amount is not None:
        plan, term, price, preview = await _resolve_change(db, sub, payload)
        if payload.plan_id and payload.plan_id != sub.plan_id:
            events.append(("plan_changed", {"from": str(sub.plan_id), "to": str(plan.id)}))
            sub.plan_id = plan.id
            sub.currency = plan.currency
        trialing = sub.status == SubscriptionStatus.TRIALING.value
        if preview.effective == EFFECTIVE_NEXT_RENEWAL:
            sub.pending_billing_term, sub.pending_price_amount = term, price
            events.append(("term_change_scheduled", {"to": term, "price": str(price), "at": preview.effective_at.isoformat()}))
        else:
            if term != sub.billing_term:
                events.append(("term_changed", preview.as_dict()))
            elif price != sub.price_amount:
                events.append(("price_changed", {"from": str(sub.price_amount), "to": str(price)}))
            sub.billing_term, sub.price_amount = term, price
            sub.pending_billing_term = sub.pending_price_amount = None
            if not trialing:  # a paid change restarts the period; a trial keeps running
                sub.current_period_start, sub.current_period_end = preview.new_period_start, preview.new_period_end
                sub.grace_until = None
                if sub.status in (SubscriptionStatus.PAST_DUE.value, SubscriptionStatus.EXPIRED.value):
                    sub.status = SubscriptionStatus.ACTIVE.value
                # Money side of the proration: invoice what is owed now, keep what is left over as account credit.
                if preview.account_credit_remaining > 0:
                    sub.credit_balance = q(sub.credit_balance + preview.account_credit_remaining)
                if preview.amount_due_now > 0:
                    invoice = await billing_service.issue_period_invoice(
                        db, sub, InvoiceKind.PLAN_CHANGE, preview.amount_due_now, actor.admin.id,
                        f"Change to {term} billing (prorated)")
                    events.append(("invoice_issued", {"invoice": invoice.number, "amount_due": str(invoice.amount_due)}))

    if payload.auto_renew is not None and payload.auto_renew != sub.auto_renew:
        sub.auto_renew = payload.auto_renew
        events.append(("auto_renew_changed", {"auto_renew": payload.auto_renew}))
    if payload.clear_limit_overrides:
        sub.max_branches_override = sub.max_staff_override = None
        events.append(("limits_changed", {"cleared": True}))
    elif payload.max_branches_override is not None or payload.max_staff_override is not None:
        if payload.max_branches_override is not None:
            sub.max_branches_override = payload.max_branches_override
        if payload.max_staff_override is not None:
            sub.max_staff_override = payload.max_staff_override
        events.append(("limits_changed", {"max_branches": sub.max_branches_override, "max_staff": sub.max_staff_override}))
    if not events:
        raise ValidationFailed("Nothing to change", code="no_changes")

    sub.version += 1
    for event_type, data in events:
        add_subscription_event(db, org_id, event_type, actor.admin.id, {**data, "reason": payload.reason})
    await append_audit(db, action="subscription.update", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before=before, after=_sub_snapshot(sub), reason=payload.reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])
    return sub, preview, invoice


async def extend_subscription(db: AsyncSession, redis_client: aioredis.Redis, actor: PlatformPrincipal, org_id: uuid.UUID,
                              payload: ExtendBody, meta: RequestMeta) -> Subscription:
    await load_org(db, org_id, lock=True)
    sub = await load_subscription(db, org_id, lock=True)
    check_version(sub, payload.version)
    if sub.current_period_end is None:
        raise ValidationFailed("The subscription has no end date to extend", code="open_ended")
    before = _sub_snapshot(sub)
    now = _now()
    sub.current_period_end = max(sub.current_period_end, now) + timedelta(days=payload.days)
    if sub.status == SubscriptionStatus.TRIALING.value:
        sub.trial_ends_at = sub.current_period_end
    elif sub.status in (SubscriptionStatus.PAST_DUE.value, SubscriptionStatus.EXPIRED.value):
        sub.status = SubscriptionStatus.ACTIVE.value
    sub.grace_until = None
    sub.version += 1
    add_subscription_event(db, org_id, "extended", actor.admin.id, {"days": payload.days, "reason": payload.reason,
                                                                    "new_end": sub.current_period_end.isoformat()})
    await append_audit(db, action="subscription.extend", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       before=before, after=_sub_snapshot(sub), reason=payload.reason, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])
    return sub


# --- support impersonation ----------------------------------------------------------------------------------------


async def impersonate(db: AsyncSession, actor: PlatformPrincipal, org_id: uuid.UUID, payload: ImpersonateBody,
                      meta: RequestMeta) -> ImpersonationToken:
    """A short, read-only tenant token for support. Every use is audited and the token cannot write."""
    await load_org(db, org_id)
    stmt = select(StaffUser).where(StaffUser.organization_id == org_id, StaffUser.is_active.is_(True))
    if payload.staff_id:
        stmt = stmt.where(StaffUser.id == payload.staff_id)
    else:
        stmt = stmt.where(StaffUser.role == Role.OWNER.value).order_by(StaffUser.created_at)
    target = (await db.scalars(stmt.limit(1))).first()
    if target is None:
        raise NotFound("No matching active staff member in this organization")
    ttl = timedelta(minutes=payload.minutes)
    token = create_access_token(target.id, claims={**token_claims(target, "pwd"), "imp": str(actor.admin.id)}, expires_delta=ttl)
    await append_audit(db, action="org.impersonate", admin_id=actor.admin.id, target_type="organization", target_id=org_id,
                       after={"staff_id": str(target.id), "username": target.username, "minutes": payload.minutes},
                       reason=payload.reason, meta=meta)
    await raise_alert(db, "org.impersonation", AlertSeverity.INFO, f"{actor.admin.email} opened a read-only support session on organization {org_id}",
                      {"by": actor.admin.email, "organization_id": org_id, "reason": payload.reason})
    add_subscription_event(db, org_id, "impersonated", actor.admin.id, {"staff": target.username, "reason": payload.reason})
    await db.commit()
    return ImpersonationToken(access_token=token, expires_in=int(ttl.total_seconds()), staff_id=target.id, username=target.username)

