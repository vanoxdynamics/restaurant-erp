from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.realtime.connection_manager import realtime_manager

router = APIRouter(prefix="/ws", tags=["Realtime WebSockets"])


@router.websocket("/channels/{channel_id}")
async def websocket_channel_endpoint(websocket: WebSocket, channel_id: str):
    """
    WebSocket endpoint for real-time order updates, KOT station notifications,
    and table status sync across POS tablets and Admin apps.
    """
    await realtime_manager.connect(channel_id, websocket)
    try:
        while True:
            # Keep socket alive and receive client events/pings
            data = await websocket.receive_text()
            # Echo or process incoming client events
            await websocket.send_json({"type": "ack", "data": data})
    except WebSocketDisconnect:
        realtime_manager.disconnect(channel_id, websocket)
