"""Shared data models for daemon interfaces.

These models are used by both ApiManager (HTTP/WebSocket) and
WebRTCManager (data channels) to ensure consistent data formats.

The SDK client also uses these models for communication.
"""

from .motor_command import (
    FullBodyTarget,
    GotoRequest,
    MotorControlCommand,
)
from .motor_state import (
    FullState,
    MotorControlMode,
    MotorName,
)
from .pose import (
    AnyPose,
    Matrix4x4Pose,
    XYZRPYPose,
    pose_from_numpy,
    pose_to_numpy,
)
from .sensors import (
    DoAData,
    IMUData,
    SensorData,
)
from .daemon_status import DaemonStatus

__all__ = [
    # Pose models
    "AnyPose",
    "Matrix4x4Pose",
    "XYZRPYPose",
    "pose_from_numpy",
    "pose_to_numpy",
    # Motor state models
    "MotorControlMode",
    "MotorName",
    "FullState",
    # Motor command models
    "FullBodyTarget",
    "MotorControlCommand",
    "GotoRequest",
    # Sensor models
    "SensorData",
    "DoAData",
    "IMUData",
    # Daemon status
    "DaemonStatus",
]
