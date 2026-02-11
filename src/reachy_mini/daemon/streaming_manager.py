"""Streaming manager for Reachy Mini daemon.

This module provides the StreamingManager class that handles all real-time
streaming sessions (WebSocket and WebRTC data channels).

Both transports use the same protocol: StreamingSession + ProtocolHandler,
ensuring consistent behavior regardless of transport layer.
"""

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any, Optional

from reachy_mini.daemon.streaming import (
    ProtocolHandler,
    StreamingSession,
    StreamingTransport,
)
from reachy_mini.daemon.streaming.transports.webrtc_data_channel import (
    WebRTCDataChannelTransport,
)

if TYPE_CHECKING:
    from reachy_mini.daemon.daemon import Daemon
    from reachy_mini.media.media_manager import MediaManager
    from reachy_mini.motion.manager import MotionManager
    from reachy_mini.motor_controller.abstract import MotorController


class StreamingManager:
    """Manages all real-time streaming sessions.

    Provides a unified session factory for both WebSocket and WebRTC
    transports. Also manages WebRTC infrastructure (GStreamer) when enabled.
    """

    def __init__(
        self,
        log_level: str = "INFO",
        webrtc_enabled: bool = False,
    ) -> None:
        """Initialize the StreamingManager.

        Args:
            log_level: Logging level.
            webrtc_enabled: Whether WebRTC streaming should be enabled.

        """
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        self._log_level = log_level
        self._daemon: Optional[Daemon] = None
        self._motor_controller: Optional[MotorController] = None
        self._motion_manager: Optional[MotionManager] = None
        self._audio: Optional[MediaManager] = None

        # WebRTC infrastructure
        self._webrtc: Optional[Any] = None  # GstWebRTC, imported conditionally
        self._webrtc_transports: dict[str, WebRTCDataChannelTransport] = {}
        self._webrtc_sessions: dict[str, concurrent.futures.Future[None]] = {}

        if webrtc_enabled:
            try:
                from reachy_mini.media.webrtc_daemon import GstWebRTC

                self._webrtc = GstWebRTC(log_level)
            except Exception as e:
                self.logger.error(f"Failed to initialize WebRTC: {e}")
                self._webrtc = None

    @property
    def webrtc(self) -> Optional[Any]:
        """Get the underlying GstWebRTC instance."""
        return self._webrtc

    @property
    def is_webrtc_available(self) -> bool:
        """Check if WebRTC is available and initialized."""
        return self._webrtc is not None

    def set_daemon(self, daemon: "Daemon") -> None:
        """Set the daemon reference and wire up dependencies.

        Must be called after the daemon's motor controller is started,
        so that ProtocolHandler can be created for each peer.

        Args:
            daemon: The Daemon instance.

        """
        self._daemon = daemon
        self._motor_controller = daemon.motor_controller
        self._motion_manager = daemon.motion_manager
        self._audio = daemon.audio

        if self._webrtc is not None:
            self._webrtc.set_message_handler(self._on_data_message)
            self._webrtc.set_open_handler(self._on_data_channel_open)
            self._webrtc.set_close_handler(self._on_data_channel_close)

    def create_session(self, transport: StreamingTransport) -> StreamingSession:
        """Create a streaming session for any transport type.

        Args:
            transport: The transport layer (WebSocket or DataChannel).

        Returns:
            A configured StreamingSession ready to run.

        Raises:
            RuntimeError: If the daemon is not set or motor controller not ready.

        """
        if self._daemon is None:
            raise RuntimeError("Daemon not set, call set_daemon() first")

        motor_controller = self._daemon.motor_controller
        if motor_controller is None or not motor_controller.ready.is_set():
            raise RuntimeError("Motor controller not ready")

        handler = ProtocolHandler(
            motor_controller=motor_controller,
            motion_manager=self._daemon.motion_manager,
            audio=self._daemon.audio,
            daemon=self._daemon,
        )
        return StreamingSession(transport, handler)

    # --- WebRTC lifecycle ---

    async def start_webrtc(self) -> None:
        """Start WebRTC streaming."""
        if self._webrtc is not None:
            self.logger.info("Starting WebRTC...")
            # Give some time for other components to release audio device
            await asyncio.sleep(0.2)
            self._webrtc.start()

    def pause_webrtc(self) -> None:
        """Pause WebRTC streaming (keeps signaling server running)."""
        if self._webrtc is not None:
            self._webrtc.pause()

    def stop_webrtc(self) -> None:
        """Stop WebRTC streaming and cancel all active sessions."""
        for peer_id, task in list(self._webrtc_sessions.items()):
            task.cancel()
        self._webrtc_sessions.clear()
        self._webrtc_transports.clear()

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
        transport = WebRTCDataChannelTransport(
            peer_id=peer_id,
            send_fn=self._webrtc.send_data_message,
            loop=loop,
        )
        self._webrtc_transports[peer_id] = transport

        session = self.create_session(transport)

        # Start session on the asyncio event loop
        task = asyncio.run_coroutine_threadsafe(session.run(), loop)
        self._webrtc_sessions[peer_id] = task

        self.logger.info(f"Streaming session started for WebRTC peer {peer_id}")

    def _on_data_channel_close(self, channel: Any, peer_id: str) -> None:
        """Handle data channel close event from GLib thread."""
        self.logger.info(f"Data channel closed for peer {peer_id}")

        # Notify transport (triggers session cleanup via close callback)
        transport = self._webrtc_transports.pop(peer_id, None)
        if transport is not None:
            transport.notify_close()

        # Clean up session tracking
        task = self._webrtc_sessions.pop(peer_id, None)
        if task is not None:
            task.cancel()

        # Update GstWebRTC's internal tracking
        if self._webrtc is not None and peer_id in self._webrtc._data_channels:
            del self._webrtc._data_channels[peer_id]

    def _on_data_message(self, peer_id: str, message: str) -> None:
        """Handle incoming data channel message from GLib thread.

        Routes the message to the correct transport's receive method.
        """
        transport = self._webrtc_transports.get(peer_id)
        if transport is not None:
            transport.receive(message)
        else:
            self.logger.debug(f"No transport for peer {peer_id}, ignoring message")
