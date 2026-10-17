from datetime import datetime
from typing import AsyncGenerator, Optional
import redis.asyncio as aioredis
from app.core.config import settings

redis_pool: Optional[aioredis.ConnectionPool] = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global redis_pool
    if redis_pool is None:
        redis_pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return redis_pool


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """Dependency that yields an async Redis client instance."""
    pool = get_redis_pool()
    client = aioredis.Redis(connection_pool=pool)
    try:
        yield client
    finally:
        await client.aclose()


async def close_redis_pool() -> None:
    """Close redis connection pool on application shutdown."""
    global redis_pool
    if redis_pool is not None:
        await redis_pool.disconnect()
        redis_pool = None


async def get_next_daily_sequence(redis_client: aioredis.Redis, prefix: str, branch_id: str) -> str:
    """
    Generates an atomic daily incremented sequence key.
    Format: {PREFIX}-{BRANCH_ID}-{YYYYMMDD}-{0001}
    Auto-expires the daily tracking key after 48 hours to preserve memory.
    """
    date_str = datetime.utcnow().strftime("%Y%m%d")
    seq_key = f"seq:{prefix}:{branch_id}:{date_str}"
    next_val = await redis_client.incr(seq_key)
    if next_val == 1:
        await redis_client.expire(seq_key, 60 * 60 * 48)  # 48 hours
    return f"{prefix}-{date_str}-{next_val:04d}"
