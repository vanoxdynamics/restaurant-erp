"""
Background work run after the response via FastAPI BackgroundTasks:
- Bill of Materials (BOM) recipe inventory deductions
- Real-time mobile push alerts for managerial authorizations
- Automated End-of-Day (EOD) Z-Report generation

Each task opens its own database session (never reuse the request session) and is
idempotent. When a durable queue is needed, move these to ARQ (async, Redis-backed).
"""

import logging
import uuid
from datetime import date
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def deduct_bom_recipe_inventory(order_id: uuid.UUID) -> None:
    """Deduct raw inventory per recipe for a settled order."""
    # TODO(inventory): no inventory/BOM model exists yet; this only records the trigger.
    logger.info("BOM deduction requested for order %s (inventory module not implemented)", order_id)


async def send_manager_push_alert(
    branch_id: uuid.UUID,
    title: str,
    body: str,
    payload: Dict[str, Any],
) -> None:
    """Send a push notification to manager/owner devices (voids, high discounts)."""
    # TODO(push): no push provider configured yet; alerts are logged only.
    logger.info("Manager alert for branch %s: %s — %s %s", branch_id, title, body, payload)


async def generate_eod_daily_summary(branch_id: uuid.UUID, report_date: date) -> None:
    """Compile and archive the daily Z-Report for a branch (upsert, safe to rerun)."""
    from app.core.database import AsyncSessionLocal
    from app.modules.analytics.service import archive_z_report

    async with AsyncSessionLocal() as db:
        await archive_z_report(db, branch_id, report_date)
