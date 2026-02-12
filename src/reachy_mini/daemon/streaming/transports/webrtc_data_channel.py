"""WebRTC data channel transport implementation.

Bridges GStreamer WebRTC data channels to the streaming protocol.
GStreamer callbacks run on the GLib main loop thread, so this transport
uses an asyncio queue to pass messages to the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from reachy_mini.daemon.streaming.transport import (
    ConnectionClosedError,
    StreamingTransport,
)

logger = logging.getLogger(__name__)


class WebRTCDataChannelTransport(StreamingTransport):
    """Transport for a single WebRTC data channel peer.

    This transport wraps the send/receive interface of a GStreamer
    WebRTC data channel. Since GStreamer signals fire on the GLib
    thread, incoming messages are passed to an asyncio queue via
    call_soon_threadsafe.
    """

    def __init__(
        self,
        peer_id: str,
        send_fn: Callable[[Optional[str], str], None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Initialize the data channel transport.

        Args:
            peer_id: The WebRTC peer ID for this channel.
            send_fn: Function to send a message (peer_id, message).
                     This is GstWebRTC.send_data_message.
            loop: The asyncio event loop to dispatch messages on.

        """
        self._peer_id = peer_id
        self._send_fn = send_fn
        self._loop = loop
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._connected = True

    @property
    def peer_id(self) -> str:
        """Get the WebRTC peer ID."""
        return self._peer_id

    @property
    def is_connected(self) -> bool:
        """Check if the data channel is still connected."""
        return self._connected

    async def send(self, message: str) -> None:
        """Send a message to the peer via the data channel."""
        if not self._connected:
            raise ConnectionClosedError(f"Data channel closed for peer {self._peer_id}")

        try:
            self._send_fn(self._peer_id, message)
        except Exception as e:
            self._connected = False
            raise ConnectionClosedError(
                f"Failed to send to peer {self._peer_id}: {e}"
            ) from e

    async def receive(self) -> str:
        """Receive the next message from the data channel."""
        message = await self._queue.get()
        if message is None:
            raise ConnectionClosedError(f"Data channel closed for peer {self._peer_id}")
        return message

    async def close(self) -> None:
        """Mark the transport as closed."""
        self._connected = False
        self._queue.put_nowait(None)

    def enqueue(self, message: str) -> None:
        """Enqueue a message from the GLib thread.

        Called from the GLib main loop thread when a data channel
        message arrives. Schedules the message on the asyncio loop.

        Args:
            message: The raw message string from the data channel.

        """
        if not self._connected:
            return

        self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    def notify_close(self) -> None:
        """Notify that the data channel has closed.

        Called from the GLib main loop thread when the data channel
        closes. Puts a sentinel in the queue to wake up receive().
        """
        self._connected = False
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
