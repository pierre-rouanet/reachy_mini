"""Shared data models for daemon interfaces.

These models are used by both ApiManager (HTTP/WebSocket) and
WebRTCManager (data channels) to ensure consistent data formats.

The SDK client also uses these models for communication.
"""

from .motor_command import GotoCommand, MotorControlCommand, TargetCommand
from .motor_state import (
    DoAInfo,
    JointPositions,
    MotorControlMode,
    MotorState,
    MotorStatus,
    SensorState,
)
from .pose import (
    AnyPose,
    Matrix4x4Pose,
    XYZRPYPose,
    pose_from_numpy,
    pose_to_numpy,
)

__all__ = [
    # Pose models
    "AnyPose",
    "Matrix4x4Pose",
    "XYZRPYPose",
    "pose_from_numpy",
    "pose_to_numpy",
    # Motor state models
    "MotorControlMode",
    "JointPositions",
    "MotorState",
    "MotorStatus",
    "DoAInfo",
    "SensorState",
    # Motor command models
    "TargetCommand",
    "MotorControlCommand",
    "GotoCommand",
]
