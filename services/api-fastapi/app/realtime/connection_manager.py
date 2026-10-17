import json
from typing import Dict, List
from fastapi import WebSocket
import redis.asyncio as aioredis


class RealtimeConnectionManager:
    """
    Manages active ASGI WebSockets connections grouped by channel
    (e.g., 'branch_{id}_kots', 'branch_{id}_tables', 'branch_{id}_billing').
    Supports horizontal scaling via Redis Pub/Sub fan-out.
    """

    def __init__(self) -> None:
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, channel_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if channel_id not in self.active_connections:
            self.active_connections[channel_id] = []
        self.active_connections[channel_id].append(websocket)

    def disconnect(self, channel_id: str, websocket: WebSocket) -> None:
        if channel_id in self.active_connections:
            if websocket in self.active_connections[channel_id]:
                self.active_connections[channel_id].remove(websocket)
            if not self.active_connections[channel_id]:
                del self.active_connections[channel_id]

    async def broadcast_local(self, channel_id: str, message: dict) -> None:
        """Broadcast message to all sockets connected to this local worker instance."""
        if channel_id in self.active_connections:
            message_text = json.dumps(message)
            for connection in self.active_connections[channel_id]:
                try:
                    await connection.send_text(message_text)
                except Exception:
                    pass

    async def publish_redis(self, redis_client: aioredis.Redis, channel_id: str, message: dict) -> None:
        """Publish event to Redis pub/sub for fan-out to all backend cluster instances."""
        await redis_client.publish(f"channel:{channel_id}", json.dumps(message))


realtime_manager = RealtimeConnectionManager()
