"""Streaming protocol for WebSocket and WebRTC.

This module provides the bidirectional streaming protocol used by
both WebSocket and WebRTC data channels.
"""

from .messages import (
    # Type aliases
    MoveId,
    # Enums
    MoveStatus,
    # Commands (Client → Server)
    CancelCommand,
    GetDaemonStatusCommand,
    GetStatusCommand,
    GotoCommand,
    InboundMessage,
    SetAutomaticBodyRotationCommand,
    SetModeCommand,
    SubscribeCommand,
    TargetCommand,
    # Events (Server → Client)
    AutomaticBodyRotationChangedEvent,
    CancelledEvent,
    DaemonStatusEvent,
    ErrorEvent,
    GotoDoneEvent,
    GotoStartedEvent,
    ModeChangedEvent,
    OutboundMessage,
    StateEvent,
    StatusEvent,
    # Utilities
    parse_inbound_message,
)

__all__ = [
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
