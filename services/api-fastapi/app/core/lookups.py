import uuid
from typing import Any, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound

M = TypeVar("M")


async def get_or_404(
    db: AsyncSession,
    model: Type[M],
    obj_id: uuid.UUID,
    *options: Any,
    for_update: bool = False,
    populate_existing: bool = False,
) -> M:
    """Load a row by primary key (optionally locked / eager-loaded) or raise NotFound."""
    stmt = select(model).where(model.id == obj_id)  # type: ignore[attr-defined]
    if options:
        stmt = stmt.options(*options)
    if for_update:
        stmt = stmt.with_for_update()
    if for_update or populate_existing:
        # Refreshing from the DB would silently discard pending in-memory changes; persist them first.
        await db.flush()
        stmt = stmt.execution_options(populate_existing=True)
    obj = await db.scalar(stmt)
    if obj is None:
        raise NotFound(f"{model.__name__} not found")
    return obj
