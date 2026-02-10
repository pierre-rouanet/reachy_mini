"""SDK client transport implementations."""

from reachy_mini.sdk_client.transports.websocket import WebSocketClientTransport

__all__ = ["WebSocketClientTransport"]


def get_webrtc_transport(
    host: str = "localhost",
    signalling_port: int = 8443,
    peer_name: str = "reachymini",
) -> "WebRTCClientTransport":  # noqa: F821
    """Create a WebRTC data channel transport (requires GStreamer).

    Lazily imports to avoid requiring GStreamer when not needed.

    Args:
        host: The server host address.
        signalling_port: The GStreamer signalling server port.
        peer_name: The producer peer name to connect to.

    Returns:
        A WebRTCClientTransport instance.

    """
    from reachy_mini.sdk_client.transports.webrtc import WebRTCClientTransport

    return WebRTCClientTransport(
        host=host,
        signalling_port=signalling_port,
        peer_name=peer_name,
    )
