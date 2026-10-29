"""
Idempotency guard for retry-safe writes (orders, KOT firing, bills, payments).

Route pattern:

    cached = await guard.begin()
    if cached:
        return cached.as_response()
    try:
        result = await service.do_thing(db, user, payload)   # service commits
    except Exception:
        await guard.release()
        raise
    return await guard.complete(201, ResponseSchema.model_validate(result))

The key is bound to the caller and to a fingerprint of the payload, and a completed
request replays its original status and body.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

import redis.asyncio as aioredis
from fastapi import Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.auth import CurrentUser, get_current_user
from app.core.errors import Conflict, DomainError, ValidationFailed
from app.core.redis import get_redis

PROCESSING_TTL_SECONDS = 60
COMPLETED_TTL_SECONDS = 86400


@dataclass
class CachedResponse:
    status_code: int
    body: Any

    def as_response(self) -> JSONResponse:
        return JSONResponse(self.body, status_code=self.status_code, headers={"Idempotent-Replay": "true"})


class IdempotencyGuard:
    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        scope: str,
        key: Optional[str],
        user_id: uuid.UUID,
        required: bool,
    ) -> None:
        self.redis = redis_client
        self.scope = scope
        self.key = key
        self.required = required
        self.redis_key = f"idem:{scope}:{user_id}:{key}" if key else None
        self.fingerprint: Optional[str] = None

    async def begin(self, payload: Any = None) -> Optional[CachedResponse]:
        if self.redis_key is None:
            if self.required:
                raise DomainError("X-Idempotency-Key header is required", code="idempotency_key_required")
            return None

        self.fingerprint = _fingerprint(payload)
        record = json.dumps({"state": "processing", "fingerprint": self.fingerprint})
        if await self.redis.set(self.redis_key, record, nx=True, ex=PROCESSING_TTL_SECONDS):
            return None

        raw = await self.redis.get(self.redis_key)
        if raw is None:  # expired between SET and GET; take ownership
            await self.redis.set(self.redis_key, record, ex=PROCESSING_TTL_SECONDS)
            return None
        existing = json.loads(raw)
        if existing.get("fingerprint") != self.fingerprint:
            raise ValidationFailed(
                "This idempotency key was already used with a different request body",
                code="idempotency_key_reused",
            )
        if existing.get("state") == "processing":
            raise Conflict("A request with this idempotency key is in progress", code="request_in_progress",
                           headers={"Retry-After": "1"})
        return CachedResponse(status_code=existing["status"], body=existing["body"])

    async def complete(self, status_code: int, result: BaseModel) -> JSONResponse:
        body = result.model_dump(mode="json")
        if self.redis_key is not None:
            record = {"state": "done", "fingerprint": self.fingerprint, "status": status_code, "body": body}
            await self.redis.set(self.redis_key, json.dumps(record), ex=COMPLETED_TTL_SECONDS)
        return JSONResponse(body, status_code=status_code)

    async def release(self) -> None:
        if self.redis_key is not None:
            await self.redis.delete(self.redis_key)


def _fingerprint(payload: Any) -> str:
    if isinstance(payload, BaseModel):
        data = payload.model_dump_json()
    else:
        data = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def idempotent(scope: str, *, required: bool = True) -> Callable[..., Coroutine[Any, Any, IdempotencyGuard]]:
    async def dependency(
        idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key", min_length=8, max_length=100),
        redis_client: aioredis.Redis = Depends(get_redis),
        user: CurrentUser = Depends(get_current_user),
    ) -> IdempotencyGuard:
        return IdempotencyGuard(redis_client, scope=scope, key=idempotency_key, user_id=user.id, required=required)

    return dependency
