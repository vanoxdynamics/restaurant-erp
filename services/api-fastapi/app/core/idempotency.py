from typing import Optional
from fastapi import Header, HTTPException, status
import redis.asyncio as aioredis


async def verify_idempotency_key(
    redis_client: aioredis.Redis,
    idempotency_key: Optional[str],
    scope: str = "general",
    ttl_seconds: int = 120,
) -> None:
    """
    Validates idempotency key against Redis to prevent duplicate submissions
    (e.g., duplicate billing, payment submissions, or duplicate KOT firing).
    """
    if not idempotency_key:
        return

    redis_key = f"idempotency:{scope}:{idempotency_key}"
    # Atomically set if not exists
    is_new = await redis_client.set(redis_key, "processing", nx=True, ex=ttl_seconds)
    if not is_new:
        current_state = await redis_client.get(redis_key)
        if current_state == "processing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Request already in progress with this idempotency key.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already executed with this idempotency key.",
        )


async def mark_idempotency_completed(
    redis_client: aioredis.Redis,
    idempotency_key: Optional[str],
    scope: str = "general",
    ttl_seconds: int = 86400,
) -> None:
    """Marks an idempotent operation as successfully committed."""
    if not idempotency_key:
        return
    redis_key = f"idempotency:{scope}:{idempotency_key}"
    await redis_client.set(redis_key, "completed", ex=ttl_seconds)
