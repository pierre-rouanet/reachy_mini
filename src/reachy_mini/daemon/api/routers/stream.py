"""Unified streaming API endpoint.

This provides a single WebSocket endpoint that handles all commands
and streams all events, as defined in the streaming protocol (messages.py).

Commands (client -> server): target, goto, set_mode, cancel, subscribe, get_status
Events (server -> client): state, goto_started, goto_done, mode_changed, cancelled, error, status
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from reachy_mini.daemon.streaming import WebSocketTransport

if TYPE_CHECKING:
    from reachy_mini.daemon.daemon import Daemon

router = APIRouter(prefix="/stream")


def _get_daemon(websocket: WebSocket) -> Daemon:
    """Get the daemon instance from websocket app state."""
    daemon: Daemon = websocket.app.state.daemon
    return daemon


@router.websocket("/ws")
async def unified_stream(websocket: WebSocket) -> None:
    """Unified WebSocket endpoint for bidirectional streaming.

    Handles all commands (target, goto, set_mode, cancel, subscribe, get_status)
    and streams events (state, goto_started, goto_done, mode_changed, etc.).
    """
    daemon = _get_daemon(websocket)
    motor_controller = daemon.motor_controller

    if motor_controller is None or not motor_controller.ready.is_set():
        await websocket.close(code=1013, reason="Motor controller not ready")
        return

    await websocket.accept()

    transport = WebSocketTransport(websocket)
    session = daemon.streaming_manager.create_session(transport)

    try:
        await session.run()
    except WebSocketDisconnect:
        pass
