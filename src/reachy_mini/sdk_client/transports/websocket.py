"""WebSocket transport implementation for SDK client."""

import asyncio
import logging
from typing import Optional

import websockets
from websockets.asyncio.client import ClientConnection

from reachy_mini.daemon.streaming.transport import ConnectionClosedError
from reachy_mini.sdk_client.transport import ClientTransport

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
        """Connect to the WebSocket server."""
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._uri),
                timeout=timeout,
            )
            logger.info("Connected to %s", self._uri)
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {self._uri}: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket server."""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, message: str) -> None:
        """Send a message over the WebSocket."""
        if not self._ws:
            raise ConnectionClosedError("Not connected")
        try:
            await self._ws.send(message)
        except Exception as e:
            raise ConnectionClosedError(f"Failed to send: {e}") from e

    async def receive(self) -> str:
        """Receive the next message from the WebSocket."""
        if not self._ws:
            raise ConnectionClosedError("Not connected")
        try:
            message = await self._ws.recv()
            return str(message)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise ConnectionClosedError(f"WebSocket closed: {e}") from e
