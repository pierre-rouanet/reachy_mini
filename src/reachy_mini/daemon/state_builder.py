"""Shared state builder for HTTP and streaming APIs.

Builds a FullState from motor controller and sensor data,
with support for selective field inclusion.

Used by both the /state/full HTTP endpoint and the streaming
protocol's subscribe/state mechanism.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from reachy_mini.daemon.models import DoAData, FullState, IMUData, pose_from_numpy

if TYPE_CHECKING:
    from reachy_mini.media.media_manager import MediaManager
    from reachy_mini.motor_controller.abstract import MotorController


def build_state(
    motor_controller: MotorController,
    audio: MediaManager | None,
    fields: list[str] | None = None,
    sensors: list[str] | None = None,
    use_pose_matrix: bool = False,
) -> FullState:
    """Build a FullState object based on requested fields and sensors.

    Args:
        motor_controller: The motor controller instance.
        audio: Optional audio/media manager for DoA sensor.
        fields: List of field names to include, or None for all.
        sensors: List of sensor types to include (e.g. ["doa", "imu"]).
        use_pose_matrix: Whether to use matrix format for poses.

    Returns:
        FullState with requested data.

    """
    mc = motor_controller
    include_all = fields is None
    _fields = fields or []

    result: dict[str, Any] = {}

    if include_all or "control_mode" in _fields:
        result["control_mode"] = mc.get_motor_control_mode().value

    if include_all or "head_pose" in _fields:
        pose = mc.get_present_head_pose()
        result["head_pose"] = pose_from_numpy(pose, use_pose_matrix)

    if include_all or "target_head_pose" in _fields:
        target_pose = mc.target_head_pose
        if target_pose is not None:
            result["target_head_pose"] = pose_from_numpy(target_pose, use_pose_matrix)

    if include_all or "head_joints" in _fields:
        head_joints = mc.get_present_head_joint_positions()
        # Exclude body_rotation at index 0
        result["head_joints"] = list(head_joints[1:])

    if include_all or "target_head_joints" in _fields:
        target = mc.target_head_joint_positions
        if target is not None:
            result["target_head_joints"] = list(target[1:])

    if include_all or "body_rotation" in _fields:
        result["body_rotation"] = mc.get_present_body_yaw()

    if include_all or "target_body_rotation" in _fields:
        result["target_body_rotation"] = mc.target_body_yaw

    if include_all or "antennas" in _fields:
        pos = mc.get_present_antenna_joint_positions()
        result["antennas"] = (pos[0], pos[1])

    if include_all or "target_antennas" in _fields:
        target = mc.target_antenna_joint_positions
        if target is not None:
            result["target_antennas"] = (target[0], target[1])

    if include_all or "passive_joints" in _fields:
        joints = mc.get_present_passive_joint_positions()
        if joints is not None:
            result["passive_joints"] = list(joints.values())
        else:
            result["passive_joints"] = None

    # Handle sensors
    result["sensors"] = {}
    if sensors:
        if "doa" in sensors and audio:
            doa_result = audio.get_DoA()
            if doa_result:
                result["sensors"]["doa"] = DoAData(
                    angle=doa_result[0], speech_detected=doa_result[1]
                )

        if "imu" in sensors:
            imu_data = mc.get_imu_data() if hasattr(mc, "get_imu_data") else None
            if imu_data:
                result["sensors"]["imu"] = IMUData(
                    accelerometer=tuple(imu_data["accelerometer"]),
                    gyroscope=tuple(imu_data["gyroscope"]),
                    quaternion=tuple(imu_data["quaternion"]),
                    temperature=imu_data.get("temperature"),
                )

    result["timestamp"] = time.time()
    return FullState.model_validate(result)
