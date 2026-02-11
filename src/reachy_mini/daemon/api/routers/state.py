"""State-related API routes.

This exposes:
- basic get routes to retrieve most common fields
- full state HTTP endpoint

For real-time streaming, use the unified WebSocket endpoint at /api/stream/ws.
"""

from fastapi import APIRouter, Depends

from reachy_mini.daemon.models import AnyPose, DoAData, FullState, pose_from_numpy
from reachy_mini.daemon.state_builder import build_state
from reachy_mini.media.media_manager import MediaManager
from reachy_mini.motor_controller.abstract import MotorController

from ..dependencies import get_audio, get_motor_controller

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
    field_map = {
        "control_mode": with_control_mode,
        "head_pose": with_head_pose,
        "target_head_pose": with_target_head_pose,
        "head_joints": with_head_joints,
        "target_head_joints": with_target_head_joints,
        "body_rotation": with_body_rotation,
        "target_body_rotation": with_target_body_rotation,
        "antennas": with_antennas,
        "target_antennas": with_target_antennas,
        "passive_joints": with_passive_joints,
    }
    fields = [name for name, include in field_map.items() if include]

    sensor_list: list[str] | None = None
    if sensors:
        sensor_list = sensors.split(",") if sensors != "all" else ["doa"]  # TODO: get from registry

    return build_state(motor_controller, audio, fields, sensor_list, use_pose_matrix)


