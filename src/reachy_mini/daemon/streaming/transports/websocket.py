"""WebSocket transport implementation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from reachy_mini.daemon.streaming.transport import (
    ConnectionClosedError,
    StreamingTransport,
)

if TYPE_CHECKING:
    from fastapi import WebSocket


logger = logging.getLogger(__name__)


class WebSocketTransport(StreamingTransport):
    """WebSocket transport for streaming protocol.

    Wraps a FastAPI WebSocket connection to implement the StreamingTransport interface.
    """

    def __init__(self, websocket: WebSocket) -> None:
        """Initialize the WebSocket transport.

        Args:
            websocket: The FastAPI WebSocket connection.

        """
        self._ws = websocket
        self._connected = True

    async def send(self, message: str) -> None:
        """Send a message to the client."""
        if not self._connected:
            raise ConnectionClosedError("WebSocket connection is closed")

        try:
            await self._ws.send_text(message)
        except Exception as e:
            self._connected = False
            raise ConnectionClosedError(f"Failed to send message: {e}") from e

    async def receive(self) -> str:
        """Receive the next message from the client."""
        if not self._connected:
            raise ConnectionClosedError("WebSocket connection is closed")

        try:
            return await self._ws.receive_text()
        except Exception as e:
            self._connected = False
            raise ConnectionClosedError(f"WebSocket closed: {e}") from e

    async def close(self) -> None:
        """Close the connection gracefully."""
        self._connected = False
        try:
            await self._ws.close()
        except Exception:
            pass

    @property
    def is_connected(self) -> bool:
        """Check if the transport is still connected."""
        return self._connected
