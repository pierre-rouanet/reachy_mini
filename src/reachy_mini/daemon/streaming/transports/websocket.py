"""WebSocket transport implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from reachy_mini.daemon.streaming.transport import (
    CloseCallback,
    MessageCallback,
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
        self._message_callback: MessageCallback | None = None
        self._close_callback: CloseCallback | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._connected = True

    async def send(self, message: str) -> None:
        """Send a message to the client.

        Args:
            message: JSON-encoded message string.

        Raises:
            ConnectionError: If the connection is closed.

        """
        if not self._connected:
            raise ConnectionError("WebSocket connection is closed")

        try:
            await self._ws.send_text(message)
        except Exception as e:
            self._connected = False
            raise ConnectionError(f"Failed to send message: {e}") from e

    def on_message(self, callback: MessageCallback) -> None:
        """Register callback for incoming messages.

        Args:
            callback: Async function that receives the raw message string.

        """
        self._message_callback = callback

        # Start receive loop if not already running
        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self._receive_loop())

    def on_close(self, callback: CloseCallback) -> None:
        """Register callback for connection close.

        Args:
            callback: Async function called when connection closes.

        """
        self._close_callback = callback

    async def close(self) -> None:
        """Close the connection gracefully."""
        self._connected = False

        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        try:
            await self._ws.close()
        except Exception:
            pass

    @property
    def is_connected(self) -> bool:
        """Check if the transport is still connected."""
        return self._connected

    async def _receive_loop(self) -> None:
        """Receive messages and dispatch to callback."""
        try:
            while self._connected:
                try:
                    message = await self._ws.receive_text()
                    if self._message_callback is not None:
                        await self._message_callback(message)
                except Exception as e:
                    # Connection closed or error
                    logger.debug(f"WebSocket receive error: {e}")
                    break
        finally:
            self._connected = False
            if self._close_callback is not None:
                await self._close_callback()
