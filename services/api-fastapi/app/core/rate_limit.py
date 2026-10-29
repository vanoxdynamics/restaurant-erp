import redis.asyncio as aioredis

from app.core.config import settings
from app.core.errors import TooManyAttempts


class LoginRateLimiter:
    """Fixed-window failed-attempt counter for logins and approval PINs."""

    def __init__(self, redis_client: aioredis.Redis, *, scope: str, key: str) -> None:
        self.redis = redis_client
        self.redis_key = f"login:{scope}:{key}"

    async def check(self) -> None:
        attempts = await self.redis.get(self.redis_key)
        if attempts is not None and int(attempts) >= settings.LOGIN_MAX_ATTEMPTS:
            ttl = await self.redis.ttl(self.redis_key)
            retry_after = max(int(ttl), 1)
            raise TooManyAttempts(
                "Too many failed attempts; try again later.",
                details={"retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

    async def record_failure(self) -> bool:
        """Count a failure. Returns True when this failure tripped the lockout."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(self.redis_key)
            pipe.expire(self.redis_key, settings.LOGIN_LOCKOUT_SECONDS, nx=True)
            count, _ = await pipe.execute()
        return int(count) == settings.LOGIN_MAX_ATTEMPTS

    async def reset(self) -> None:
        await self.redis.delete(self.redis_key)
