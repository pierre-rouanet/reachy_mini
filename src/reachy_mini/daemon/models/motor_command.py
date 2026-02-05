"""Motor command models.

These models represent commands sent to the robot's motors.
Used by ApiManager and WebRTCManager for receiving commands.
"""

from pydantic import BaseModel, model_validator

from reachy_mini.utils.interpolation import InterpolationTechnique

from .motor_state import MotorControlMode, MotorName
from .pose import AnyPose


class FullBodyTarget(BaseModel):
    """Target positions for the robot.

    Used by the /move/set_target endpoint and target streaming commands.

    For head control, use either head_pose (task-space) or head_joints
    (joint-space), but not both. If both are provided, head_pose takes precedence.

    All fields are optional - only provided fields are updated.
    """

    head_pose: AnyPose | None = None
    head_joints: list[float] | None = None  # 6 stewart platform joint positions in radians
    antennas: tuple[float, float] | None = None
    body_rotation: float | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "head_pose": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": 0.02,
                        "roll": 0.0,
                        "pitch": 0.2,
                        "yaw": 0.0,
                    },
                    "antennas": [0.0, 0.0],
                    "body_rotation": 0.0,
                }
            ]
        }
    }


class MotorControlCommand(BaseModel):
    """Motor control mode command.

    Changes the motor control mode (enable/disable/gravity compensation).

    Note: gravity_compensation mode only applies to the 6 stewart platform motors
    as a unit (they work together for parallel kinematics). When gravity_compensation
    is requested, motor_names must be None (the mode is applied globally, affecting
    only the stewart motors internally).
    """

    mode: MotorControlMode

    # Optional: target specific motors by name (only for enabled/disabled modes)
    # TODO: Implement per-motor mode control in MotorController
    motor_names: list[MotorName] | None = None

    @model_validator(mode="after")
    def validate_gravity_compensation(self) -> "MotorControlCommand":
        """Validate that gravity_compensation is only used globally."""
        if self.mode == MotorControlMode.GravityCompensation and self.motor_names is not None:
            raise ValueError(
                "gravity_compensation mode cannot target specific motors. "
                "It only applies to the 6 stewart platform motors as a unit."
            )
        return self


class GotoRequest(BaseModel):
    """Goto request with interpolation.

    Moves to a target over a specified duration with interpolation.

    For head control, use either head_pose (task-space) or head_joints
    (joint-space), but not both. If both are provided, head_pose takes precedence.

    At least one target (head_pose, head_joints, antennas, or body_rotation) is required.
    """

    head_pose: AnyPose | None = None
    head_joints: list[float] | None = None  # 6 stewart platform joint positions in radians
    antennas: tuple[float, float] | None = None
    body_rotation: float | None = None
    duration: float  # Movement duration in seconds
    interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK

    @model_validator(mode="after")
    def validate_has_target(self) -> "GotoRequest":
        """Validate that at least one target is provided."""
        if (
            self.head_pose is None
            and self.head_joints is None
            and self.antennas is None
            and self.body_rotation is None
        ):
            raise ValueError(
                "At least one target (head_pose, head_joints, antennas, or body_rotation) is required."
            )
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "head_pose": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": 0.02,
                        "roll": 0.0,
                        "pitch": 0.2,
                        "yaw": 0.0,
                    },
                    "duration": 2.0,
                    "interpolation": "minjerk",
                },
                {
                    "head_joints": [0.0, 0.1, -0.1, 0.0, 0.2, 0.0],
                    "antennas": [0.3, -0.3],
                    "duration": 1.5,
                    "interpolation": "linear",
                },
            ],
        }
    }
