"""Read-only stock figures for the catalogue (kept apart from `stock` so the catalogue can use it without a circular import)."""

import uuid
from decimal import Decimal
from typing import Dict, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hardware import HardwareStock


async def on_hand(db: AsyncSession, branch_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, Decimal]:
    """Base units of each product in the branch; a product never stocked reads as 0."""
    ids = set(product_ids)
    if not ids:
        return {}
    rows = (await db.execute(select(HardwareStock.product_id, HardwareStock.quantity)
                             .where(HardwareStock.branch_id == branch_id, HardwareStock.product_id.in_(ids)))).all()
    found = {product_id: quantity for product_id, quantity in rows}
    return {i: found.get(i, Decimal("0")) for i in ids}
