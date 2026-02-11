"""WebRTC manager for Reachy Mini daemon.

This module provides the WebRTCManager class that handles real-time
streaming via WebRTC (video, audio, and motor data).

Motor data streaming uses the same protocol as WebSocket (StreamingSession +
ProtocolHandler) but transported over WebRTC data channels for lower latency.
"""

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any, Optional

from reachy_mini.daemon.streaming import ProtocolHandler, StreamingSession
from reachy_mini.daemon.streaming.transports.data_channel import DataChannelTransport

if TYPE_CHECKING:
    from reachy_mini.daemon.daemon import Daemon


class WebRTCManager:
    """Manages WebRTC real-time streaming.

    Handles WebRTC streaming for video, audio, and motor data.
    When a WebRTC peer connects, a StreamingSession is created for
    the peer's data channel, enabling motor state/command streaming
    using the same protocol as WebSocket.
    """

    def __init__(
        self,
        log_level: str = "INFO",
        enabled: bool = False,
    ) -> None:
        """Initialize the WebRTCManager.

        Args:
            log_level: Logging level.
            enabled: Whether WebRTC streaming should be enabled.

        """
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        self._log_level = log_level
        self._enabled = enabled
        self._webrtc: Optional[Any] = None  # GstWebRTC, imported conditionally
        self._daemon: Optional[Daemon] = None

        # Active streaming sessions per peer
        self._transports: dict[str, DataChannelTransport] = {}
        self._sessions: dict[str, concurrent.futures.Future[None]] = {}

        if enabled:
            try:
                from reachy_mini.media.webrtc_daemon import GstWebRTC

                self._webrtc = GstWebRTC(log_level)
            except Exception as e:
                self.logger.error(f"Failed to initialize WebRTC: {e}")
                self._webrtc = None

    def __del__(self) -> None:
        """Destructor to ensure proper cleanup."""
        self.logger.debug("Cleaning up WebRTCManager resources...")
        if self._webrtc is not None:
            self._webrtc.stop()
            self._webrtc.__del__()
            self._webrtc = None

    @property
    def webrtc(self) -> Optional[Any]:
        """Get the underlying GstWebRTC instance."""
        return self._webrtc

    @property
    def is_available(self) -> bool:
        """Check if WebRTC is available and initialized."""
        return self._webrtc is not None

    def set_daemon(self, daemon: "Daemon") -> None:
        """Set the daemon reference and wire up data channel handlers.

        Must be called after the daemon's motor controller is started,
        so that ProtocolHandler can be created for each peer.

        Args:
            daemon: The Daemon instance.

        """
        self._daemon = daemon

        if self._webrtc is not None:
            self._webrtc.set_message_handler(self._on_data_message)
            self._webrtc.set_open_handler(self._on_data_channel_open)
            self._webrtc.set_close_handler(self._on_data_channel_close)

    async def start(self) -> None:
        """Start WebRTC streaming."""
        if self._webrtc is not None:
            self.logger.info("Starting WebRTC...")
            # Give some time for other components to release audio device
            await asyncio.sleep(0.2)
            self._webrtc.start()

    def pause(self) -> None:
        """Pause WebRTC streaming (keeps signaling server running)."""
        if self._webrtc is not None:
            self._webrtc.pause()

    def stop(self) -> None:
        """Stop WebRTC streaming."""
        # Cancel all active sessions
        for peer_id, task in list(self._sessions.items()):
            task.cancel()
        self._sessions.clear()
        self._transports.clear()

        if self._webrtc is not None:
            self._webrtc.stop()

    # --- Data channel event handlers (called from GLib thread) ---

    def _on_data_channel_open(self, channel: Any, peer_id: str) -> None:
        """Handle data channel open event from GLib thread.

        Creates a streaming session for the new peer.
        """
        self.logger.info(f"Data channel opened for peer {peer_id}")

        if self._daemon is None:
            self.logger.warning("Daemon not set, cannot create streaming session")
            return

        motor_controller = self._daemon.motor_controller
        if motor_controller is None or not motor_controller.ready.is_set():
            self.logger.warning(
                "Motor controller not ready, skipping session for peer %s", peer_id
            )
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            self.logger.warning("No asyncio event loop available")
            return

        assert self._webrtc is not None  # guaranteed by set_daemon wiring
        transport = DataChannelTransport(
            peer_id=peer_id,
            send_fn=self._webrtc.send_data_message,
            loop=loop,
        )
        self._transports[peer_id] = transport

        handler = ProtocolHandler(
            motor_controller=motor_controller,
            motion_manager=self._daemon.motion_manager,
            audio=self._daemon.audio,
            daemon=self._daemon,
        )

        session = StreamingSession(transport, handler)

        # Start session on the asyncio event loop
        task = asyncio.run_coroutine_threadsafe(session.run(), loop)
        self._sessions[peer_id] = task

        self.logger.info(f"Streaming session started for WebRTC peer {peer_id}")

    def _on_data_channel_close(self, channel: Any, peer_id: str) -> None:
        """Handle data channel close event from GLib thread."""
        self.logger.info(f"Data channel closed for peer {peer_id}")

        # Notify transport (triggers session cleanup via close callback)
        transport = self._transports.pop(peer_id, None)
        if transport is not None:
            transport.notify_close()

        # Clean up session tracking
        task = self._sessions.pop(peer_id, None)
        if task is not None:
            task.cancel()

        # Update GstWebRTC's internal tracking
        if self._webrtc is not None and peer_id in self._webrtc._data_channels:
            del self._webrtc._data_channels[peer_id]

    def _on_data_message(self, peer_id: str, message: str) -> None:
        """Handle incoming data channel message from GLib thread.

        Routes the message to the correct transport's receive method.
        """
        transport = self._transports.get(peer_id)
        if transport is not None:
            transport.receive(message)
        else:
            self.logger.debug(f"No transport for peer {peer_id}, ignoring message")
