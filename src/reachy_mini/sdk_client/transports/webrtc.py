"""WebRTC data channel transport implementation for SDK client.

Uses GStreamer's webrtcsrc to connect to the daemon's signalling server.
The data channel carries the same streaming protocol messages as WebSocket.

GStreamer signals fire on the GLib main loop thread, so this transport
uses an asyncio queue to pass messages to the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
from threading import Event, Thread
from typing import Any, Optional

import gi

from reachy_mini.daemon.streaming.transport import ConnectionClosedError
from reachy_mini.media.webrtc_utils import find_producer_peer_id_by_name
from reachy_mini.sdk_client.transport import ClientTransport

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

logger = logging.getLogger(__name__)


class WebRTCClientTransport(ClientTransport):
    """WebRTC data channel implementation of ClientTransport.

    Connects to the daemon's GStreamer signalling server using webrtcsrc.
    Motor data flows over a WebRTC data channel while video/audio pads
    are ignored (media is handled separately by MediaManager).
    """

    def __init__(
        self,
        host: str = "localhost",
        signalling_port: int = 8443,
        peer_name: str = "reachymini",
    ) -> None:
        """Initialize the WebRTC transport.

        Args:
            host: The server host address.
            signalling_port: The GStreamer signalling server port.
            peer_name: The producer peer name to connect to.

        """
        self._host = host
        self._signalling_port = signalling_port
        self._peer_name = peer_name
        self._uri = f"ws://{host}:{signalling_port}"

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._glib_loop: Optional[GLib.MainLoop] = None
        self._glib_thread: Optional[Thread] = None
        self._pipeline: Optional[Gst.Pipeline] = None
        self._data_channel: Optional[Any] = None
        self._connected = False
        self._channel_open = Event()

        self._queue: asyncio.Queue[str | None] = asyncio.Queue()

    @property
    def uri(self) -> str:
        """Get the signalling server URI."""
        return self._uri

    @property
    def is_connected(self) -> bool:
        """Check if the data channel is open."""
        return self._connected

    async def connect(self, timeout: float = 5.0) -> None:
        """Connect to the daemon via WebRTC.

        Starts a GStreamer pipeline with webrtcsrc, discovers the producer
        peer, and waits for the data channel to open.

        Args:
            timeout: Maximum time to wait for data channel to open.

        Raises:
            ConnectionError: If unable to connect or data channel doesn't open.

        """
        self._loop = asyncio.get_event_loop()

        try:
            Gst.init(None)
        except Exception:
            pass  # Already initialized

        # Start GLib main loop in background thread
        self._glib_loop = GLib.MainLoop()
        self._glib_thread = Thread(target=self._glib_loop.run, daemon=True)
        self._glib_thread.start()

        try:
            peer_id = find_producer_peer_id_by_name(
                self._host, self._signalling_port, self._peer_name
            )
        except Exception as e:
            raise ConnectionError(
                f"Failed to find producer '{self._peer_name}' at {self._uri}: {e}"
            ) from e

        logger.debug("Found producer peer ID: %s", peer_id)

        # Build pipeline
        self._pipeline = Gst.Pipeline.new("webrtc-client")
        source = Gst.ElementFactory.make("webrtcsrc")
        if not source:
            raise ConnectionError(
                "webrtcsrc GStreamer element not available. "
                "Install gst-plugins-rs (webrtc)."
            )

        self._pipeline.add(source)

        # Catch webrtcbin creation to set up data channel handler
        source.connect("deep-element-added", self._on_element_added)

        # Configure signaller
        signaller = source.get_property("signaller")
        signaller.set_property("producer-peer-id", peer_id)
        signaller.set_property("uri", self._uri)

        # Connect pad-added to handle (and discard) media pads
        source.connect("pad-added", self._on_pad_added)

        # Start pipeline
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise ConnectionError("Failed to start WebRTC pipeline")

        # Wait for data channel to open
        if not self._channel_open.wait(timeout=timeout):
            self._stop_pipeline()
            raise ConnectionError(f"Data channel did not open within {timeout}s")

        logger.info("Connected via WebRTC data channel to %s", self._uri)

    async def disconnect(self) -> None:
        """Disconnect from the WebRTC peer."""
        self._connected = False
        self._queue.put_nowait(None)
        self._stop_pipeline()

    def _stop_pipeline(self) -> None:
        """Stop GStreamer pipeline and GLib loop."""
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

        if self._glib_loop is not None:
            self._glib_loop.quit()
            self._glib_loop = None

        self._data_channel = None

    async def send(self, message: str) -> None:
        """Send a message over the data channel."""
        if not self._connected or self._data_channel is None:
            raise ConnectionClosedError("Data channel not connected")

        try:
            self._data_channel.emit("send-string", message)
        except Exception as e:
            self._connected = False
            raise ConnectionClosedError(f"Failed to send: {e}") from e

    async def receive(self) -> str:
        """Receive the next message from the data channel."""
        message = await self._queue.get()
        if message is None:
            raise ConnectionClosedError("Data channel closed")
        return message

    # --- GStreamer signal handlers (called from GLib thread) ---

    def _on_element_added(
        self, _bin: Gst.Bin, _sub_bin: Gst.Bin, element: Gst.Element
    ) -> None:
        """Catch webrtcbin to set up data channel handler."""
        if element.get_name().startswith("webrtcbin"):
            element.connect("on-data-channel", self._on_data_channel)

    def _on_pad_added(self, _src: Gst.Element, pad: Gst.Pad) -> None:
        """Handle new pads — attach fakesink to discard media streams."""
        assert self._pipeline is not None

        sink = Gst.ElementFactory.make("fakesink")
        assert sink is not None
        self._pipeline.add(sink)
        pad.link(sink.get_static_pad("sink"))
        sink.sync_state_with_parent()

    def _on_data_channel(self, _webrtcbin: Gst.Element, channel: Any) -> None:
        """Handle incoming data channel from server."""
        logger.debug("Data channel received: %s", channel.get_property("label"))
        self._data_channel = channel
        channel.connect("on-open", self._on_channel_open)
        channel.connect("on-close", self._on_channel_close)
        channel.connect("on-message-string", self._on_channel_message)
        channel.connect("on-error", self._on_channel_error)

    def _on_channel_open(self, _channel: Any) -> None:
        """Handle data channel open."""
        self._connected = True
        self._channel_open.set()

    def _on_channel_close(self, _channel: Any) -> None:
        """Handle data channel close."""
        logger.debug("Data channel closed")
        self._connected = False
        self._data_channel = None

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def _on_channel_message(self, _channel: Any, message: str) -> None:
        """Handle incoming data channel message."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    def _on_channel_error(self, _channel: Any, error: str) -> None:
        """Handle data channel error."""
        logger.error("Data channel error: %s", error)
        self._connected = False
