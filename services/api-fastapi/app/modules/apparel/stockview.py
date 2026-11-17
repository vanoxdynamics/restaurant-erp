"""
Stock on hand as the catalogue shows it (counter search, product reads, scanner lookup). A missing stock row means nothing
on hand.
"""

import uuid
from typing import Dict, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.apparel import ApparelStock


async def on_hand(db: AsyncSession, branch_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, int]:
    ids = list(set(variant_ids))
    if not ids:
        return {}
    rows = await db.execute(select(ApparelStock.variant_id, ApparelStock.quantity)
                            .where(ApparelStock.branch_id == branch_id, ApparelStock.variant_id.in_(ids)))
    return {variant_id: quantity for variant_id, quantity in rows.tuples().all()}
