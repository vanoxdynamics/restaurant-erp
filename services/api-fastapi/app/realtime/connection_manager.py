import asyncio
import json
import logging
import uuid
from collections import defaultdict
from typing import Any, Dict, Optional, Set

import redis.asyncio as aioredis
from fastapi import WebSocket

from app.core.enums import Topic
from app.core.redis import redis_client as make_redis_client

logger = logging.getLogger(__name__)

REDIS_PREFIX = "rt:"
SEND_TIMEOUT_SECONDS = 2.0


def channel_name(branch_id: uuid.UUID, topic: Topic) -> str:
    return f"branch:{branch_id}:{topic.value}"


class RealtimeConnectionManager:
    """
    Tracks WebSockets per channel ('branch:{id}:{topic}') on this worker and fans out
    events received from Redis Pub/Sub, so every worker's clients get every event.
    Delivery is best-effort: clients refetch state over REST after reconnecting.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._subscriber: Optional[asyncio.Task] = None

    def register(self, channel: str, websocket: WebSocket) -> None:
        self._connections[channel].add(websocket)

    def unregister(self, channel: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(channel)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(channel, None)

    def stats(self) -> Dict[str, int]:
        return {"channels": len(self._connections), "sockets": sum(len(s) for s in self._connections.values())}

    async def publish(self, branch_id: uuid.UUID, topic: Topic, event: Dict[str, Any]) -> None:
        """Publish to Redis; the subscriber on every worker delivers it to local sockets."""
        client = make_redis_client()
        try:
            await client.publish(REDIS_PREFIX + channel_name(branch_id, topic), json.dumps(event, default=str))
        finally:
            await client.aclose()

    async def close_branch(self, branch_prefix: str, code: int = 4403) -> None:
        """Drop every socket of one branch (all topics): the organization lost access."""
        for channel in [c for c in self._connections if c.startswith(branch_prefix)]:
            for ws in list(self._connections.get(channel, ())):
                self.unregister(channel, ws)
                try:
                    await ws.close(code=code)
                except Exception:
                    pass

    async def fan_out_local(self, channel: str, text: str) -> None:
        if '"access.revoked"' in text:
            await self.fan_out_local_only(channel, text)
            await self.close_branch(channel.rsplit(":", 1)[0] + ":")
            return
        await self.fan_out_local_only(channel, text)

    async def fan_out_local_only(self, channel: str, text: str) -> None:
        sockets = list(self._connections.get(channel, ()))
        if not sockets:
            return
        results = await asyncio.gather(*(self._send(ws, text) for ws in sockets), return_exceptions=True)
        for ws, result in zip(sockets, results, strict=True):
            if isinstance(result, BaseException):
                self.unregister(channel, ws)
                try:
                    await ws.close(code=1011)
                except Exception:
                    pass

    @staticmethod
    async def _send(ws: WebSocket, text: str) -> None:
        await asyncio.wait_for(ws.send_text(text), timeout=SEND_TIMEOUT_SECONDS)

    # --- Redis subscriber lifecycle ------------------------------------------------

    def start(self) -> None:
        if self._subscriber is None or self._subscriber.done():
            self._subscriber = asyncio.create_task(self._subscribe_forever(), name="realtime-subscriber")

    async def stop(self) -> None:
        if self._subscriber is not None:
            self._subscriber.cancel()
            try:
                await self._subscriber
            except (asyncio.CancelledError, Exception):
                pass
            self._subscriber = None
        for sockets in list(self._connections.values()):
            for ws in list(sockets):
                try:
                    await ws.close(code=1001)
                except Exception:
                    pass
        self._connections.clear()

    async def _subscribe_forever(self) -> None:
        backoff = 0.5
        while True:
            client: aioredis.Redis = make_redis_client()
            pubsub = client.pubsub()
            try:
                await pubsub.psubscribe(f"{REDIS_PREFIX}branch:*")
                backoff = 0.5
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    channel = str(message["channel"])[len(REDIS_PREFIX):]
                    await self.fan_out_local(channel, str(message["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Realtime subscriber lost Redis; reconnecting in %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                try:
                    await pubsub.aclose()
                    await client.aclose()
                except Exception:
                    pass


realtime_manager = RealtimeConnectionManager()
