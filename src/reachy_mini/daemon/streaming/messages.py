"""Streaming protocol message definitions.

These messages define the contract between clients and server for
WebSocket and WebRTC streaming. Both HTTP and Streaming APIs share
the underlying data models (FullState, FullBodyTarget, GotoRequest).

Commands: Client → Server (have 'cmd' field)
Events: Server → Client (have 'event' field)
"""

from typing import Literal

from pydantic import BaseModel, TypeAdapter

from reachy_mini.daemon.models import (
    DaemonStatus,
    FullBodyTarget,
    FullState,
    GotoRequest,
    MotorControlMode,
)
from reachy_mini.motion.manager import MoveId, MoveStatus

# =============================================================================
# Commands (Client → Server)
# =============================================================================


class TargetCommand(BaseModel):
    """Set immediate target position.

    No response - fire-and-forget for performance.
    """

    cmd: Literal["target"] = "target"
    target: FullBodyTarget


class GotoCommand(BaseModel):
    """Start an interpolated movement.

    If 'id' is provided: async mode - returns GotoStartedEvent immediately,
    then GotoDoneEvent when complete. The ID must be unique - reusing an
    active or recently completed move ID returns an ErrorEvent with code
    'DUPLICATE_MOVE_ID'.

    If 'id' is omitted: blocking mode - server generates a UUID internally,
    returns GotoDoneEvent when complete.
    """

    cmd: Literal["goto"] = "goto"
    request: GotoRequest
    id: MoveId | None = None  # Provide to enable async mode (must be unique)


class SetModeCommand(BaseModel):
    """Change motor control mode.

    Returns ModeChangedEvent on success.
    """

    cmd: Literal["set_mode"] = "set_mode"
    mode: MotorControlMode


class CancelCommand(BaseModel):
    """Cancel an async goto movement.

    Returns CancelledEvent, and the goto receives GotoDoneEvent with status 'cancelled'.
    """

    cmd: Literal["cancel"] = "cancel"
    id: MoveId


class SubscribeCommand(BaseModel):
    """Configure state streaming.

    Must be sent before receiving state events.
    """

    cmd: Literal["subscribe"] = "subscribe"
    fields: list[str] | None = None  # None = all motor state fields
    sensors: list[str] | None = None  # Sensor types to include
    frequency: float = 50.0  # Hz, max 100


class GetStatusCommand(BaseModel):
    """Request daemon/motor status.

    Returns StatusEvent.
    """

    cmd: Literal["get_status"] = "get_status"


class SetAutomaticBodyRotationCommand(BaseModel):
    """Set automatic body rotation.

    When enabled, the body rotation is automatically computed during IK
    to stay within mechanical limits.

    Returns AutomaticBodyRotationChangedEvent.
    """

    cmd: Literal["set_automatic_body_rotation"] = "set_automatic_body_rotation"
    enabled: bool


class GetDaemonStatusCommand(BaseModel):
    """Request full daemon status.

    Returns DaemonStatusEvent with complete daemon information.
    """

    cmd: Literal["get_daemon_status"] = "get_daemon_status"


# Union of all inbound message types
InboundMessage = (
    TargetCommand
    | GotoCommand
    | SetModeCommand
    | CancelCommand
    | SubscribeCommand
    | GetStatusCommand
    | SetAutomaticBodyRotationCommand
    | GetDaemonStatusCommand
)

# Type adapter for parsing inbound messages
_inbound_adapter: TypeAdapter[InboundMessage] = TypeAdapter(InboundMessage)


def parse_inbound_message(data: str | bytes) -> InboundMessage:
    """Parse and validate an inbound message from JSON."""
    return _inbound_adapter.validate_json(data)


# =============================================================================
# Events (Server → Client)
# =============================================================================


class StateEvent(BaseModel):
    """Robot state update.

    Sent continuously after subscribe command.
    """

    event: Literal["state"] = "state"
    state: FullState


class GotoStartedEvent(BaseModel):
    """Async goto has started.

    Only sent for gotos with an id (async mode).
    """

    event: Literal["goto_started"] = "goto_started"
    id: MoveId


class GotoDoneEvent(BaseModel):
    """Goto movement completed.

    For blocking goto: id is omitted.
    For async goto: id matches the request id.
    """

    event: Literal["goto_done"] = "goto_done"
    status: MoveStatus
    id: MoveId | None = None


class ModeChangedEvent(BaseModel):
    """Motor mode changed successfully."""

    event: Literal["mode_changed"] = "mode_changed"
    mode: MotorControlMode


class CancelledEvent(BaseModel):
    """Async goto was cancelled."""

    event: Literal["cancelled"] = "cancelled"
    id: MoveId


class ErrorEvent(BaseModel):
    """An error occurred."""

    event: Literal["error"] = "error"
    message: str
    code: str | None = None  # Optional error code for programmatic handling


class StatusEvent(BaseModel):
    """Daemon/motor status response."""

    event: Literal["status"] = "status"
    motor_ready: bool
    control_mode: MotorControlMode | None = None
    available_sensors: list[str] = []
    automatic_body_rotation: bool | None = None


class AutomaticBodyRotationChangedEvent(BaseModel):
    """Automatic body rotation setting changed."""

    event: Literal["automatic_body_rotation_changed"] = "automatic_body_rotation_changed"
    enabled: bool


class DaemonStatusEvent(DaemonStatus):
    """Full daemon status response (streaming event)."""

    event: Literal["daemon_status"] = "daemon_status"


# Union of all outbound message types
OutboundMessage = (
    StateEvent
    | GotoStartedEvent
    | GotoDoneEvent
    | ModeChangedEvent
    | CancelledEvent
    | ErrorEvent
    | StatusEvent
    | AutomaticBodyRotationChangedEvent
    | DaemonStatusEvent
)
