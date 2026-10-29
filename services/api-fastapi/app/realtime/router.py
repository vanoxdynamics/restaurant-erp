"""
Realtime event streams.

    ws://host/ws/branches/{branch_id}/{topic}     topic ∈ approvals | orders | kots | tables | billing | catalog

Handshake: within 5 seconds of connecting, send {"type": "auth", "token": "<JWT>"}.
The server replies {"type": "ready", "channel": ...} or closes with 4401 (auth failed) /
4403 (no access to the branch or topic). After that the stream is server → client only;
clients may send {"type": "ping"} and receive {"type": "pong"}. The server pings every 25s
and closes idle connections after 60s. After any reconnect, refetch state over REST.
"""

import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import CurrentUser, can_access_branch, resolve_token
from app.core.database import AsyncSessionLocal
from app.core.enums import Role, Topic
from app.core.errors import Unauthorized
from app.realtime.connection_manager import channel_name, realtime_manager

router = APIRouter(prefix="/ws", tags=["Realtime WebSockets"])

AUTH_TIMEOUT_SECONDS = 5.0
PING_INTERVAL_SECONDS = 25.0
IDLE_TIMEOUT_SECONDS = 60.0

TOPIC_ROLES = {
    Topic.APPROVALS: {Role.OWNER, Role.MANAGER},
    Topic.BILLING: {Role.OWNER, Role.MANAGER, Role.CASHIER},
    Topic.KOTS: {Role.OWNER, Role.MANAGER, Role.CAPTAIN, Role.KITCHEN, Role.WAITER, Role.CASHIER},
}


async def authenticate_ws(websocket: WebSocket, branch_id: uuid.UUID, topic: Topic) -> Optional[CurrentUser]:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        message = json.loads(raw)
        if message.get("type") != "auth":
            raise ValueError("expected auth message")
        async with AsyncSessionLocal() as db:
            user = await resolve_token(db, message.get("token"))
    except (asyncio.TimeoutError, ValueError, TypeError, Unauthorized):
        await websocket.close(code=4401)
        return None

    allowed_roles = TOPIC_ROLES.get(topic)
    if not can_access_branch(user, branch_id) or (allowed_roles and user.role not in allowed_roles):
        await websocket.close(code=4403)
        return None
    return user


async def _keepalive(websocket: WebSocket) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_SECONDS)
        await websocket.send_json({"type": "ping"})


async def _receive_loop(websocket: WebSocket) -> None:
    while True:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await websocket.close(code=1001)
            return
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("type") == "ping":
            await websocket.send_json({"type": "pong"})


@router.websocket("/branches/{branch_id}/{topic}")
async def branch_stream(websocket: WebSocket, branch_id: uuid.UUID, topic: Topic):
    """Authenticated, server-to-client event stream for one branch topic."""
    await websocket.accept()
    channel = channel_name(branch_id, topic)
    registered = False
    keepalive: Optional[asyncio.Task] = None
    try:
        user = await authenticate_ws(websocket, branch_id, topic)
        if user is None:
            return
        realtime_manager.register(channel, websocket)
        registered = True
        await websocket.send_json({"type": "ready", "channel": channel})
        keepalive = asyncio.create_task(_keepalive(websocket))
        await _receive_loop(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        if keepalive is not None:
            keepalive.cancel()
        if registered:
            realtime_manager.unregister(channel, websocket)
