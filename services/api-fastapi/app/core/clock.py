from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def business_date(branch_tz: str, at: Optional[datetime] = None) -> date:
    """Calendar date in the branch's local timezone (restaurant day boundary)."""
    return (at or utcnow()).astimezone(ZoneInfo(branch_tz)).date()


def financial_year(d: date) -> str:
    """Indian financial year (April–March) as a 4-digit code, e.g. 2026-10-28 -> '2627'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"
