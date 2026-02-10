"""Streaming transport interface.

Defines the Transport ABC that abstracts the underlying transport mechanism
(WebSocket, WebRTC data channel, etc.) from the streaming protocol logic.
Used by both server (daemon) and client (SDK) implementations.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine

# Type aliases for callbacks
MessageCallback = Callable[[str], Coroutine[Any, Any, None]]
CloseCallback = Callable[[], Coroutine[Any, Any, None]]


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
            ConnectionError: If the connection is closed.

        """

    @abstractmethod
    def on_message(self, callback: MessageCallback) -> None:
        """Register callback for incoming messages.

        The callback will be invoked for each incoming message.
        Only one callback can be registered at a time.

        Args:
            callback: Async function that receives the raw message string.

        """

    @abstractmethod
    def on_close(self, callback: CloseCallback) -> None:
        """Register callback for connection close.

        Args:
            callback: Async function called when connection closes.

        """

    @abstractmethod
    async def close(self) -> None:
        """Close the connection gracefully."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the transport is still connected."""


__all__ = ["StreamingTransport", "MessageCallback", "CloseCallback"]
