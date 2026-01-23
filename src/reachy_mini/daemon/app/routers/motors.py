"""Motors router.

Provides endpoints to get and set the motor control mode.
"""

import json

import numpy as np
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ....daemon.backend.abstract import Backend, MotorControlMode
from ..dependencies import get_backend, ws_get_backend

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
async def get_motor_status(backend: Backend = Depends(get_backend)) -> MotorStatus:
    """Get the current status of the motors."""
    return MotorStatus(mode=backend.get_motor_control_mode())


@router.post("/set_mode/{mode}")
async def set_motor_mode(
    mode: MotorControlMode,
    backend: Backend = Depends(get_backend),
) -> dict[str, str]:
    """Set the motor control mode."""
    backend.set_motor_control_mode(mode)

    return {"status": f"motors changed to {mode} mode"}


@router.websocket("/ws/command")
async def ws_command(
    websocket: WebSocket,
    backend: Backend = Depends(ws_get_backend),
) -> None:
    """WebSocket endpoint for sending real-time commands to the robot.

    Accepts commands in JSON format with any combination of:
    - torque: bool or specific motor IDs
    - head_joint_positions: list[float]
    - head_pose: list[float] (4x4 matrix flattened)
    - body_yaw: float
    - antennas_joint_positions: list[float]
    - gravity_compensation: bool
    - automatic_body_yaw: bool
    - set_target_record: dict
    - start_recording: bool
    - stop_recording: bool
    """
    await websocket.accept()

    try:
        while True:
            # Receive command from client
            data = await websocket.receive_text()
            command = json.loads(data)

            # Handle command
            block_targets = backend.is_move_running

            def _maybe_ignore(field: str) -> bool:
                """Return True if the command should be ignored while a move runs."""
                if not block_targets:
                    return False
                backend.logger.warning(
                    f"Ignoring {field} command: a move is currently running."
                )
                return True

            if "torque" in command:
                if command.get("ids") is not None:
                    backend.set_motor_torque_ids(command["ids"], command["torque"])
                else:
                    if command["torque"]:
                        backend.set_motor_control_mode(MotorControlMode.Enabled)
                    else:
                        backend.set_motor_control_mode(MotorControlMode.Disabled)

            if "head_joint_positions" in command:
                if not _maybe_ignore("head_joint_positions"):
                    backend.set_target_head_joint_positions(
                        np.array(command["head_joint_positions"])
                    )

            if "head_pose" in command:
                if not _maybe_ignore("head_pose"):
                    backend.set_target_head_pose(
                        np.array(command["head_pose"]).reshape(4, 4)
                    )

            if "body_yaw" in command:
                if not _maybe_ignore("body_yaw"):
                    backend.set_target_body_yaw(command["body_yaw"])

            if "antennas_joint_positions" in command:
                if not _maybe_ignore("antennas_joint_positions"):
                    backend.set_target_antenna_joint_positions(
                        np.array(command["antennas_joint_positions"]),
                    )

            if "gravity_compensation" in command:
                try:
                    if command["gravity_compensation"]:
                        backend.set_motor_control_mode(
                            MotorControlMode.GravityCompensation
                        )
                    else:
                        backend.set_motor_control_mode(MotorControlMode.Enabled)
                except ValueError as e:
                    backend.logger.error(f"Error setting gravity compensation: {e}")

            if "automatic_body_yaw" in command:
                backend.set_automatic_body_yaw(command["automatic_body_yaw"])

            if "set_target_record" in command:
                backend.append_record(command["set_target_record"])

            if "start_recording" in command:
                backend.start_recording()

            if "stop_recording" in command:
                backend.stop_recording()

    except WebSocketDisconnect:
        pass
