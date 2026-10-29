from typing import Literal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_date, financial_year
from app.models.audit import DocumentCounter
from app.models.floor import Branch

DocType = Literal["order", "kot", "bill"]


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

    stmt = (
        insert(DocumentCounter)
        .values(branch_id=branch.id, doc_type=doc_type, period=period, value=1)
        .on_conflict_do_update(
            index_elements=[DocumentCounter.branch_id, DocumentCounter.doc_type, DocumentCounter.period],
            set_={"value": DocumentCounter.value + 1},
        )
        .returning(DocumentCounter.value)
    )
    value = (await db.execute(stmt)).scalar_one()

    if doc_type == "bill":
        return f"{branch.code}/{period}/{value:06d}"
    prefix = "ORD" if doc_type == "order" else "KOT"
    return f"{prefix}-{branch.code}-{period}-{value:04d}"
