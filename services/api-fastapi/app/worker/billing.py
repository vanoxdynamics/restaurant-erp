"""
Billing worker: runs the renewal sweep on a schedule.

    python -m app.cli run-billing            # one pass (cron / Kubernetes CronJob)
    python -m app.cli run-billing --loop     # keep running every BILLING_SWEEP_INTERVAL_SECONDS

Several copies may run at once: each subscription is locked with SKIP LOCKED and every action is idempotent.
"""

import asyncio
import logging
import signal

from app.core.config import settings
from app.core.events import set_publisher
from app.modules.platform.billing_service import run_billing_sweep
from app.realtime import realtime_manager

logger = logging.getLogger(__name__)


async def run_once() -> dict:
    # Events go through Redis pub/sub, so API workers deliver them to connected clients even though this is a separate process.
    set_publisher(realtime_manager.publish)
    summary = await run_billing_sweep()
    logger.info("Billing sweep finished: %s", summary)
    return summary


async def run_forever(interval_seconds: int | None = None) -> None:
    interval = interval_seconds or settings.BILLING_SWEEP_INTERVAL_SECONDS
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            pass
    while not stop.is_set():
        try:
            await run_once()
        except Exception:
            logger.exception("Billing sweep crashed; will retry next interval")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
