from typing import Literal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_date, financial_year
from app.models.audit import DocumentCounter
from app.models.floor import Branch

DocType = Literal["order", "kot", "bill"]


async def next_daily_value(db: AsyncSession, branch: Branch, counter: str) -> int:
    """Next value of a per-branch counter that restarts every business day (e.g. "token"), inside the caller's transaction."""
    period = business_date(branch.timezone).strftime("%Y%m%d")
    return await _bump(db, branch, counter, period)


async def _bump(db: AsyncSession, branch: Branch, counter: str, period: str) -> int:
    stmt = (
        insert(DocumentCounter)
        .values(branch_id=branch.id, doc_type=counter, period=period, value=1)
        .on_conflict_do_update(
            index_elements=[DocumentCounter.branch_id, DocumentCounter.doc_type, DocumentCounter.period],
            set_={"value": DocumentCounter.value + 1},
        )
        .returning(DocumentCounter.value)
    )
    return (await db.execute(stmt)).scalar_one()


async def next_document_number(db: AsyncSession, branch: Branch, doc_type: DocType) -> str:
    """
    Allocate the next document number inside the caller's transaction.

    Orders and KOTs restart daily (branch-local date); bills run per Indian financial year,
    as GST requires a unique consecutive invoice series. A rollback releases the number,
    so the bill series has no gaps from failed requests.

    Formats: ORD-{CODE}-{YYYYMMDD}-{0001}, KOT-{CODE}-{YYYYMMDD}-{0001}, {CODE}/{FY}/{000001}.
    """
    today = business_date(branch.timezone)
    period = financial_year(today) if doc_type == "bill" else today.strftime("%Y%m%d")

    value = await _bump(db, branch, doc_type, period)

    if doc_type == "bill":
        return f"{branch.code}/{period}/{value:06d}"
    prefix = "ORD" if doc_type == "order" else "KOT"
    return f"{prefix}-{branch.code}-{period}-{value:04d}"
