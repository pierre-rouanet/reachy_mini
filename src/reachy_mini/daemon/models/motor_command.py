"""Motor command models.

These models represent commands sent to the robot's motors.
Used by ApiManager and WebRTCManager for receiving commands.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from .motor_state import MotorControlMode
from .pose import AnyPose


class TargetCommand(BaseModel):
    """Target command for the robot.

    Sets desired positions in either task space (head_pose) or joint space.
    At least one target must be specified.
    """

    # Task space target
    head_pose: Optional[AnyPose] = None

    # Joint space targets
    body_rotation: Optional[float] = None
    stewart: Optional[tuple[float, float, float, float, float, float]] = None
    antennas: Optional[tuple[float, float]] = None

    # Timestamp for latency tracking
    timestamp: Optional[datetime] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "head_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "roll": 0.0, "pitch": 0.0, "yaw": 0.0},
                    "antennas": [0.0, 0.0],
                },
                {
                    "body_rotation": 0.5,
                    "stewart": [0.1, 0.2, 0.1, 0.2, 0.1, 0.2],
                },
            ]
        }
    }


class MotorControlCommand(BaseModel):
    """Motor control mode command.

    Changes the motor control mode (enable/disable/gravity compensation).
    """

    mode: MotorControlMode

    # Optional: target specific motors by name
    motor_ids: Optional[list[str]] = None


class GotoCommand(BaseModel):
    """Goto command with interpolation.

    Moves to a target over a specified duration with interpolation.
    """

    # Target (at least one must be specified)
    head_pose: Optional[AnyPose] = None
    body_rotation: Optional[float] = None
    antennas: Optional[tuple[float, float]] = None

    # Movement parameters
    duration: float  # seconds
    interpolation: str = "minjerk"  # linear, minjerk, ease, cartoon

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "head_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "roll": 0.0, "pitch": 0.0, "yaw": 0.0},
                    "duration": 1.0,
                    "interpolation": "minjerk",
                }
            ]
        }
    }
