"""Motors router.

Provides endpoints to get and set the motor control mode.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from reachy_mini.motor_controller.abstract import MotorController, MotorControlMode

from ..dependencies import get_motor_controller

router = APIRouter(
    prefix="/motors",
)


class MotorStatus(BaseModel):
    """Represents the status of the motors.

    Exposes
    - mode: The current motor control mode (enabled, disabled, gravity_compensation).
    """

    mode: MotorControlMode


@router.get("/status")
async def get_motor_status(motor_controller: MotorController = Depends(get_motor_controller)) -> MotorStatus:
    """Get the current status of the motors."""
    return MotorStatus(mode=motor_controller.get_motor_control_mode())


@router.post("/set_mode/{mode}")
async def set_motor_mode(
    mode: MotorControlMode,
    motor_controller: MotorController = Depends(get_motor_controller),
) -> dict[str, str]:
    """Set the motor control mode."""
    motor_controller.set_motor_control_mode(mode)

    return {"status": f"motors changed to {mode} mode"}


@router.get("/automatic_body_rotation")
async def get_automatic_body_rotation(
    motor_controller: MotorController = Depends(get_motor_controller),
) -> bool:
    """Get the automatic body rotation setting."""
    return motor_controller.head_kinematics.automatic_body_yaw


@router.post("/automatic_body_rotation/{enabled}")
async def set_automatic_body_rotation(
    enabled: bool,
    motor_controller: MotorController = Depends(get_motor_controller),
) -> dict[str, str]:
    """Set the automatic body rotation setting.

    When enabled, the body rotation is automatically computed during IK
    to stay within mechanical limits.
    """
    motor_controller.set_automatic_body_yaw(enabled)  # Internal still uses body_yaw
    return {"status": f"automatic_body_rotation set to {enabled}"}
