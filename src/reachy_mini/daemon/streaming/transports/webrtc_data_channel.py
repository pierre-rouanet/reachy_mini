"""WebRTC data channel transport implementation.

Bridges GStreamer WebRTC data channels to the streaming protocol.
GStreamer callbacks run on the GLib main loop thread, so this transport
uses call_soon_threadsafe to dispatch messages to the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from reachy_mini.daemon.streaming.transport import (
    CloseCallback,
    MessageCallback,
    StreamingTransport,
)

logger = logging.getLogger(__name__)


class WebRTCDataChannelTransport(StreamingTransport):
    """Transport for a single WebRTC data channel peer.

    This transport wraps the send/receive interface of a GStreamer
    WebRTC data channel. Since GStreamer signals fire on the GLib
    thread, incoming messages are dispatched to the asyncio event
    loop via call_soon_threadsafe.
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
            loop: The asyncio event loop to dispatch callbacks on.

        """
        self._peer_id = peer_id
        self._send_fn = send_fn
        self._loop = loop
        self._message_callback: MessageCallback | None = None
        self._close_callback: CloseCallback | None = None
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
        """Send a message to the peer via the data channel.

        Args:
            message: JSON-encoded message string.

        Raises:
            ConnectionError: If the data channel is closed.

        """
        if not self._connected:
            raise ConnectionError(f"Data channel closed for peer {self._peer_id}")

        try:
            self._send_fn(self._peer_id, message)
        except Exception as e:
            self._connected = False
            raise ConnectionError(f"Failed to send to peer {self._peer_id}: {e}") from e

    def on_message(self, callback: MessageCallback) -> None:
        """Register callback for incoming messages.

        Args:
            callback: Async function that receives the raw message string.

        """
        self._message_callback = callback

    def on_close(self, callback: CloseCallback) -> None:
        """Register callback for connection close.

        Args:
            callback: Async function called when the data channel closes.

        """
        self._close_callback = callback

    async def close(self) -> None:
        """Mark the transport as closed."""
        self._connected = False

    def receive(self, message: str) -> None:
        """Receive a message from the GLib thread.

        Called from the GLib main loop thread when a data channel
        message arrives. Schedules the async callback on the asyncio loop.

        Args:
            message: The raw message string from the data channel.

        """
        if not self._connected or self._message_callback is None:
            return

        asyncio.run_coroutine_threadsafe(self._message_callback(message), self._loop)

    def notify_close(self) -> None:
        """Notify that the data channel has closed.

        Called from the GLib main loop thread when the data channel
        closes. Schedules the async close callback on the asyncio loop.
        """
        self._connected = False

        if self._close_callback is not None:
            asyncio.run_coroutine_threadsafe(self._close_callback(), self._loop)
