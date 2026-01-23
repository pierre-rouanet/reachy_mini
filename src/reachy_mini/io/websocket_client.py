"""WebSocket client for Reachy Mini.

This module implements a WebSocket client that allows communication with the Reachy Mini
robot via WebSocket connections.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import numpy as np
import numpy.typing as npt
from websockets.sync.client import connect

from reachy_mini.daemon.app.models import (
    HeadPose,
    IMUData,
    JointPositions,
)
from reachy_mini.io.abstract import AbstractClient
from reachy_mini.io.protocol import AnyTaskRequest, TaskProgress, TaskRequest


class WebSocketClient(AbstractClient):
    """WebSocket client for Reachy Mini."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        robot_name: str = "reachy_mini",
    ):
        """Initialize the WebSocket client.

        Args:
            host: The host to connect to.
            port: The port to connect to.
            robot_name: Name of the robot (currently unused, for compatibility).

        """
        self.host = host
        self.port = port
        self.robot_name = robot_name
        self.base_url = f"ws://{host}:{port}/api"

        self.logger = logging.getLogger(__name__)

        # Events for data reception
        self.joint_position_received = threading.Event()
        self.head_pose_received = threading.Event()
        self.status_received = threading.Event()

        # Cached data
        self._last_head_joint_positions: list[float] | None = None
        self._last_antennas_joint_positions: list[float] | None = None
        self._last_head_pose: npt.NDArray[np.float64] | None = None
        self._recorded_data: list[dict] | None = None
        self._recorded_data_ready = threading.Event()
        self._is_alive = False
        self._last_status: dict = {}
        self._last_imu_data: IMUData | None = None

        # Task tracking
        self.tasks: dict[UUID, TaskState] = {}

        # WebSocket connections
        self._data_ws = None
        self._command_ws = None
        self._task_ws = None

        # Threads
        self._data_thread: threading.Thread | None = None
        self._task_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def wait_for_connection(self, timeout: float = 5.0) -> None:
        """Wait for the client to connect to the server.

        Args:
            timeout: Maximum time to wait for the connection in seconds.

        Raises:
            TimeoutError: If the connection is not established within the timeout period.

        """
        # Connect to full state stream
        try:
            # Use /ws/full with head_joints enabled, pose matrix format, IMU data, and recording consumption
            data_url = f"{self.base_url}/state/ws/full?with_head_joints=true&frequency=50.0&use_pose_matrix=true&with_imu=true&consume_recording=true"
            self._data_ws = connect(data_url)
            self.logger.info(f"Connected to state stream at {data_url}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to state stream: {e}")

        # Start data receiving thread
        self._data_thread = threading.Thread(
            target=self._receive_data_loop, daemon=True
        )
        self._data_thread.start()

        # Wait for initial data
        start = time.time()
        while not self.joint_position_received.wait(
            timeout=1.0
        ) or not self.head_pose_received.wait(timeout=1.0):
            if time.time() - start > timeout:
                self.disconnect()
                raise TimeoutError(
                    "Timeout while waiting for connection with the server."
                )
            self.logger.info("Waiting for connection with the server...")

        self._is_alive = True
        threading.Thread(target=self.check_alive, daemon=True).start()

    def check_alive(self) -> None:
        """Periodically check if the client is still connected to the server."""
        while not self._stop_event.is_set():
            self._is_alive = self.is_connected()
            time.sleep(1.0)

    def is_connected(self) -> bool:
        """Check if the client is connected to the server."""
        self.joint_position_received.clear()
        self.head_pose_received.clear()
        return self.joint_position_received.wait(
            timeout=1.0
        ) and self.head_pose_received.wait(timeout=1.0)

    def disconnect(self) -> None:
        """Disconnect the client from the server."""
        self._stop_event.set()
        if self._data_ws:
            try:
                self._data_ws.close()
            except Exception:
                pass
        if self._command_ws:
            try:
                self._command_ws.close()
            except Exception:
                pass
        if self._task_ws:
            try:
                self._task_ws.close()
            except Exception:
                pass

    def send_command(self, command: str) -> None:
        """Send a command to the server.

        Args:
            command: JSON string containing the command to send.

        """
        if not self._is_alive:
            raise ConnectionError("Lost connection with the server.")

        # Create command connection if not exists
        if self._command_ws is None:
            command_url = f"{self.base_url}/motors/ws/command"
            self._command_ws = connect(command_url)

        self._command_ws.send(command)

    def _receive_data_loop(self) -> None:
        """Receive data from the WebSocket in a loop."""
        assert self._data_ws is not None

        try:
            while not self._stop_event.is_set():
                try:
                    message = self._data_ws.recv(timeout=0.1, decode=True)
                    self._handle_data_message(message)
                except TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"Error receiving data: {e}")
                    break
        finally:
            self._is_alive = False

    def _receive_task_loop(self) -> None:
        """Receive task progress updates from the task WebSocket in a loop."""
        assert self._task_ws is not None
        try:
            while not self._stop_event.is_set():
                try:
                    message = self._task_ws.recv(timeout=0.1)
                    self._handle_task_progress(json.loads(message))
                except TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"Error receiving task progress: {e}")
                    break
        except Exception:
            pass

    def _handle_data_message(self, message: str) -> None:
        """Handle incoming FullState messages from /ws/full.

        Args:
            message: JSON string containing FullState data.

        Note: This now expects FullState format, not topic-wrapped messages.
              Missing: recording notifications, task progress.

        """
        try:
            state = json.loads(message)

            # Debug: Check if recording is present in any message
            if "recording" in state:
                self.logger.info(
                    f"Recording field present in message: {state['recording'] is not None}"
                )

            # Extract joint positions from FullState
            if "head_joints" in state and state["head_joints"]:
                head_joints = state["head_joints"]
                antennas = state.get("antennas_position", [0.0, 0.0])
                self._handle_joint_positions(
                    {
                        "head_joint_positions": head_joints,
                        "antennas_joint_positions": antennas,
                    }
                )

            # Extract head pose from FullState (Matrix4x4Pose format)
            if "head_pose" in state and state["head_pose"]:
                pose_data = state["head_pose"]
                # When use_pose_matrix=true, head_pose is {"m": [16 floats]}
                if isinstance(pose_data, dict) and "m" in pose_data:
                    # Extract the matrix as a list
                    matrix_list = list(pose_data["m"])
                    self._handle_head_pose({"head_pose": matrix_list})
                elif isinstance(pose_data, list):
                    # Already a list (shouldn't happen with use_pose_matrix=true)
                    self._handle_head_pose({"head_pose": pose_data})

            # Extract IMU data from FullState
            if "imu" in state and state["imu"]:
                self._last_imu_data = IMUData.model_validate_json(state["imu"])

            # Extract recording data from FullState
            if "recording" in state and state["recording"]:
                recording_data = state["recording"]
                self._recorded_data = recording_data["data"]
                self._recorded_data_ready.set()
                if self._recorded_data is not None:
                    self.logger.info(
                        f"Recorded data: {len(self._recorded_data)} frames received."
                    )

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    def _handle_joint_positions(self, payload: dict) -> None:
        """Handle incoming joint positions."""
        joint_positions = JointPositions.model_validate(payload)
        self._last_head_joint_positions = joint_positions.head_joint_positions
        self._last_antennas_joint_positions = joint_positions.antennas_joint_positions
        self.joint_position_received.set()

    def _handle_head_pose(self, payload: dict) -> None:
        """Handle incoming head pose."""
        head_pose = HeadPose.model_validate(payload)
        self._last_head_pose = np.array(head_pose.head_pose).reshape(4, 4)
        self.head_pose_received.set()

    def _handle_task_progress(self, payload: dict) -> None:
        """Handle task progress updates."""
        progress = TaskProgress.model_validate(payload)
        if progress.uuid in self.tasks:
            if progress.error:
                self.tasks[progress.uuid].error = progress.error
            if progress.finished:
                self.tasks[progress.uuid].event.set()

    def get_current_joints(self) -> tuple[list[float], list[float]]:
        """Get the current joint positions.

        Returns:
            Tuple of (head_joint_positions, antennas_joint_positions).

        """
        assert (
            self._last_head_joint_positions is not None
            and self._last_antennas_joint_positions is not None
        ), "No joint positions received yet. Wait for the client to connect."
        return (
            self._last_head_joint_positions.copy(),
            self._last_antennas_joint_positions.copy(),
        )

    def get_current_head_pose(self) -> npt.NDArray[np.float64]:
        """Get the current head pose as a 4x4 matrix.

        Returns:
            4x4 transformation matrix representing the head pose.

        """
        assert self._last_head_pose is not None, "No head pose received yet."
        return self._last_head_pose.copy()  # type: ignore[return-value]

    def wait_for_recorded_data(self, timeout: float = 5.0) -> bool:
        """Wait for recorded data to arrive from the daemon.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if data was received, False if timeout occurred.

        """
        return self._recorded_data_ready.wait(timeout)

    def get_recorded_data(
        self, wait: bool = True, timeout: float = 5.0
    ) -> list[dict] | None:
        """Get recorded trajectory data.

        Args:
            wait: If True, wait for data to arrive.
            timeout: Maximum time to wait in seconds.

        Returns:
            List of recorded frames or None if no data available.

        Raises:
            TimeoutError: If nothing shows up in time.

        """
        if wait and not self._recorded_data_ready.wait(timeout):
            raise TimeoutError("Recording not received in time.")
        self._recorded_data_ready.clear()
        if self._recorded_data is not None:
            return self._recorded_data.copy()
        return None

    def get_status(self, wait: bool = True, timeout: float = 5.0) -> dict:
        """Get the daemon status.

        Args:
            wait: If True, wait for status to arrive.
            timeout: Maximum time to wait in seconds.

        Returns:
            Dictionary containing daemon status information.

        Raises:
            TimeoutError: If status not received in time.

        """
        # For WebSocket, we fetch status via HTTP API
        import requests

        try:
            response = requests.get(
                f"http://{self.host}:{self.port}/api/daemon/status",
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise TimeoutError(f"Failed to get status: {e}")

    def get_current_imu_data(self) -> IMUData | None:
        """Get the current IMU sensor data.

        Returns:
            Dictionary with accelerometer, gyroscope, quaternion, temperature
            or None if IMU is not available or no data received yet.

        """
        if self._last_imu_data is None:
            return None
        return self._last_imu_data.copy()

    def send_task_request(self, task_req: AnyTaskRequest) -> UUID:
        """Send a task request to the server and return a unique task identifier.

        Args:
            task_req: The task request to send.

        Returns:
            UUID identifying the task.

        """
        if not self._is_alive:
            raise ConnectionError("Lost connection with the server.")

        task = TaskRequest(uuid=uuid4(), req=task_req, timestamp=datetime.now())
        self.tasks[task.uuid] = TaskState(event=threading.Event(), error=None)

        # Create task connection and start listener thread if not exists
        if self._task_ws is None:
            task_url = f"{self.base_url}/move/ws/task"
            self._task_ws = connect(task_url)
            self._task_thread = threading.Thread(
                target=self._receive_task_loop, daemon=True
            )
            self._task_thread.start()

        self._task_ws.send(task.model_dump_json())

        return task.uuid

    def wait_for_task_completion(self, task_uid: UUID, timeout: float = 5.0) -> None:
        """Wait for the specified task to complete.

        Args:
            task_uid: UUID of the task to wait for.
            timeout: Maximum time to wait in seconds.

        """
        if task_uid not in self.tasks:
            raise ValueError("Task not found.")

        self.tasks[task_uid].event.wait(timeout)

        if not self.tasks[task_uid].event.is_set():
            raise TimeoutError("Task did not complete in time.")
        if self.tasks[task_uid].error is not None:
            raise Exception(f"Task failed with error: {self.tasks[task_uid].error}")

        del self.tasks[task_uid]


@dataclass
class TaskState:
    """Represents the state of a task."""

    event: threading.Event
    error: str | None
