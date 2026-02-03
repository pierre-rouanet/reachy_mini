"""API-specific pydantic models.

This module re-exports shared models and adds API-specific models
that are only used by the HTTP/WebSocket API layer.
"""

from datetime import datetime
from typing import Optional

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

# Re-export shared models for backward compatibility
from reachy_mini.daemon.models import (
    AnyPose,
    DoAInfo,
    Matrix4x4Pose,
    MotorControlMode,
    XYZRPYPose,
    pose_from_numpy,
)

__all__ = [
    "AnyPose",
    "DoAInfo",
    "Matrix4x4Pose",
    "MotorControlMode",
    "XYZRPYPose",
    "as_any_pose",
    "FullBodyTarget",
    "FullState",
]


# Backward compatibility alias
def as_any_pose(pose: NDArray[np.float64], use_matrix: bool) -> AnyPose:
    """Convert a numpy array to an AnyPose representation."""
    return pose_from_numpy(pose, use_matrix)


class FullBodyTarget(BaseModel):
    """Represent the full body target including the head pose and antennas.

    Used by the /move/set_target endpoint.
    """

    target_head_pose: Optional[AnyPose] = None
    target_antennas: Optional[tuple[float, float]] = None
    target_body_yaw: Optional[float] = None
    timestamp: Optional[datetime] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "target_head_pose": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": 0.0,
                        "roll": 0.0,
                        "pitch": 0.0,
                        "yaw": 0.0,
                    },
                    "target_antennas": [0.0, 0.0],
                    "target_body_yaw": 0.0,
                }
            ]
        }
    }


class FullState(BaseModel):
    """Represent the full state of the robot.

    Used by the /state/full endpoint.
    This is a legacy model - prefer MotorState for new code.
    """

    control_mode: Optional[MotorControlMode] = None
    head_pose: Optional[AnyPose] = None
    target_head_pose: Optional[AnyPose] = None
    head_joints: Optional[list[float]] = None
    target_head_joints: Optional[list[float]] = None
    body_yaw: Optional[float] = None
    target_body_yaw: Optional[float] = None
    antennas_position: Optional[list[float]] = None
    target_antennas_position: Optional[list[float]] = None
    timestamp: Optional[datetime] = None
    passive_joints: Optional[list[float]] = None
    doa: Optional[DoAInfo] = None
