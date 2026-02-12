"""Client-side transport interface.

Extends the StreamingTransport ABC with client-specific methods
for connection establishment (connect/disconnect).
"""

from abc import abstractmethod

from reachy_mini.daemon.streaming.transport import StreamingTransport

__all__ = ["ClientTransport"]


class ClientTransport(StreamingTransport):
    """Transport for SDK clients.

    Extends the base StreamingTransport with:
    - connect(): Establish connection to server
    - disconnect(): Clean disconnection
    - uri: Connection URI property
    """

    @abstractmethod
    async def connect(self, timeout: float = 5.0) -> None:
        """Connect to the server.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            ConnectionError: If unable to connect.

        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the server."""

    @property
    @abstractmethod
    def uri(self) -> str:
        """Get the connection URI."""

    async def close(self) -> None:
        """Close is an alias for disconnect on client transports."""
        await self.disconnect()
