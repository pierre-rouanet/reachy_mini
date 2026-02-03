"""HTTP/WebSocket API client for Reachy Mini.

This module implements an API client that communicates with the Reachy Mini
daemon via HTTP REST endpoints and WebSocket for real-time streaming.
"""

import json
import logging
import threading
from typing import Any, List, Optional, Union, cast
from uuid import UUID

import httpx
import numpy as np
import numpy.typing as npt
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as ws_connect
from websockets.sync.connection import Connection

from reachy_mini.daemon.models import FullBodyTarget, FullState, MotorControlMode
from reachy_mini.daemon.models.motor_command import GotoRequest, MoveUUID
from reachy_mini.daemon.models.pose import pose_from_numpy
from reachy_mini.utils.interpolation import InterpolationTechnique


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
        self._last_state: FullState | None = None

        # Connection state
        self._is_connected = False
        self._state_ws: Connection | None = None
        self._command_ws: ClientConnection | None = None
        self._goto_ws: Connection | None = None
        self._state_thread: threading.Thread | None = None
        self._goto_update_thread: threading.Thread | None = None
        self._goto_wait_events: dict[UUID, threading.Event] = {}
        self._stop_event = threading.Event()

        # Synchronization
        self.state_received = threading.Event()
        self._command_ws_lock = threading.Lock()

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

        self._goto_update_thread = threading.Thread(
            target=self._stream_goto_updates, daemon=True
        )
        self._goto_update_thread.start()

        # Connect command WebSocket
        cmd_ws_url = f"{self.ws_base_url}/api/move/ws/set_target"
        self._command_ws = ws_connect(cmd_ws_url)

        # Wait for initial state with valid data
        if not self.state_received.wait(timeout) or self._last_state is None:
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

        if self._command_ws:
            try:
                self._command_ws.close()
            except Exception:
                pass

        if self._goto_ws:
            try:
                self._goto_ws.close()
            except Exception:
                pass

        self._http_client.close()

    def is_connected(self) -> bool:
        """Check if connected to the daemon."""
        return self._is_connected

    def _stream_state(self) -> None:
        """Stream state updates via WebSocket."""
        ws_url = f"{self.ws_base_url}/api/state/ws/full?frequency=50&with_head_joints=true&with_antenna_positions=true"

        try:
            with ws_connect(ws_url) as ws:
                self._state_ws = ws
                while not self._stop_event.is_set():
                    try:
                        msg = ws.recv(timeout=1.0)
                        if msg:
                            self._handle_state_message(cast(str, msg))
                    except TimeoutError:
                        continue
        except Exception as e:
            if not self._stop_event.is_set():
                self.logger.warning("State WebSocket error: %s", e)
        finally:
            self._is_connected = False

    def _handle_state_message(self, msg: str) -> None:
        """Handle incoming state message from WebSocket."""
        self._last_state = FullState.model_validate_json(msg)
        self.state_received.set()

    def get_state(self) -> FullState:
        """Get the current state.

        Returns:
            The last received FullState from WebSocket streaming.

        Raises:
            ConnectionError: If the connection to the daemon is lost.

        """
        if not self._is_connected:
            raise ConnectionError("Lost connection with the server.")
        assert self._last_state is not None, (
            "No state received yet. Call connect() first."
        )
        return self._last_state

    def get_status(self) -> dict[str, Any]:
        """Get the daemon status.

        Returns:
            Dictionary containing daemon status information.

        """
        response = self._http_client.get("/api/daemon/status")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def _stream_goto_updates(self) -> None:
        """Stream goto updates via WebSocket."""
        ws_url = f"{self.ws_base_url}/api/move/ws/updates"

        try:
            with ws_connect(ws_url) as ws:
                self._goto_ws = ws
                while not self._stop_event.is_set():
                    try:
                        msg = ws.recv(timeout=1.0)
                        if msg:
                            self._handle_goto_update_message(cast(str, msg))
                    except TimeoutError:
                        continue
        except Exception as e:
            if not self._stop_event.is_set():
                self.logger.warning("Goto updates WebSocket error: %s", e)
        finally:
            self._is_connected = False

    def _handle_goto_update_message(self, msg: str) -> None:
        """Handle incoming goto update message from WebSocket."""
        update = json.loads(msg)

        assert "uuid" in update, "Invalid goto update message: missing 'uuid'"
        assert "type" in update, "Invalid goto update message: missing 'type'"

        move_uuid = UUID(update["uuid"])
        update_type = update["type"]

        if update_type in ("move_completed", "move_failed", "move_cancelled"):
            if move_uuid in self._goto_wait_events:
                event = self._goto_wait_events.pop(move_uuid)
                event.set()

    def send_goto_request(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,  # 4x4 pose matrix
        antennas: Optional[
            Union[npt.NDArray[np.float64], List[float]]
        ] = None,  # [right_angle, left_angle] (in rads)
        duration: float = 0.5,  # Duration in seconds for the movement, default is 0.5 seconds.
        method: InterpolationTechnique = InterpolationTechnique.MIN_JERK,  # can be "linear", "minjerk", "ease" or "cartoon", default is "minjerk")
        body_yaw: float | None = 0.0,  # Body yaw angle in radians
    ) -> MoveUUID:
        """Send a goto request to the daemon.

        Args:
            head: 4x4 pose matrix for the head target.
            antennas: List or array of two floats for right and left antenna angles (in radians).
            duration: Duration in seconds for the movement.
            method: Interpolation technique to use.
            body_yaw: Body yaw angle in radians.

        Returns:
            MoveUUID: The UUID of the initiated move.

        """
        req = GotoRequest(
            head_pose=pose_from_numpy(head) if head is not None else None,
            antennas=((antennas[0], antennas[1]) if antennas is not None else None),
            duration=duration,
            interpolation=method,
            body_yaw=body_yaw,
        )
        response = self._http_client.post(
            "/api/move/goto", content=req.model_dump_json(exclude_none=True)
        )
        response.raise_for_status()
        move_uuid = MoveUUID.model_validate(response.json())
        # Create the event immediately to avoid race condition with WebSocket updates
        self._goto_wait_events[move_uuid.uuid] = threading.Event()
        return move_uuid

    def wait_for_move_completion(
        self, move_uuid: MoveUUID, timeout: Optional[float] = None
    ) -> None:
        """Wait for the completion of a move.

        Args:
            move_uuid: The UUID of the move to wait for.
            timeout: Maximum time to wait in seconds. If None, wait indefinitely.

        Raises:
            TimeoutError: If the move does not complete within the specified timeout.

        """
        if move_uuid.uuid not in self._goto_wait_events:
            raise ValueError(f"No ongoing move with UUID: {move_uuid.uuid}")

        event = self._goto_wait_events[move_uuid.uuid]
        completed = event.wait(timeout)
        if not completed:
            raise TimeoutError(f"Timeout waiting for move {move_uuid.uuid} to complete.")

    def send_target(self, target: FullBodyTarget) -> None:
        """Send a target command to the robot via WebSocket.

        Args:
            target: The target command containing head pose, antennas, and/or body yaw.

        Raises:
            ConnectionError: If the connection to the daemon is lost.
            RuntimeError: If not connected.

        """
        if self._command_ws is None:
            raise RuntimeError("Not connected. Call connect() first.")

        with self._command_ws_lock:
            try:
                self._command_ws.send(target.model_dump_json(exclude_none=True))
            except Exception as e:
                self._is_connected = False
                raise ConnectionError("Lost connection with the server.") from e

    def set_motor_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode.

        Args:
            mode: The motor control mode (enabled, disabled, gravity_compensation).

        """
        response = self._http_client.post(f"/api/motors/set_mode/{mode.value}")
        response.raise_for_status()

    def get_automatic_body_yaw(self) -> bool:
        """Get the automatic body yaw setting.

        Returns:
            True if automatic body yaw is enabled.

        """
        response = self._http_client.get("/api/motors/automatic_body_yaw")
        response.raise_for_status()
        return cast(bool, response.json())

    def set_automatic_body_yaw(self, enabled: bool) -> None:
        """Set the automatic body yaw setting.

        When enabled, the body yaw is automatically computed during IK
        to stay within mechanical limits.

        Args:
            enabled: Whether to enable automatic body yaw.

        """
        response = self._http_client.post(
            f"/api/motors/automatic_body_yaw/{str(enabled).lower()}"
        )
        response.raise_for_status()
