"""HTTP-only API client for Reachy Mini.

This is a test client that uses ONLY HTTP endpoints (no WebSocket).
It demonstrates that full robot control is possible via REST API alone.

For production use, prefer ApiClient which uses WebSocket for real-time
state streaming and lower latency target updates.

This module also contains pytest tests that verify HTTP-only API completeness.
"""

import logging
import time
from typing import Any, List, Optional, Union, cast

import httpx
import numpy as np
import numpy.typing as npt
import pytest

from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon
from reachy_mini.daemon.models import FullBodyTarget, FullState, MotorControlMode
from reachy_mini.daemon.models.motor_command import GotoRequest
from reachy_mini.daemon.models.pose import pose_from_numpy
from reachy_mini.daemon.streaming.messages import MoveId, MoveStatus
from reachy_mini.utils.interpolation import InterpolationTechnique

# Test configuration
_TEST_CONFIG = DaemonArgs(sim=True, headless=True, wake_up_on_start=False, use_audio=False, goto_sleep_on_stop=False)


class HttpOnlyClient:
    """HTTP-only API client for Reachy Mini.

    This client uses only HTTP REST endpoints - no WebSocket connections.
    Useful for simple integrations or environments where WebSocket is not available.

    Limitations compared to ApiClient:
    - State is polled on each get_state() call (no real-time streaming)
    - set_target() has higher latency (HTTP request per call)
    - wait_for_move_completion() polls status endpoint
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        timeout: float = 5.0,
    ):
        """Initialize the HTTP-only client.

        Args:
            host: The daemon host address.
            port: The daemon HTTP port.
            timeout: Default timeout for HTTP requests.

        """
        self.logger = logging.getLogger(__name__)
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

        self._http_client = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._is_connected = False

    def connect(self, timeout: float = 5.0) -> None:
        """Connect to the daemon.

        Verifies the daemon is reachable by fetching initial state.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            ConnectionError: If unable to connect to the daemon.

        """
        try:
            # Verify daemon is reachable
            response = self._http_client.get(
                "/api/state/full",
                params={"with_head_joints": "true"},
                timeout=timeout,
            )
            response.raise_for_status()
            self._is_connected = True
            self.logger.info("Connected to daemon at %s", self.base_url)
        except Exception as e:
            raise ConnectionError(f"Failed to connect to daemon: {e}") from e

    def disconnect(self) -> None:
        """Disconnect from the daemon."""
        self._is_connected = False
        self._http_client.close()

    def is_connected(self) -> bool:
        """Check if connected to the daemon."""
        return self._is_connected

    def get_state(
        self,
        with_head_joints: bool = True,
        with_target_head_pose: bool = False,
        with_sensors: Optional[str] = None,
    ) -> FullState:
        """Get the current robot state via HTTP.

        Args:
            with_head_joints: Include head joint positions.
            with_target_head_pose: Include target head pose.
            with_sensors: Comma-separated sensor types or "all".

        Returns:
            Current robot state.

        Raises:
            ConnectionError: If not connected.

        """
        if not self._is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        params = {
            "with_head_joints": str(with_head_joints).lower(),
            "with_target_head_pose": str(with_target_head_pose).lower(),
        }
        if with_sensors:
            params["sensors"] = with_sensors

        response = self._http_client.get("/api/state/full", params=params)
        response.raise_for_status()
        return FullState.model_validate(response.json())

    def get_status(self) -> dict[str, Any]:
        """Get the daemon status.

        Returns:
            Dictionary containing daemon status information.

        """
        response = self._http_client.get("/api/daemon/status")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def set_target(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
    ) -> None:
        """Set immediate target position via HTTP.

        Args:
            head: 4x4 pose matrix for the head target.
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.

        """
        if not self._is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        target = FullBodyTarget(
            head_pose=pose_from_numpy(head) if head is not None else None,
            antennas=(antennas[0], antennas[1]) if antennas is not None else None,
            body_rotation=body_rotation,
        )

        response = self._http_client.post(
            "/api/move/set_target",
            content=target.model_dump_json(exclude_none=True),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

    def goto(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
        duration: float = 1.0,
        interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> MoveId:
        """Execute an interpolated movement via HTTP.

        Args:
            head: 4x4 pose matrix for the head target.
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.
            duration: Movement duration in seconds.
            interpolation: Interpolation technique.
            wait: If True, block until movement completes.
            timeout: Maximum time to wait (only if wait=True).

        Returns:
            Move ID for tracking.

        Raises:
            TimeoutError: If wait=True and movement doesn't complete in time.

        """
        if not self._is_connected:
            raise ConnectionError("Not connected. Call connect() first.")

        req = GotoRequest(
            head_pose=pose_from_numpy(head) if head is not None else None,
            antennas=(antennas[0], antennas[1]) if antennas is not None else None,
            body_rotation=body_rotation,
            duration=duration,
            interpolation=interpolation,
        )

        response = self._http_client.post(
            "/api/move/goto",
            content=req.model_dump_json(exclude_none=True),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        move_id = response.json()["id"]

        if wait:
            self.wait_for_move_completion(move_id, timeout or (duration + 5.0))

        return move_id

    def get_move_status(self, move_id: MoveId) -> MoveStatus:
        """Get the status of a movement.

        Args:
            move_id: The move ID to check.

        Returns:
            Current status of the movement.

        """
        response = self._http_client.get(f"/api/move/goto/{move_id}")
        response.raise_for_status()
        return MoveStatus(response.json()["status"])

    def wait_for_move_completion(
        self,
        move_id: MoveId,
        timeout: float,
        poll_interval: float = 0.05,
    ) -> MoveStatus:
        """Wait for a movement to complete by polling status.

        Args:
            move_id: The move ID to wait for.
            timeout: Maximum time to wait in seconds.
            poll_interval: Time between status polls.

        Returns:
            Final status of the movement.

        Raises:
            TimeoutError: If movement doesn't complete within timeout.

        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_move_status(move_id)
            if status in (MoveStatus.Completed, MoveStatus.Failed, MoveStatus.Cancelled):
                return status
            time.sleep(poll_interval)

        raise TimeoutError(f"Timeout waiting for move {move_id} to complete.")

    def cancel_move(self, move_id: MoveId) -> MoveStatus:
        """Cancel a running movement.

        Args:
            move_id: The move ID to cancel.

        Returns:
            Status after cancellation.

        """
        response = self._http_client.post(f"/api/move/goto/{move_id}/cancel")
        response.raise_for_status()
        return MoveStatus(response.json()["status"])

    def set_motor_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode.

        Args:
            mode: The motor control mode.

        """
        response = self._http_client.post(f"/api/motors/set_mode/{mode.value}")
        response.raise_for_status()

    def get_motor_status(self) -> dict[str, Any]:
        """Get motor status.

        Returns:
            Motor status information.

        """
        response = self._http_client.get("/api/motors/status")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def get_automatic_body_rotation(self) -> bool:
        """Get the automatic body rotation setting."""
        response = self._http_client.get("/api/motors/automatic_body_rotation")
        response.raise_for_status()
        return cast(bool, response.json())

    def set_automatic_body_rotation(self, enabled: bool) -> None:
        """Set the automatic body rotation setting."""
        response = self._http_client.post(
            f"/api/motors/automatic_body_rotation/{str(enabled).lower()}"
        )
        response.raise_for_status()

    # Convenience methods

    def enable_motors(self) -> None:
        """Enable all motors."""
        self.set_motor_mode(MotorControlMode.Enabled)

    def disable_motors(self) -> None:
        """Disable all motors."""
        self.set_motor_mode(MotorControlMode.Disabled)

    def enable_gravity_compensation(self) -> None:
        """Enable gravity compensation mode."""
        self.set_motor_mode(MotorControlMode.GravityCompensation)


# --- Pytest Tests ---


@pytest.mark.asyncio
async def test_http_only_client_basic_operations() -> None:
    """Test HTTP-only client basic operations: connect, get_state, motor modes."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            # Test connection
            client.connect()
            assert client.is_connected()

            # Test get_state
            state = client.get_state()
            assert state is not None
            assert state.control_mode is not None

            # Test get_status
            status = client.get_status()
            assert status is not None
            assert "state" in status

            # Test motor mode changes
            client.enable_motors()
            state = client.get_state()
            assert state.control_mode == MotorControlMode.Enabled

            client.disable_motors()
            state = client.get_state()
            assert state.control_mode == MotorControlMode.Disabled

            client.enable_gravity_compensation()
            state = client.get_state()
            assert state.control_mode == MotorControlMode.GravityCompensation

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_http_only_client_set_target() -> None:
    """Test HTTP-only client set_target operation."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            client.connect()
            client.enable_motors()

            # Test set_target with head pose
            head_pose = np.eye(4)
            head_pose[2, 3] = 0.02  # Set z position
            client.set_target(head=head_pose)

            # Test set_target with antennas
            client.set_target(antennas=[0.1, -0.1])

            # Test set_target with body_rotation
            client.set_target(body_rotation=0.1)

            # Test combined target
            client.set_target(head=head_pose, antennas=[0.0, 0.0], body_rotation=0.0)

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_http_only_client_goto() -> None:
    """Test HTTP-only client goto operation with wait."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            client.connect()
            client.enable_motors()

            # Test goto with head pose (blocking)
            head_pose = np.eye(4)
            head_pose[2, 3] = 0.02
            move_id = client.goto(head=head_pose, duration=0.5, wait=True)
            assert move_id is not None

            # Verify move completed
            status = client.get_move_status(move_id)
            assert status == MoveStatus.Completed

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_http_only_client_goto_async() -> None:
    """Test HTTP-only client async goto operation."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            client.connect()
            client.enable_motors()

            # Test async goto (non-blocking)
            head_pose = np.eye(4)
            head_pose[2, 3] = 0.02
            move_id = client.goto(head=head_pose, duration=0.5, wait=False)
            assert move_id is not None

            # Check status while in progress
            status = client.get_move_status(move_id)
            assert status in (MoveStatus.InProgress, MoveStatus.Completed)

            # Wait for completion
            final_status = client.wait_for_move_completion(move_id, timeout=5.0)
            assert final_status == MoveStatus.Completed

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_http_only_client_cancel_move() -> None:
    """Test HTTP-only client cancel move operation."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            client.connect()
            client.enable_motors()

            # Start a long move
            head_pose = np.eye(4)
            head_pose[2, 3] = 0.02
            move_id = client.goto(head=head_pose, duration=5.0, wait=False)

            # Cancel it immediately
            status = client.cancel_move(move_id)
            assert status == MoveStatus.Cancelled

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_http_only_client_automatic_body_rotation() -> None:
    """Test HTTP-only client automatic body rotation setting."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            client.connect()

            # Get current setting
            initial = client.get_automatic_body_rotation()
            assert isinstance(initial, bool)

            # Toggle setting
            client.set_automatic_body_rotation(not initial)
            assert client.get_automatic_body_rotation() == (not initial)

            # Restore original
            client.set_automatic_body_rotation(initial)
            assert client.get_automatic_body_rotation() == initial

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_http_only_client_motor_status() -> None:
    """Test HTTP-only client motor status endpoint."""
    async with Daemon(_TEST_CONFIG):
        client = HttpOnlyClient()
        try:
            client.connect()

            status = client.get_motor_status()
            assert status is not None
            assert "mode" in status

        finally:
            client.disconnect()


def test_goto_request_requires_target() -> None:
    """Test that GotoRequest requires at least one target."""
    from pydantic import ValidationError

    from reachy_mini.daemon.models import GotoRequest

    # Should fail: no target provided
    try:
        GotoRequest(duration=1.0)
        assert False, "Should have raised ValidationError"
    except ValidationError as e:
        assert "At least one target" in str(e)

    # Should succeed: head_pose provided
    req = GotoRequest(
        head_pose={"x": 0, "y": 0, "z": 0.02, "roll": 0, "pitch": 0, "yaw": 0},
        duration=1.0,
    )
    assert req.head_pose is not None

    # Should succeed: only body_rotation provided
    req = GotoRequest(body_rotation=0.1, duration=1.0)
    assert req.body_rotation == 0.1


def test_duplicate_move_id_validation() -> None:
    """Test that duplicate move IDs are rejected only while in progress."""
    from reachy_mini.daemon.api.routers.move import (
        DuplicateMoveIdError,
        is_move_id_in_progress,
        move_completed,
        move_tasks,
    )

    # Clean state
    move_tasks.clear()
    move_completed.clear()

    # New ID should not be in progress
    assert not is_move_id_in_progress("test-id-123")

    # Simulate an active move
    move_tasks["test-id-123"] = None  # type: ignore

    # Now it should be in progress
    assert is_move_id_in_progress("test-id-123")

    # Clean up active, add to completed
    del move_tasks["test-id-123"]
    move_completed["test-id-123"] = MoveStatus.Completed

    # No longer in progress (completed moves don't block reuse)
    assert not is_move_id_in_progress("test-id-123")

    # Clean up
    move_completed.clear()
