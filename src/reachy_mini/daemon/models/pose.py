"""Pose representation models.

These models are used to represent 3D poses in different formats.
Both ApiManager and WebRTCManager use these for motor data.
"""

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel
from scipy.spatial.transform import Rotation as R


class Matrix4x4Pose(BaseModel):
    """Represent a 3D pose by its 4x4 transformation matrix.

    Translation is expressed in meters.
    """

    m: tuple[
        float, float, float, float,
        float, float, float, float,
        float, float, float, float,
        float, float, float, float,
    ]

    @classmethod
    def from_numpy(cls, arr: NDArray[np.float64]) -> "Matrix4x4Pose":
        """Create from a 4x4 numpy array."""
        assert arr.shape == (4, 4), "Array must be of shape (4, 4)"
        m: tuple[
            float, float, float, float,
            float, float, float, float,
            float, float, float, float,
            float, float, float, float,
        ] = tuple(arr.flatten().tolist())
        return cls(m=m)

    def to_numpy(self) -> NDArray[np.float64]:
        """Convert to a 4x4 numpy array."""
        return np.array(self.m).reshape((4, 4))

    # Aliases for backward compatibility
    @classmethod
    def from_pose_array(cls, arr: NDArray[np.float64]) -> "Matrix4x4Pose":
        """Alias for from_numpy."""
        return cls.from_numpy(arr)

    def to_pose_array(self) -> NDArray[np.float64]:
        """Alias for to_numpy."""
        return self.to_numpy()


class XYZRPYPose(BaseModel):
    """Represent a 3D pose using position and Euler angles.

    Position (x, y, z) in meters.
    Orientation (roll, pitch, yaw) in radians.
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    @classmethod
    def from_numpy(cls, arr: NDArray[np.float64]) -> "XYZRPYPose":
        """Create from a 4x4 numpy array."""
        assert arr.shape == (4, 4), "Array must be of shape (4, 4)"

        x, y, z = arr[0, 3], arr[1, 3], arr[2, 3]
        roll, pitch, yaw = R.from_matrix(arr[:3, :3]).as_euler("xyz")

        return cls(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw)

    def to_numpy(self) -> NDArray[np.float64]:
        """Convert to a 4x4 numpy array."""
        rotation = R.from_euler("xyz", [self.roll, self.pitch, self.yaw])
        pose_matrix = np.eye(4)
        pose_matrix[:3, 3] = [self.x, self.y, self.z]
        pose_matrix[:3, :3] = rotation.as_matrix()
        return pose_matrix

    # Aliases for backward compatibility
    @classmethod
    def from_pose_array(cls, arr: NDArray[np.float64]) -> "XYZRPYPose":
        """Alias for from_numpy."""
        return cls.from_numpy(arr)

    def to_pose_array(self) -> NDArray[np.float64]:
        """Alias for to_numpy."""
        return self.to_numpy()


# Union type for flexible pose representation
AnyPose = XYZRPYPose | Matrix4x4Pose


def pose_from_numpy(arr: NDArray[np.float64], use_matrix: bool = False) -> AnyPose:
    """Convert a numpy array to an AnyPose representation.

    Args:
        arr: 4x4 transformation matrix as numpy array.
        use_matrix: If True, return Matrix4x4Pose; otherwise XYZRPYPose.

    Returns:
        Pose in the requested format.

    """
    if use_matrix:
        return Matrix4x4Pose.from_numpy(arr)
    return XYZRPYPose.from_numpy(arr)


def pose_to_numpy(pose: AnyPose) -> NDArray[np.float64]:
    """Convert any pose to a 4x4 numpy array."""
    return pose.to_numpy()
