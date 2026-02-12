"""Streaming transport interface.

Defines the Transport ABC that abstracts the underlying transport mechanism
(WebSocket, WebRTC data channel, etc.) from the streaming protocol logic.
Used by both server (daemon) and client (SDK) implementations.
"""

from abc import ABC, abstractmethod


class ConnectionClosedError(Exception):
    """Raised when a receive is attempted on a closed connection."""


class StreamingTransport(ABC):
    """Abstract bidirectional message transport.

    Defines the core operations needed for bidirectional JSON message passing.

    Server transports (daemon): Wrap an already-connected socket.
    Client transports (SDK): Add connect()/disconnect() methods.
    """

    @abstractmethod
    async def send(self, message: str) -> None:
        """Send a JSON message.

        Args:
            message: JSON-encoded message string.

        Raises:
            ConnectionClosedError: If the connection is closed.

        """

    @abstractmethod
    async def receive(self) -> str:
        """Receive the next JSON message.

        Blocks until a message is available.

        Returns:
            The raw JSON message string.

        Raises:
            ConnectionClosedError: If the connection is closed.

        """

    @abstractmethod
    async def close(self) -> None:
        """Close the connection gracefully."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the transport is still connected."""


__all__ = ["StreamingTransport", "ConnectionClosedError"]
