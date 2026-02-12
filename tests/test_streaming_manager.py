"""Tests for StreamingManager."""

import pytest

from reachy_mini.daemon.streaming.transport import StreamingTransport
from reachy_mini.daemon.streaming_manager import StreamingManager


class _DummyTransport(StreamingTransport):
    """Minimal transport for testing create_session."""

    async def send(self, message: str) -> None:
        pass

    async def receive(self) -> str:
        return ""

    async def close(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return True


def test_create_session_without_daemon() -> None:
    """create_session raises RuntimeError before set_daemon is called."""
    mgr = StreamingManager()
    with pytest.raises(RuntimeError, match="Daemon not set"):
        mgr.create_session(_DummyTransport())


def test_webrtc_disabled_by_default() -> None:
    """WebRTC is not available when webrtc_enabled=False (default)."""
    mgr = StreamingManager()
    assert mgr.is_webrtc_available is False
    assert mgr.webrtc is None


def test_stop_webrtc_noop_when_disabled() -> None:
    """stop_webrtc does not crash when WebRTC was never enabled."""
    mgr = StreamingManager()
    mgr.stop_webrtc()  # Should not raise
    mgr.pause_webrtc()  # Should not raise
