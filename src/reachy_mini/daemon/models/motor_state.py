"""Motor state models.

These models represent the current state of the robot's motors.
Used by ApiManager and WebRTCManager for state streaming.
"""

from enum import Enum

from pydantic import BaseModel, SerializeAsAny

from .pose import AnyPose
from .sensors import SensorData


class MotorControlMode(str, Enum):
    """Motor control modes."""

    Enabled = "enabled"  # Torque ON, position control
    Disabled = "disabled"  # Torque OFF, compliant
    GravityCompensation = "gravity_compensation"  # Torque ON, current control


class MotorName(str, Enum):
    """Motor names for the Reachy Mini robot.

    The robot has 9 motors:
    - body_rotation: rotates the entire head assembly
    - stewart_1 to stewart_6: 6 linear actuators forming the stewart platform
    - right_antenna, left_antenna: the two antennas
    """

    # Body rotation (separate from stewart platform)
    BodyRotation = "body_rotation"

    # Stewart platform motors (6 linear actuators)
    Stewart1 = "stewart_1"
    Stewart2 = "stewart_2"
    Stewart3 = "stewart_3"
    Stewart4 = "stewart_4"
    Stewart5 = "stewart_5"
    Stewart6 = "stewart_6"

    # Antennas
    RightAntenna = "right_antenna"
    LeftAntenna = "left_antenna"


class FullState(BaseModel):
    """Full robot state.

    Used by the /state/full endpoint and streaming state events.
    All fields are optional to support selective field inclusion.
    """

    # Motor control
    control_mode: MotorControlMode | None = None

    # Head pose (task space)
    head_pose: AnyPose | None = None
    target_head_pose: AnyPose | None = None

    # Head joints (joint space) - 6 stewart platform motors only, body_rotation is separate
    head_joints: list[float] | None = None
    target_head_joints: list[float] | None = None

    # Body
    body_rotation: float | None = None
    target_body_rotation: float | None = None

    # Antennas
    antennas: tuple[float, float] | None = None
    target_antennas: tuple[float, float] | None = None

    # Passive joints (read-only)
    passive_joints: list[float] | None = None

    # Timestamp (seconds since epoch)
    timestamp: float | None = None

    # Sensors (extensible via SensorData subclasses)
    # SerializeAsAny ensures subclass fields are included in serialization
    sensors: dict[str, SerializeAsAny[SensorData]] = {}
