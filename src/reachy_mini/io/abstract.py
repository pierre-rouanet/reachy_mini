"""Base class for client implementations.

This abstract class defines the interface for client components that communicate
with the Reachy Mini daemon. Currently implemented by WebSocketClient, with plans
for future WebRTC client support.
"""

from abc import ABC, abstractmethod
from uuid import UUID

import numpy as np
import numpy.typing as npt

from reachy_mini.daemon.app.models import IMUData
from reachy_mini.io.protocol import AnyTaskRequest


class AbstractClient(ABC):
    """Base class for client implementations."""

    @abstractmethod
    def wait_for_connection(self, timeout: float = 5.0) -> None:
        """Wait for the client to connect to the server.

        Args:
            timeout: Maximum time to wait for the connection in seconds.

        """
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the client is connected to the server."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect the client from the server."""
        pass

    @abstractmethod
    def send_command(self, command: str) -> None:
        """Send a command to the server.

        Args:
            command: JSON string containing the command to send.

        """
        pass

    @abstractmethod
    def get_current_joints(self) -> tuple[list[float], list[float]]:
        """Get the current joint positions.

        Returns:
            Tuple of (head_joint_positions, antennas_joint_positions).

        """
        pass

    @abstractmethod
    def get_current_head_pose(self) -> "npt.NDArray[np.float64]":
        """Get the current head pose as a 4x4 matrix.

        Returns:
            4x4 transformation matrix representing the head pose.

        """
        pass

    @abstractmethod
    def wait_for_recorded_data(self, timeout: float = 5.0) -> bool:
        """Wait for recorded data to arrive from the daemon.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if data was received, False if timeout occurred.

        """
        pass

    @abstractmethod
    def get_recorded_data(
        self, wait: bool = True, timeout: float = 5.0
    ) -> list[dict] | None:
        """Get recorded trajectory data.

        Args:
            wait: If True, wait for data to arrive.
            timeout: Maximum time to wait in seconds.

        Returns:
            List of recorded frames or None if no data available.

        """
        pass

    @abstractmethod
    def get_status(self, wait: bool = True, timeout: float = 5.0) -> dict:
        """Get the daemon status.

        Args:
            wait: If True, wait for status to arrive.
            timeout: Maximum time to wait in seconds.

        Returns:
            Dictionary containing daemon status information.

        """
        pass

    @abstractmethod
    def get_current_imu_data(self) -> "IMUData | None":
        """Get the current IMU sensor data.

        Returns:
            Dictionary with accelerometer, gyroscope, quaternion, temperature
            or None if IMU is not available or no data received yet.

        """
        pass

    @abstractmethod
    def send_task_request(self, task_req: AnyTaskRequest) -> UUID:
        """Send a task request to the server and return a unique task identifier.

        Args:
            task_req: The task request to send.

        Returns:
            UUID identifying the task.

        """
        pass

    @abstractmethod
    def wait_for_task_completion(self, task_uid: UUID, timeout: float = 5.0) -> None:
        """Wait for the specified task to complete.

        Args:
            task_uid: UUID of the task to wait for.
            timeout: Maximum time to wait in seconds.

        """
        pass
