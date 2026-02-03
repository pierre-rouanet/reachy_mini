"""HTTP/WebSocket API client for Reachy Mini.

This module implements an API client that communicates with the Reachy Mini
daemon via HTTP REST endpoints and WebSocket for real-time streaming.
"""

import json
import logging
import threading
import time
from typing import Any

import httpx
import numpy as np
import numpy.typing as npt
from websockets.sync.client import connect as ws_connect

from reachy_mini.daemon.models import MotorState


class ApiClient:
    """HTTP/WebSocket API client for Reachy Mini."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        timeout: float = 5.0,
    ):
        """Initialize the API client.

        Args:
            host: The daemon host address.
            port: The daemon HTTP port.
            timeout: Default timeout for HTTP requests.

        """
        self.logger = logging.getLogger(__name__)
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.ws_base_url = f"ws://{host}:{port}"
        self.timeout = timeout

        self._http_client = httpx.Client(base_url=self.base_url, timeout=timeout)

        # State cache (updated by WebSocket streaming)
        self._last_state: MotorState | None = None

        # Connection state
        self._is_connected = False
        self._state_ws = None
        self._state_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Synchronization
        self.state_received = threading.Event()

    def connect(self, timeout: float = 5.0) -> None:
        """Connect to the daemon and start state streaming.

        Args:
            timeout: Maximum time to wait for initial state.

        Raises:
            TimeoutError: If no state is received within timeout.

        """
        self._stop_event.clear()
        self._state_thread = threading.Thread(target=self._stream_state, daemon=True)
        self._state_thread.start()

        # Wait for initial state
        if not self.state_received.wait(timeout):
            self.disconnect()
            raise TimeoutError("Timeout waiting for state from daemon.")

        self._is_connected = True
        self.logger.info("Connected to daemon at %s", self.base_url)

    def disconnect(self) -> None:
        """Disconnect from the daemon."""
        self._stop_event.set()
        self._is_connected = False

        if self._state_ws:
            try:
                self._state_ws.close()
            except Exception:
                pass

        self._http_client.close()

    def is_connected(self) -> bool:
        """Check if connected to the daemon."""
        return self._is_connected

    def _stream_state(self) -> None:
        """Stream state updates via WebSocket."""
        ws_url = f"{self.ws_base_url}/api/state/ws/full?frequency=50&with_head_joints=true&with_antenna_positions=true"

        while not self._stop_event.is_set():
            try:
                with ws_connect(ws_url) as ws:
                    self._state_ws = ws
                    while not self._stop_event.is_set():
                        try:
                            msg = ws.recv(timeout=1.0)
                            if msg:
                                self._handle_state_message(msg)
                        except TimeoutError:
                            continue
            except Exception as e:
                if not self._stop_event.is_set():
                    self.logger.warning("State WebSocket error: %s, reconnecting...", e)
                    time.sleep(1.0)

    def _handle_state_message(self, msg: str) -> None:
        """Handle incoming state message from WebSocket."""
        data = json.loads(msg)
        # The WebSocket returns FullState, we need to adapt it
        # For now, cache the raw data
        self._last_state_raw = data
        self.state_received.set()

    def get_state(self) -> dict[str, Any]:
        """Get the current state as a dict.

        Returns:
            The last received state from WebSocket streaming.

        """
        if not hasattr(self, "_last_state_raw") or self._last_state_raw is None:
            raise RuntimeError("No state received yet. Call connect() first.")
        return self._last_state_raw

    def get_head_pose(self) -> npt.NDArray[np.float64]:
        """Get the current head pose as a 4x4 matrix."""
        state = self.get_state()
        pose = state.get("head_pose")
        if pose is None:
            raise RuntimeError("No head pose in state.")

        if "m" in pose:
            # Matrix4x4Pose
            return np.array(pose["m"]).reshape(4, 4)
        else:
            # XYZRPYPose - convert to matrix
            from scipy.spatial.transform import Rotation as R

            rotation = R.from_euler("xyz", [pose["roll"], pose["pitch"], pose["yaw"]])
            matrix = np.eye(4)
            matrix[:3, 3] = [pose["x"], pose["y"], pose["z"]]
            matrix[:3, :3] = rotation.as_matrix()
            return matrix

    def get_joints(self) -> tuple[list[float], list[float]]:
        """Get the current joint positions.

        Returns:
            Tuple of (head_joints, antenna_joints).

        """
        state = self.get_state()
        head_joints = state.get("head_joints", [])
        antennas = state.get("antennas_position", [])
        return (list(head_joints), list(antennas))

    def get_body_yaw(self) -> float:
        """Get the current body yaw."""
        state = self.get_state()
        return state.get("body_yaw", 0.0)
