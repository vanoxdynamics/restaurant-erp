"""End-to-end WebSocket tests: real lifespan, Redis pub/sub subscriber, REST-triggered events."""

import asyncio
import importlib

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import redis as redis_module
from app.main import app
from tests import factories
from tests.factories import auth_headers, idem

# The module itself; `app.realtime.router` is shadowed by the APIRouter re-export.
realtime_router = importlib.import_module("app.realtime.router")


@pytest.fixture
async def ws_app():
    # The TestClient runs the app on its own event loop; don't share pooled Redis connections across loops.
    await redis_module.close_redis_pool()
    with TestClient(app) as client:
        await asyncio.sleep(0.3)  # let the subscriber attach its pattern subscription
        yield client
    redis_module.redis_pool = None


def _auth(ws, user):
    ws.send_json({"type": "auth", "token": auth_headers(user)["Authorization"].split()[1]})


async def test_rejects_bad_token(ws_app, branch):
    with ws_app.websocket_connect(f"/ws/branches/{branch.id}/kots") as ws:
        ws.send_json({"type": "auth", "token": "garbage"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4401


async def test_auth_timeout(ws_app, branch, monkeypatch):
    monkeypatch.setattr(realtime_router, "AUTH_TIMEOUT_SECONDS", 0.2)
    with ws_app.websocket_connect(f"/ws/branches/{branch.id}/orders") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4401


async def test_topic_and_branch_permissions(ws_app, db, branch, other_branch, waiter):
    with ws_app.websocket_connect(f"/ws/branches/{branch.id}/billing") as ws:
        _auth(ws, waiter)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4403
    with ws_app.websocket_connect(f"/ws/branches/{other_branch.id}/orders") as ws:
        _auth(ws, waiter)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4403


async def test_ping_pong_and_no_echo(ws_app, branch, waiter):
    with ws_app.websocket_connect(f"/ws/branches/{branch.id}/orders") as ws:
        _auth(ws, waiter)
        assert ws.receive_json()["type"] == "ready"
        ws.send_text("hello")  # ignored, not echoed
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


async def test_firing_a_kot_reaches_the_station_board(ws_app, db, branch, waiter, kitchen):
    item = await factories.make_menu_item(db, station="kitchen")
    with ws_app.websocket_connect(f"/ws/branches/{branch.id}/kots") as ws:
        _auth(ws, kitchen)
        assert ws.receive_json()["type"] == "ready"

        order = ws_app.post("/api/v1/orders", headers=idem(waiter),
                            json={"order_type": "takeaway", "items": [{"item_id": str(item.id)}]})
        assert order.status_code == 201, order.text
        fired = ws_app.post("/api/v1/kots/fire", headers=idem(waiter), json={"order_id": order.json()["id"]})
        assert fired.status_code == 201, fired.text

        event = ws.receive_json()
        assert event["type"] == "kot.fired"
        assert event["entity_id"] == fired.json()[0]["id"]
        assert event["data"]["station"] == "kitchen"
