from dataclasses import dataclass
from typing import Any, List, Sequence

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


async def paginate(db: AsyncSession, stmt: Select[Any], params: PageParams) -> tuple[List[Any], int]:
    """Run `stmt` with limit/offset and return (rows, total count)."""
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows: Sequence[Any] = (await db.scalars(stmt.limit(params.limit).offset(params.offset))).all()
    return list(rows), int(total or 0)
