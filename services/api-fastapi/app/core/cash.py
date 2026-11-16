"""
Cash taken outside the shared `payments` table.

Closing a shift or the cash day counts the cash the drawer should hold. Restaurant, café and shop bills pay into `payments`,
which the staff module sums itself. The dress and hardware modules keep their own payment tables, so each registers a source
here (in its package `__init__`) and the close adds what the sources report.

A source returns **net** cash for the branch: successful cash payments minus cash paid out (refunds). It is called either
with `shift_id` (a personal shift: only that shift's cash) or with `since` (the branch's cash day: everything since it
opened).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

CashSource = Callable[..., Awaitable[Decimal]]
# async def source(db, *, branch_id: UUID, since: datetime | None, shift_id: UUID | None) -> Decimal

_sources: List[CashSource] = []


def register_cash_source(fn: CashSource) -> None:
    """Add a source once (importing a module twice must not count its cash twice)."""
    if fn not in _sources:
        _sources.append(fn)


def unregister_cash_source(fn: CashSource) -> None:
    if fn in _sources:
        _sources.remove(fn)


async def extra_cash(
    db: AsyncSession, *, branch_id: uuid.UUID, since: Optional[datetime] = None, shift_id: Optional[uuid.UUID] = None,
) -> Decimal:
    """Sum of every registered source; zero when none is registered."""
    total = Decimal("0.00")
    for source in list(_sources):
        total += Decimal(await source(db, branch_id=branch_id, since=since, shift_id=shift_id))
    return total
