"""
Subscription billing: invoices, manually recorded payments, renewals and the periodic sweep.

How money flows today (no payment gateway yet):
  * Activation and plan-change invoices describe a period that is already in effect; the operator trusts the customer
    and records the payment when it arrives.
  * Renewal invoices are issued shortly before a period ends. Recording the payment is what extends the subscription
    (and applies a scheduled term change). Unpaid renewals fall through past-due -> grace -> read-only.
  * Leftover prepaid money (downgrades, overpayments) sits in `subscription.credit_balance` and is applied to the next
    invoice automatically.
The sweep and every function here are safe to run repeatedly and concurrently.
"""

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol

import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing_periods import add_term
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.enums import InvoiceKind, InvoiceStatus, OrgStatus, PaymentMethod, SubscriptionStatus
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO, q
from app.models.invoicing import BillingNotification, Invoice, InvoicePayment
from app.models.organization import Organization
from app.models.subscription import Subscription
from app.modules.platform.audit import RequestMeta, append_audit
from app.modules.platform.notify import add_subscription_event, commit_and_notify

logger = logging.getLogger(__name__)

REMINDER_DAYS = (14, 7, 1)  # renewal reminders, counted in days before the period ends
TRIAL_REMINDER_DAYS = (3, 1)
MAX_SEND_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- notifications (outbox) ----------------------------------------------------------------------------------------


class Mailer(Protocol):
    async def send(self, to: Optional[str], subject: str, body: str) -> None: ...


class LogMailer:
    """Placeholder transport: writes messages to the log. Swap in an SMTP/API mailer without touching the engine."""

    async def send(self, to: Optional[str], subject: str, body: str) -> None:
        logger.info("BILLING MAIL to=%s subject=%s\n%s", to or "(no billing email on file)", subject, body)


async def queue_notification(db: AsyncSession, org: Organization, kind: str, dedupe_key: str, subject: str, body: str,
                             payload: Optional[Dict[str, Any]] = None) -> bool:
    """Queue a message once per `dedupe_key`. Returns False when it already exists."""
    stmt = (
        pg_insert(BillingNotification)
        .values(id=uuid.uuid4(), organization_id=org.id, kind=kind, dedupe_key=dedupe_key[:120], to_email=org.billing_email,
                subject=subject[:200], body=body, payload=payload or {})
        .on_conflict_do_nothing(index_elements=[BillingNotification.dedupe_key])
        .returning(BillingNotification.id)
    )
    return (await db.execute(stmt)).first() is not None


async def dispatch_notifications(mailer: Optional[Mailer] = None, limit: int = 200) -> Dict[str, int]:
    """Send queued messages. A failure is retried on the next run, up to MAX_SEND_ATTEMPTS."""
    mailer = mailer or LogMailer()
    sent = failed = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(
            select(BillingNotification).where(BillingNotification.sent_at.is_(None), BillingNotification.attempts < MAX_SEND_ATTEMPTS)
            .order_by(BillingNotification.created_at).limit(limit).with_for_update(skip_locked=True)
        )).all()
        for n in rows:
            try:
                await mailer.send(n.to_email, n.subject, n.body)
                n.sent_at = _now()
                sent += 1
            except Exception:
                logger.exception("Could not send billing notification %s", n.id)
                n.attempts += 1
                failed += 1
        await db.commit()
    return {"sent": sent, "failed": failed}


# --- invoices ----------------------------------------------------------------------------------------------------------


async def _next_number(db: AsyncSession) -> str:
    seq = await db.scalar(text("SELECT nextval('platform_invoice_seq')"))
    return f"INV-{_now().year}-{int(seq):06d}"


def _money(amount: Decimal, currency: str) -> str:
    return f"{currency} {q(amount):,.2f}"


async def create_invoice(
    db: AsyncSession, *, sub: Subscription, kind: InvoiceKind, amount: Decimal, term: str, period_start: datetime,
    period_end: datetime, due_at: datetime, description: str, actor_id: Optional[uuid.UUID], apply_credit: bool = True,
) -> Invoice:
    """Issue an invoice, consuming prepaid account credit first. An invoice fully covered by credit is paid at once."""
    amount = q(amount)
    credit = min(sub.credit_balance, amount) if apply_credit else ZERO
    sub.credit_balance = q(sub.credit_balance - credit)
    due = q(amount - credit)
    invoice = Invoice(
        number=await _next_number(db), organization_id=sub.organization_id, subscription_id=sub.id, kind=kind.value,
        status=InvoiceStatus.PAID.value if due == ZERO else InvoiceStatus.OPEN.value, billing_term=term,
        period_start=period_start, period_end=period_end, currency=sub.currency, amount=amount, credit_applied=credit,
        amount_due=due, amount_paid=ZERO, description=description[:255], due_at=due_at, created_by_admin=actor_id,
        paid_at=_now() if due == ZERO else None,
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def issue_period_invoice(db: AsyncSession, sub: Subscription, kind: InvoiceKind, amount: Decimal, actor_id: Optional[uuid.UUID],
                               description: str) -> Invoice:
    """Invoice for the period that is in effect now (activation, plan change)."""
    return await create_invoice(
        db, sub=sub, kind=kind, amount=amount, term=sub.billing_term, period_start=sub.current_period_start,
        period_end=sub.current_period_end or add_term(sub.current_period_start, sub.billing_term),
        due_at=_now() + timedelta(days=settings.BILLING_DUE_DAYS), description=description, actor_id=actor_id,
    )


def renewal_terms(sub: Subscription) -> tuple[str, Decimal]:
    """Term and price of the next period: a scheduled change wins over the current terms."""
    if sub.pending_billing_term:
        return sub.pending_billing_term, sub.pending_price_amount if sub.pending_price_amount is not None else sub.price_amount
    return sub.billing_term, sub.price_amount


async def find_renewal_invoice(db: AsyncSession, sub: Subscription, period_start: datetime) -> Optional[Invoice]:
    return await db.scalar(select(Invoice).where(
        Invoice.subscription_id == sub.id, Invoice.kind == InvoiceKind.RENEWAL.value,
        Invoice.period_start == period_start, Invoice.status != InvoiceStatus.VOID.value))


async def issue_renewal_invoice(db: AsyncSession, org: Organization, sub: Subscription, actor_id: Optional[uuid.UUID],
                                *, force: bool = False) -> tuple[Invoice, bool]:
    """Invoice the next period. Returns (invoice, created); an existing live invoice for that period is reused."""
    if sub.status == SubscriptionStatus.TRIALING.value:
        raise ValidationFailed("Start a paid period first: a trial has nothing to renew", code="trialing")
    if sub.current_period_end is None:
        raise ValidationFailed("The subscription has no end date, so there is nothing to renew", code="open_ended")
    start = sub.current_period_end
    existing = await find_renewal_invoice(db, sub, start)
    if existing is not None:
        return existing, False
    term, price = renewal_terms(sub)
    due = start if start > _now() else _now()
    invoice = await create_invoice(
        db, sub=sub, kind=InvoiceKind.RENEWAL, amount=price, term=term, period_start=start, period_end=add_term(start, term),
        due_at=due, description=f"{term.capitalize()} renewal", actor_id=actor_id,
    )
    await queue_notification(
        db, org, "invoice_issued", f"invoice:{invoice.id}",
        f"Invoice {invoice.number} for your {term} renewal",
        f"Hello {org.name},\n\nInvoice {invoice.number} for {_money(invoice.amount_due, invoice.currency)} covers "
        f"{start:%d %b %Y} to {invoice.period_end:%d %b %Y} and is due {invoice.due_at:%d %b %Y}.\n",
        {"invoice_id": str(invoice.id), "amount_due": str(invoice.amount_due)})
    add_subscription_event(db, org.id, "invoice_issued", actor_id, {"invoice": invoice.number, "amount_due": str(invoice.amount_due), "kind": "renewal"})
    if invoice.status == InvoiceStatus.PAID.value:
        apply_renewal(org, sub, invoice, _now())
    return invoice, True


def apply_renewal(org: Organization, sub: Subscription, invoice: Invoice, now: datetime) -> None:
    """A renewal invoice was settled: start the next period and apply any scheduled term change."""
    old_end = sub.current_period_end
    continuous = old_end is None or now <= old_end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
    if continuous:  # paid in time (or within grace): the new period follows the old one without a gap or an overlap
        start, end = invoice.period_start, invoice.period_end
    else:  # lapsed beyond grace: the customer does not get the time they were locked out for
        start, end = now, add_term(now, invoice.billing_term)
    sub.billing_term, sub.price_amount = invoice.billing_term, invoice.amount
    sub.pending_billing_term = sub.pending_price_amount = None
    sub.current_period_start, sub.current_period_end = start, end
    sub.status, sub.grace_until, sub.cancel_at_period_end = SubscriptionStatus.ACTIVE.value, None, False
    sub.version += 1
    if org.status in (OrgStatus.PAST_DUE.value, OrgStatus.TRIAL.value):
        org.status = OrgStatus.ACTIVE.value


# --- payments ----------------------------------------------------------------------------------------------------------


async def _locked_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice:
    invoice = await db.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
    if invoice is None:
        raise NotFound("Invoice not found")
    return invoice


async def record_payment(
    db: AsyncSession, redis_client: aioredis.Redis, *, actor_id: uuid.UUID, invoice_id: uuid.UUID, amount: Decimal,
    method: PaymentMethod, reference: Optional[str], received_at: datetime, note: Optional[str], meta: RequestMeta,
) -> Dict[str, Any]:
    invoice = await _locked_invoice(db, invoice_id)
    if invoice.status != InvoiceStatus.OPEN.value:
        raise Conflict(f"This invoice is {invoice.status}", code="invoice_not_open")
    if received_at > _now() + timedelta(minutes=5):
        raise ValidationFailed("The payment date cannot be in the future", code="future_payment")
    amount = q(amount)
    if reference and await db.scalar(select(InvoicePayment.id).where(InvoicePayment.invoice_id == invoice.id, InvoicePayment.reference == reference)):
        raise Conflict("A payment with that reference is already recorded on this invoice", code="duplicate_payment")

    sub = await db.scalar(select(Subscription).where(Subscription.id == invoice.subscription_id).with_for_update())
    org = await db.get(Organization, invoice.organization_id)
    payment = InvoicePayment(invoice_id=invoice.id, organization_id=invoice.organization_id, amount=amount, currency=invoice.currency,
                             method=method.value, reference=reference, received_at=received_at, note=note, recorded_by_admin=actor_id)
    db.add(payment)

    paid_before = invoice.amount_paid
    invoice.amount_paid = q(paid_before + amount)
    settled = invoice.amount_paid >= invoice.amount_due
    excess = ZERO
    renewed = False
    if settled:
        invoice.status, invoice.paid_at = InvoiceStatus.PAID.value, _now()
        excess = q(invoice.amount_paid - invoice.amount_due)
        if excess > ZERO:
            sub.credit_balance = q(sub.credit_balance + excess)  # overpayment is not lost: it pays the next invoice
        if invoice.kind == InvoiceKind.RENEWAL.value:
            apply_renewal(org, sub, invoice, _now())
            renewed = True

    add_subscription_event(db, invoice.organization_id, "payment_recorded", actor_id,
                           {"invoice": invoice.number, "amount": str(amount), "method": method.value, "reference": reference,
                            "settled": settled, "renewed": renewed})
    await append_audit(db, action="invoice.payment", admin_id=actor_id, target_type="invoice", target_id=invoice.id,
                       after={"number": invoice.number, "amount": str(amount), "method": method.value, "reference": reference,
                              "paid": str(invoice.amount_paid), "due": str(invoice.amount_due), "settled": settled, "renewed": renewed,
                              "credit_added": str(excess)}, meta=meta)
    if settled:
        await queue_notification(db, org, "payment_received", f"payment:{payment.id}", f"Payment received for {invoice.number}",
                                 f"Thank you. We received {_money(amount, invoice.currency)} for invoice {invoice.number}.\n",
                                 {"invoice_id": str(invoice.id)})
    await commit_and_notify(db, redis_client, [invoice.organization_id])
    return {"invoice": invoice, "payment": payment, "settled": settled, "renewed": renewed, "credit_added": excess}


async def void_invoice(db: AsyncSession, redis_client: aioredis.Redis, *, actor_id: uuid.UUID, invoice_id: uuid.UUID, reason: str,
                       meta: RequestMeta) -> Invoice:
    invoice = await _locked_invoice(db, invoice_id)
    if invoice.status != InvoiceStatus.OPEN.value:
        raise Conflict(f"Only open invoices can be voided; this one is {invoice.status}", code="invoice_not_open")
    if invoice.amount_paid > ZERO:
        raise Conflict("Payments are recorded against this invoice; it cannot be voided", code="has_payments")
    sub = await db.scalar(select(Subscription).where(Subscription.id == invoice.subscription_id).with_for_update())
    sub.credit_balance = q(sub.credit_balance + invoice.credit_applied)  # give back credit the invoice had consumed
    invoice.status, invoice.voided_at, invoice.void_reason = InvoiceStatus.VOID.value, _now(), reason
    add_subscription_event(db, invoice.organization_id, "invoice_voided", actor_id, {"invoice": invoice.number, "reason": reason})
    await append_audit(db, action="invoice.void", admin_id=actor_id, target_type="invoice", target_id=invoice.id,
                       after={"number": invoice.number, "credit_returned": str(invoice.credit_applied)}, reason=reason, meta=meta)
    await commit_and_notify(db, redis_client, [invoice.organization_id])
    return invoice


# --- the sweep ---------------------------------------------------------------------------------------------------------------


def _days_left(until: datetime, now: datetime) -> int:
    return math.ceil((until - now).total_seconds() / 86400)


def _sync_org(org: Organization, status: str) -> None:
    """Keep the organization's headline status in step; never touch suspended or cancelled organizations."""
    if org.status in (OrgStatus.SUSPENDED.value, OrgStatus.CANCELLED.value):
        return
    if status == SubscriptionStatus.ACTIVE.value:
        org.status = OrgStatus.ACTIVE.value
    elif status in (SubscriptionStatus.PAST_DUE.value, SubscriptionStatus.EXPIRED.value):
        org.status = OrgStatus.PAST_DUE.value


async def _process(db: AsyncSession, sub: Subscription, org: Organization, now: datetime, summary: Dict[str, int]) -> bool:
    """Apply every time-driven transition for one subscription. Returns True when its access or status changed."""
    changed = False
    end = sub.current_period_end

    if sub.status == SubscriptionStatus.TRIALING.value:
        ends = sub.trial_ends_at
        if ends is None:
            return False
        if now > ends:
            sub.status, sub.version = SubscriptionStatus.EXPIRED.value, sub.version + 1
            _sync_org(org, sub.status)
            add_subscription_event(db, org.id, "trial_expired", None, {"trial_ended": ends.isoformat()})
            await queue_notification(db, org, "trial_expired", f"trial-expired:{sub.id}", "Your trial has ended",
                                     f"Hello {org.name},\n\nYour trial ended on {ends:%d %b %Y}. Your data is safe and stays readable; "
                                     "contact us to start your subscription.\n")
            summary["trials_expired"] += 1
            return True
        left = _days_left(ends, now)
        due = [n for n in TRIAL_REMINDER_DAYS if left <= n]
        if due and await queue_notification(
                db, org, "trial_ending", f"trial-ending:{sub.id}:{ends:%Y%m%d}:{min(due)}", "Your trial is ending soon",
                f"Hello {org.name},\n\nYour trial ends on {ends:%d %b %Y} ({left} day{'s' if left != 1 else ''} left).\n"):
            summary["reminders"] += 1
        return False

    if end is None:
        return False

    if now > end:
        if sub.cancel_at_period_end:
            sub.status, sub.auto_renew, sub.version = SubscriptionStatus.CANCELLED.value, False, sub.version + 1
            add_subscription_event(db, org.id, "cancelled", None, {"at_period_end": True})
            summary["cancelled"] += 1
            return True
        grace_end = sub.grace_until or end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        if now <= grace_end:
            if sub.status == SubscriptionStatus.ACTIVE.value:
                sub.status, sub.version = SubscriptionStatus.PAST_DUE.value, sub.version + 1
                _sync_org(org, sub.status)
                add_subscription_event(db, org.id, "past_due", None, {"period_ended": end.isoformat(), "grace_until": grace_end.isoformat()})
                await queue_notification(
                    db, org, "payment_overdue", f"overdue:{sub.id}:{end:%Y%m%d}", "Your subscription payment is overdue",
                    f"Hello {org.name},\n\nYour subscription period ended on {end:%d %b %Y}. Please pay by {grace_end:%d %b %Y} "
                    "to avoid losing the ability to make changes.\n")
                summary["past_due"] += 1
                changed = True
            if sub.auto_renew and await find_renewal_invoice(db, sub, end) is None:
                invoice, _ = await issue_renewal_invoice(db, org, sub, None)
                summary["invoices_issued"] += 1
                changed = changed or invoice.status == InvoiceStatus.PAID.value  # fully covered by credit: access changed
        elif sub.status != SubscriptionStatus.EXPIRED.value:
            sub.status, sub.version = SubscriptionStatus.EXPIRED.value, sub.version + 1
            _sync_org(org, sub.status)
            add_subscription_event(db, org.id, "expired", None, {"period_ended": end.isoformat()})
            await queue_notification(
                db, org, "access_restricted", f"expired:{sub.id}:{end:%Y%m%d}", "Your account is now read-only",
                f"Hello {org.name},\n\nBecause payment was not received, your account is read-only. Pay the open invoice to restore full access.\n")
            summary["expired"] += 1
            changed = True
        return changed

    left = _days_left(end, now)
    if sub.auto_renew and not sub.cancel_at_period_end and left <= settings.BILLING_INVOICE_LEAD_DAYS \
            and await find_renewal_invoice(db, sub, end) is None:
        invoice, _ = await issue_renewal_invoice(db, org, sub, None)
        summary["invoices_issued"] += 1
        changed = invoice.status == InvoiceStatus.PAID.value
    if not sub.cancel_at_period_end:
        due = [n for n in REMINDER_DAYS if left <= n]
        if due:
            n = min(due)
            if await queue_notification(
                    db, org, "renewal_reminder", f"renewal-reminder:{sub.id}:{end:%Y%m%d}:{n}", "Your subscription renews soon",
                    f"Hello {org.name},\n\nYour {sub.billing_term} subscription renews on {end:%d %b %Y} ({left} day{'s' if left != 1 else ''}).\n"):
                summary["reminders"] += 1
    return changed


async def run_billing_sweep(now: Optional[datetime] = None, redis_client: Optional[aioredis.Redis] = None,
                            mailer: Optional[Mailer] = None, send: bool = True) -> Dict[str, int]:
    """
    One pass over every live subscription: issue renewal invoices, send reminders, and move lapsed subscriptions through
    past-due, grace and read-only. Each subscription is handled in its own transaction, so one failure never blocks the rest,
    and rows locked by another worker are skipped (SKIP LOCKED). Running it twice in a row changes nothing the second time.
    """
    from app.core.redis import redis_client as make_redis

    now = now or _now()
    summary = {k: 0 for k in ("checked", "invoices_issued", "reminders", "past_due", "expired", "cancelled", "trials_expired", "locked", "errors")}
    own_redis = redis_client is None
    redis_client = redis_client or make_redis()
    try:
        async with AsyncSessionLocal() as db:
            ids = list((await db.scalars(select(Subscription.id).where(Subscription.status.in_(
                [SubscriptionStatus.TRIALING.value, SubscriptionStatus.ACTIVE.value, SubscriptionStatus.PAST_DUE.value])))).all())
        for sid in ids:
            summary["checked"] += 1
            try:
                async with AsyncSessionLocal() as db:
                    sub = await db.scalar(select(Subscription).where(Subscription.id == sid).with_for_update(skip_locked=True))
                    if sub is None:
                        summary["locked"] += 1
                        continue
                    org = await db.get(Organization, sub.organization_id)
                    if await _process(db, sub, org, now, summary):
                        await commit_and_notify(db, redis_client, [org.id])
                    else:
                        await db.commit()  # reminders and invoices persist without telling clients anything changed
            except Exception:
                summary["errors"] += 1
                logger.exception("Billing sweep failed for subscription %s", sid)
    finally:
        if own_redis:
            await redis_client.aclose()
    if send:
        summary.update({f"mail_{k}": v for k, v in (await dispatch_notifications(mailer)).items()})
    return summary


async def list_org_invoices(db: AsyncSession, org_id: uuid.UUID) -> List[Invoice]:
    return list((await db.scalars(select(Invoice).where(Invoice.organization_id == org_id).order_by(Invoice.issued_at.desc()))).all())


# --- reading + manual issue (used by the platform API) --------------------------------------------------------------------


def invoice_view(invoice: Invoice, payments: Optional[List[InvoicePayment]] = None, org_name: Optional[str] = None) -> Dict[str, Any]:
    now = _now()
    open_ = invoice.status == InvoiceStatus.OPEN.value
    view = {c.name: getattr(invoice, c.name) for c in Invoice.__table__.columns if c.name != "created_by_admin"}
    view.update(
        organization_name=org_name,
        balance=q(max(invoice.amount_due - invoice.amount_paid, ZERO)) if open_ else ZERO,
        overdue=open_ and invoice.due_at < now,
        payments=[{c.name: getattr(p, c.name) for c in InvoicePayment.__table__.columns} for p in (payments or [])],
    )
    return view


async def get_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> Dict[str, Any]:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise NotFound("Invoice not found")
    payments = (await db.scalars(select(InvoicePayment).where(InvoicePayment.invoice_id == invoice.id).order_by(InvoicePayment.received_at))).all()
    org = await db.get(Organization, invoice.organization_id)
    return invoice_view(invoice, list(payments), org.name if org else None)


async def list_invoices(db: AsyncSession, *, org_id: Optional[uuid.UUID], status: Optional[str], overdue: bool, limit: int,
                        offset: int) -> tuple[List[Dict[str, Any]], int]:
    from sqlalchemy import func

    stmt = select(Invoice, Organization.name).join(Organization, Organization.id == Invoice.organization_id)
    if org_id:
        stmt = stmt.where(Invoice.organization_id == org_id)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if overdue:
        stmt = stmt.where(Invoice.status == InvoiceStatus.OPEN.value, Invoice.due_at < _now())
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(stmt.order_by(Invoice.issued_at.desc()).limit(limit).offset(offset))).all()
    ids = [inv.id for inv, _ in rows]
    by_invoice: Dict[uuid.UUID, List[InvoicePayment]] = {i: [] for i in ids}
    if ids:
        for p in (await db.scalars(select(InvoicePayment).where(InvoicePayment.invoice_id.in_(ids)).order_by(InvoicePayment.received_at))).all():
            by_invoice[p.invoice_id].append(p)
    return [invoice_view(inv, by_invoice[inv.id], name) for inv, name in rows], int(total)


async def issue_renewal_now(db: AsyncSession, redis_client: aioredis.Redis, *, actor_id: uuid.UUID, org_id: uuid.UUID,
                            meta: RequestMeta) -> tuple[Invoice, bool]:
    org = await db.scalar(select(Organization).where(Organization.id == org_id).with_for_update())
    if org is None:
        raise NotFound("Organization not found")
    sub = await db.scalar(select(Subscription).where(Subscription.organization_id == org_id).with_for_update())
    if sub is None:
        raise NotFound("This organization has no subscription")
    invoice, created = await issue_renewal_invoice(db, org, sub, actor_id)
    if created:
        await append_audit(db, action="invoice.issue", admin_id=actor_id, target_type="invoice", target_id=invoice.id,
                           after={"number": invoice.number, "amount_due": str(invoice.amount_due), "kind": "renewal"}, meta=meta)
    await commit_and_notify(db, redis_client, [org_id])
    return invoice, created
