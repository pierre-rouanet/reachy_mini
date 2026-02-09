"""Server-side streaming transport.

Re-exports the shared Transport ABC for server-side use.
Server transports wrap already-connected sockets (e.g., FastAPI WebSocket).
"""

from reachy_mini.streaming.transport import CloseCallback, MessageCallback, Transport

# Re-export for backwards compatibility
StreamingTransport = Transport

__all__ = ["StreamingTransport", "Transport", "MessageCallback", "CloseCallback"]
