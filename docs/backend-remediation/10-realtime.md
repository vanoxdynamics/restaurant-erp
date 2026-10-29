# 10 — Realtime WebSockets

Fixes: F06. Depends on: 05. Parallel with: 06–09.

Files: `app/realtime/{connection_manager,router}.py`, new `app/realtime/subscriber.py`, `app/main.py` (lifespan), tests.

## Problems being fixed

- `/ws/channels/{channel_id}`: no auth, any string accepted, so any client can read any branch's
  orders/bills.
- The handler echoes whatever the client sends.
- Only `WebSocketDisconnect` is caught; any other error leaves the socket in `active_connections` forever.
- `broadcast_local` swallows send errors and never removes dead sockets; sends one at a time, so a slow
  tablet stalls the whole channel.
- `publish_redis` is never called and nothing subscribes, so with >1 uvicorn worker events only reach
  clients on the worker that handled the write.

## Design

**Channel naming:** `branch:{branch_id}:{topic}` where `topic ∈ Topic` (from `app/core/events.py`).
Route: `GET /ws/branches/{branch_id}/{topic}` (path params validated as `UUID` + `Topic`). Remove the
free-form `/ws/channels/{channel_id}` route.

**Auth:** browsers can't set headers on WebSocket, so accept the JWT as the **first message**
(`{"type":"auth","token":"..."}`) within 5 s; otherwise close with code `4401`. (Query-string tokens end
up in proxy logs — avoid.) Validate with the same logic as `get_current_user` (factor the token→user part
into a function both use). Check `require_branch_access`; topic permissions: `billing` → cashier+;
`kots` → kitchen, captain, manager, owner; others → any staff. Fail → close `4403`.

**Server → client only.** After auth the server ignores client messages except `{"type":"ping"}` → `{"type":"pong"}`.
Server also sends `{"type":"ping"}` every 25 s; close if no client message (pong/ping) in 60 s.

### `RealtimeConnectionManager`

```python
class RealtimeConnectionManager:
    def __init__(self, redis_pool): self._conns: dict[str, set[WebSocket]] = defaultdict(set)
    async def register(self, channel, ws) / unregister(channel, ws)      # sync dict ops, no await between check & mutate
    async def publish(self, branch_id, topic, event) -> None             # Redis PUBLISH only
    async def _fan_out_local(self, channel, text) -> None                # called by subscriber
```
- `_fan_out_local`: `asyncio.gather(*(self._send(ws, text) for ws in list(conns)), return_exceptions=True)`;
  `_send` wraps `ws.send_text` in `asyncio.wait_for(..., timeout=2)`; on any exception unregister that socket
  and `close()` it.
- Serialize once (`json.dumps(event, default=str)`) per message, not per socket.
- Expose `stats()` (channels, sockets) for `/health/ready` details and logs.

### `subscriber.py`

- Lifespan starts one task per worker: `pubsub = redis.pubsub(); await pubsub.psubscribe("rt:branch:*")`,
  loop `async for msg in pubsub.listen()` → `_fan_out_local(channel_without_prefix, msg["data"])`.
- Reconnect with exponential backoff (cap 10 s) on Redis errors; log each reconnect.
- On shutdown: cancel the task, `punsubscribe`, close pubsub; then close all sockets with code `1001`.
- Redis channel prefix `rt:` keeps it separate from other keys.

### Wire to events

`app/core/events.py` (file 05) `after_commit` hook calls `realtime_manager.publish(...)`. Publishing failures
are logged, never raised into the request (the DB write already committed).

### Router skeleton

```python
@router.websocket("/branches/{branch_id}/{topic}")
async def branch_stream(ws: WebSocket, branch_id: UUID, topic: Topic):
    await ws.accept()
    try:
        user = await authenticate_ws(ws)            # closes 4401/4403 and returns None on failure
        if user is None: return
        channel = f"branch:{branch_id}:{topic}"
        await manager.register(channel, ws)
        await ws.send_json({"type": "ready", "channel": channel})
        await _receive_loop(ws)                     # handles ping/pong + idle timeout
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister(channel, ws)       # guard if never registered
```

Clients reconnect and **refetch state over REST** after any reconnect — WebSocket delivery is best-effort,
not a source of truth. Put that in the module docstring and tell the app owners in the PR.

## Tests (`tests/realtime/`) — use `starlette.testclient.TestClient` websocket support with lifespan

- No auth message within 5 s → close 4401 (use a short timeout override).
- Bad token → 4401; waiter subscribing to `billing` → 4403; manager of branch A on branch B → 4403.
- Authenticated client receives an event published via `realtime_manager.publish` (goes through real Redis).
- Two `TestClient` app instances (simulated workers) sharing Redis: publish on one, received on the other.
- A socket whose `send_text` raises is removed and others still receive.
- Ping → pong; client text other than ping is ignored (no echo).
- End-to-end: firing a KOT via REST delivers `kot.fired` on `branch:{id}:kots`.

## Done when

Old route removed, tests pass, and `api-client` / frontend owners have the new URL and auth handshake
(document it in `services/api-fastapi/README.md`).
