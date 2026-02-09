"""WebSocket transport implementation for SDK client."""

import asyncio
import logging
from typing import Optional

import websockets
from websockets.asyncio.client import ClientConnection

from reachy_mini.sdk_client.transport import (
    ClientTransport,
    CloseCallback,
    MessageCallback,
)

logger = logging.getLogger(__name__)


class WebSocketClientTransport(ClientTransport):
    """WebSocket implementation of ClientTransport.

    This transport uses the websockets library to communicate with the
    daemon's streaming WebSocket endpoint.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        path: str = "/api/stream/ws",
    ) -> None:
        """Initialize the WebSocket transport.

        Args:
            host: The server host address.
            port: The server port.
            path: The WebSocket endpoint path.

        """
        self._host = host
        self._port = port
        self._path = path
        self._uri = f"ws://{host}:{port}{path}"

        self._ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task[None]] = None
        self._message_callback: Optional[MessageCallback] = None
        self._close_callback: Optional[CloseCallback] = None

    @property
    def uri(self) -> str:
        """Get the WebSocket URI."""
        return self._uri

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        if self._ws is None:
            return False
        return self._ws.close_code is None

    async def connect(self, timeout: float = 5.0) -> None:
        """Connect to the WebSocket server.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            ConnectionError: If unable to connect.

        """
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._uri),
                timeout=timeout,
            )
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("Connected to %s", self._uri)
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {self._uri}: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket server."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, message: str) -> None:
        """Send a message over the WebSocket.

        Args:
            message: The JSON message to send.

        Raises:
            ConnectionError: If not connected.

        """
        if not self._ws:
            raise ConnectionError("Not connected")
        await self._ws.send(message)

    def on_message(self, callback: MessageCallback) -> None:
        """Register callback for incoming messages.

        Args:
            callback: Async function to handle messages.

        """
        self._message_callback = callback

    def on_close(self, callback: CloseCallback) -> None:
        """Register callback for connection close.

        Args:
            callback: Async function to call when connection closes.

        """
        self._close_callback = callback

    async def _receive_loop(self) -> None:
        """Background task to receive messages and dispatch to callback."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                if self._message_callback:
                    try:
                        await self._message_callback(str(message))
                    except Exception as e:
                        logger.warning("Error in message callback: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("WebSocket receive loop ended: %s", e)
        finally:
            if self._close_callback:
                try:
                    await self._close_callback()
                except Exception as e:
                    logger.warning("Error in close callback: %s", e)
