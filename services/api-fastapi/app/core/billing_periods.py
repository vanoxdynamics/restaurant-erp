"""Billing-term arithmetic: period lengths and proration for mid-period plan or term changes. Pure functions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from dateutil.relativedelta import relativedelta

from app.core.enums import BillingTerm
from app.core.money import ZERO, q

EFFECTIVE_NOW = "now"
EFFECTIVE_NEXT_RENEWAL = "next_renewal"


def add_term(start: datetime, term: str) -> datetime:
    """End of a period that starts at `start` (calendar-aware: Jan 31 + 1 month = Feb 28/29)."""
    if term == BillingTerm.MONTHLY.value:
        return start + relativedelta(months=1)
    if term == BillingTerm.YEARLY.value:
        return start + relativedelta(years=1)
    raise ValueError(f"unknown billing term {term!r}")


def unused_credit(price: Decimal, period_start: datetime, period_end: Optional[datetime], now: datetime) -> Decimal:
    """Value of the unused part of an already-paid period (0 when the period is open-ended or over)."""
    if period_end is None or now >= period_end or period_end <= period_start:
        return ZERO
    fraction = Decimal((period_end - max(now, period_start)).total_seconds()) / Decimal(
        (period_end - period_start).total_seconds())
    return q(price * min(fraction, Decimal(1)))


@dataclass(frozen=True)
class ChangePreview:
    effective: str
    effective_at: datetime
    current_term: str
    new_term: str
    current_price: Decimal
    new_price: Decimal
    credit_for_unused_time: Decimal
    amount_due_now: Decimal
    account_credit_remaining: Decimal
    new_period_start: datetime
    new_period_end: datetime

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, datetime) else v)
                for k, v in self.__dict__.items()}


def compute_change(
    *, now: datetime, effective: str, current_term: str, current_price: Decimal,
    period_start: datetime, period_end: Optional[datetime], paid: bool, new_term: str, new_price: Decimal,
) -> ChangePreview:
    """
    effective="now": the new term starts immediately; the unused part of the paid period is credited against it.
    effective="next_renewal": nothing changes until the current period ends; no money moves today.
    `paid` is False while trialing (nothing was paid, so there is nothing to credit).
    """
    if effective == EFFECTIVE_NEXT_RENEWAL and period_end is not None and period_end > now:
        start = period_end
        return ChangePreview(effective, start, current_term, new_term, current_price, new_price, ZERO, ZERO, ZERO,
                             start, add_term(start, new_term))
    credit = unused_credit(current_price, period_start, period_end, now) if paid else ZERO
    due = q(max(new_price - credit, ZERO))
    leftover = q(max(credit - new_price, ZERO))
    return ChangePreview(EFFECTIVE_NOW, now, current_term, new_term, current_price, new_price, credit, due, leftover,
                         now, add_term(now, new_term))
