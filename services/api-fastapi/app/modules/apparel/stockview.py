"""
Stock on hand as the catalogue shows it (counter search, product reads). A missing stock row means nothing on hand.

Stock itself (`apparel_stock` and its ledger) is stage A2; until then every variant reads as zero.
"""

import uuid
from typing import Dict, Iterable

from sqlalchemy.ext.asyncio import AsyncSession


async def on_hand(db: AsyncSession, branch_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, int]:
    return {}
