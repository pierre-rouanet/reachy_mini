"""State-related API routes.

This exposes:
- basic get routes to retrieve most common fields
- full state and streaming state updates
"""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from reachy_mini.daemon.models import AnyPose, DoAData, FullState, pose_from_numpy
from reachy_mini.media.media_manager import MediaManager
from reachy_mini.motor_controller.abstract import MotorController

from ..dependencies import get_audio, get_motor_controller, ws_get_motor_controller

# Maximum streaming frequency (Hz) - limited to avoid overwhelming clients
MAX_STREAMING_FREQUENCY = 100.0

router = APIRouter(prefix="/state")


@router.get("/present_head_pose")
async def get_head_pose(
    use_pose_matrix: bool = False,
    motor_controller: MotorController = Depends(get_motor_controller),
) -> AnyPose:
    """Get the present head pose.

    Arguments:
        use_pose_matrix (bool): Whether to use the pose matrix representation (4x4 flattened) or the translation + Euler angles representation (x, y, z, roll, pitch, yaw).
        motor_controller (MotorController): The motor controller instance.

    Returns:
        AnyPose: The present head pose.

    """
    return pose_from_numpy(motor_controller.get_present_head_pose(), use_pose_matrix)


@router.get("/present_body_rotation")
async def get_body_rotation(
    motor_controller: MotorController = Depends(get_motor_controller),
) -> float:
    """Get the present body rotation (in radians)."""
    return motor_controller.get_present_body_yaw()


@router.get("/present_antenna_joint_positions")
async def get_antenna_joint_positions(
    motor_controller: MotorController = Depends(get_motor_controller),
) -> tuple[float, float]:
    """Get the present antenna joint positions (in radians) - (left, right)."""
    pos = motor_controller.get_present_antenna_joint_positions()
    assert len(pos) == 2
    return (pos[0], pos[1])


@router.get("/doa")
async def get_doa(
    audio: MediaManager | None = Depends(get_audio),
) -> DoAData | None:
    """Get the Direction of Arrival from the microphone array.

    Returns the angle in radians (0=left, π/2=front, π=right) and speech detection status.
    Returns None if the audio device is not available.
    """
    if not audio:
        return None
    result = audio.get_DoA()
    if result is None:
        return None
    return DoAData(angle=result[0], speech_detected=result[1])


@router.get("/full")
async def get_full_state(
    with_control_mode: bool = True,
    with_head_pose: bool = True,
    with_target_head_pose: bool = False,
    with_head_joints: bool = False,
    with_target_head_joints: bool = False,
    with_body_rotation: bool = True,
    with_target_body_rotation: bool = False,
    with_antennas: bool = True,
    with_target_antennas: bool = False,
    with_passive_joints: bool = False,
    sensors: str | None = None,
    use_pose_matrix: bool = False,
    motor_controller: MotorController = Depends(get_motor_controller),
    audio: MediaManager | None = Depends(get_audio),
) -> FullState:
    """Get the full robot state, with optional fields.

    Args:
        sensors: Comma-separated sensor types (e.g., "doa,imu") or "all" for all available.
    """
    result: dict[str, Any] = {}

    if with_control_mode:
        result["control_mode"] = motor_controller.get_motor_control_mode().value

    if with_head_pose:
        pose = motor_controller.get_present_head_pose()
        result["head_pose"] = pose_from_numpy(pose, use_pose_matrix)
    if with_target_head_pose:
        target_pose = motor_controller.target_head_pose
        assert target_pose is not None
        result["target_head_pose"] = pose_from_numpy(target_pose, use_pose_matrix)
    if with_head_joints:
        # Return only the 6 stewart platform joints (exclude body_rotation which is index 0)
        head_joints = motor_controller.get_present_head_joint_positions()
        result["head_joints"] = list(head_joints[1:])
    if with_target_head_joints:
        target = motor_controller.target_head_joint_positions
        if target is not None:
            result["target_head_joints"] = list(target[1:])
    if with_body_rotation:
        result["body_rotation"] = motor_controller.get_present_body_yaw()
    if with_target_body_rotation:
        result["target_body_rotation"] = motor_controller.target_body_yaw
    if with_antennas:
        pos = motor_controller.get_present_antenna_joint_positions()
        result["antennas"] = (pos[0], pos[1])
    if with_target_antennas:
        target = motor_controller.target_antenna_joint_positions
        if target is not None:
            result["target_antennas"] = (target[0], target[1])

    if with_passive_joints:
        joints = motor_controller.get_present_passive_joint_positions()
        if joints is not None:
            result["passive_joints"] = list(joints.values())
        else:
            result["passive_joints"] = None

    # Handle sensors
    result["sensors"] = {}
    if sensors:
        requested_sensors = sensors.split(",") if sensors != "all" else ["doa"]  # TODO: get from registry

        if "doa" in requested_sensors and audio:
            doa_result = audio.get_DoA()
            if doa_result:
                result["sensors"]["doa"] = DoAData(angle=doa_result[0], speech_detected=doa_result[1])

        # TODO: Add IMU and other sensors via sensor registry

    result["timestamp"] = time.time()
    return FullState.model_validate(result)


@router.websocket("/ws/full")
async def ws_full_state(
    websocket: WebSocket,
    frequency: float = 10.0,
    with_head_pose: bool = True,
    with_target_head_pose: bool = False,
    with_head_joints: bool = False,
    with_target_head_joints: bool = False,
    with_body_rotation: bool = True,
    with_target_body_rotation: bool = False,
    with_antennas: bool = True,
    with_target_antennas: bool = False,
    with_passive_joints: bool = False,
    sensors: str | None = None,
    use_pose_matrix: bool = False,
    motor_controller: MotorController = Depends(ws_get_motor_controller),
) -> None:
    """WebSocket endpoint to stream the full state of the robot.

    Supports frequencies up to 100Hz for teleoperation use cases.
    """
    await websocket.accept()
    period = 1.0 / min(frequency, MAX_STREAMING_FREQUENCY)

    try:
        while True:
            full_state = await get_full_state(
                with_head_pose=with_head_pose,
                with_target_head_pose=with_target_head_pose,
                with_head_joints=with_head_joints,
                with_target_head_joints=with_target_head_joints,
                with_body_rotation=with_body_rotation,
                with_target_body_rotation=with_target_body_rotation,
                with_antennas=with_antennas,
                with_target_antennas=with_target_antennas,
                with_passive_joints=with_passive_joints,
                sensors=sensors,
                use_pose_matrix=use_pose_matrix,
                motor_controller=motor_controller,
            )
            await websocket.send_text(full_state.model_dump_json())
            await asyncio.sleep(period)
    except WebSocketDisconnect:
        pass
