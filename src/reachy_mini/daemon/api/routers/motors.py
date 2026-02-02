"""Motors router.

Provides endpoints to get and set the motor control mode.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from reachy_mini.motor_controller.abstract import MotorControlMode, MotorController
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
