"""Motor state models.

These models represent the current state of the robot's motors.
Used by ApiManager and WebRTCManager for state streaming.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from .pose import AnyPose


class MotorControlMode(str, Enum):
    """Motor control modes."""

    Enabled = "enabled"  # Torque ON, position control
    Disabled = "disabled"  # Torque OFF, compliant
    GravityCompensation = "gravity_compensation"  # Torque ON, current control


class JointPositions(BaseModel):
    """Joint positions for the robot.

    Attributes:
        body_rotation: Rotation of the body/base (radians).
        stewart: 6 actuator positions for the Stewart platform (radians).
        antennas: 2 joint positions for left/right antennas (radians).

    """

    body_rotation: float
    stewart: tuple[float, float, float, float, float, float]
    antennas: tuple[float, float]


class MotorState(BaseModel):
    """Complete motor state snapshot.

    This is the primary model for streaming motor state data.
    Used by both ApiManager (WebSocket) and WebRTCManager (data channel).
    """

    # Timing
    timestamp: datetime

    # Control mode
    control_mode: MotorControlMode

    # Current state - task space
    head_pose: AnyPose

    # Current state - joint space
    body_rotation: float
    stewart: tuple[float, float, float, float, float, float]
    antennas: tuple[float, float]

    # Target state (what we're moving towards)
    target_head_pose: Optional[AnyPose] = None
    target_body_rotation: Optional[float] = None
    target_stewart: Optional[tuple[float, float, float, float, float, float]] = None
    target_antennas: Optional[tuple[float, float]] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "timestamp": "2024-01-01T00:00:00",
                    "control_mode": "enabled",
                    "head_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "roll": 0.0, "pitch": 0.0, "yaw": 0.0},
                    "body_rotation": 0.0,
                    "stewart": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "antennas": [0.0, 0.0],
                }
            ]
        }
    }


class MotorStatus(BaseModel):
    """Motor controller status information.

    Higher-level status about the motor controller itself.
    """

    ready: bool
    control_mode: MotorControlMode
    error: Optional[str] = None
    last_alive: Optional[float] = None

    # Control loop statistics
    mean_frequency: Optional[float] = None
    max_interval: Optional[float] = None
    error_count: Optional[int] = None


class DoAInfo(BaseModel):
    """Direction of Arrival info from the microphone array."""

    angle: float  # Angle in radians (0=left, π/2=front, π=right)
    speech_detected: bool


class SensorState(BaseModel):
    """Sensor data that may be available.

    Optional sensor readings beyond motor state.
    """

    doa: Optional[DoAInfo] = None
    # Future: IMU data, etc.
