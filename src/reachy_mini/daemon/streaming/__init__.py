"""Streaming protocol for WebSocket and WebRTC.

This module provides the bidirectional streaming protocol used by
both WebSocket and WebRTC data channels.
"""

from reachy_mini.motion import MoveId, MoveStatus

from .handler import ProtocolHandler
from .messages import (
    # Events (Server → Client)
    AutomaticBodyRotationChangedEvent,
    # Commands (Client → Server)
    CancelCommand,
    CancelledEvent,
    DaemonStatusEvent,
    ErrorEvent,
    GetDaemonStatusCommand,
    GetStatusCommand,
    GotoCommand,
    GotoDoneEvent,
    GotoStartedEvent,
    InboundMessage,
    ModeChangedEvent,
    OutboundMessage,
    SetAutomaticBodyRotationCommand,
    SetModeCommand,
    StateEvent,
    StatusEvent,
    SubscribeCommand,
    TargetCommand,
    # Utilities
    parse_inbound_message,
)
from .session import StreamingSession
from .transport import StreamingTransport
from .transports import WebSocketTransport

__all__ = [
    # Core components
    "StreamingTransport",
    "StreamingSession",
    "ProtocolHandler",
    "WebSocketTransport",
    # Type aliases
    "MoveId",
    # Enums
    "MoveStatus",
    # Commands
    "TargetCommand",
    "GotoCommand",
    "SetModeCommand",
    "CancelCommand",
    "SubscribeCommand",
    "GetStatusCommand",
    "GetDaemonStatusCommand",
    "SetAutomaticBodyRotationCommand",
    "InboundMessage",
    # Events
    "StateEvent",
    "GotoStartedEvent",
    "GotoDoneEvent",
    "ModeChangedEvent",
    "CancelledEvent",
    "ErrorEvent",
    "StatusEvent",
    "DaemonStatusEvent",
    "AutomaticBodyRotationChangedEvent",
    "OutboundMessage",
    # Utilities
    "parse_inbound_message",
]
