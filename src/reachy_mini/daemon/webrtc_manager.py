"""WebRTC manager for Reachy Mini daemon.

This module provides the WebRTCManager class that handles real-time
streaming via WebRTC (video, audio, and motor data).
"""

import asyncio
import logging
from typing import Any, Optional


class WebRTCManager:
    """Manages WebRTC real-time streaming.

    Handles WebRTC streaming for video, audio, and motor data.
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
        if self._webrtc is not None:
            self._webrtc.stop()
