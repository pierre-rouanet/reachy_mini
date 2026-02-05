"""Streaming API client for Reachy Mini.

This client uses the unified WebSocket streaming endpoint (/api/stream/ws)
for all communication - state streaming and commands.

This is the recommended client for real-time control and teleoperation.

StreamClient is an async client for use with asyncio. For synchronous usage,
use the ReachyMini class which wraps StreamClient with a background event loop.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import numpy as np
import numpy.typing as npt
import websockets
from websockets.asyncio.client import ClientConnection

from reachy_mini.daemon.models import FullState, MotorControlMode
from reachy_mini.daemon.models.pose import pose_from_numpy
from reachy_mini.daemon.streaming.messages import MoveId, MoveStatus
from reachy_mini.utils.interpolation import InterpolationTechnique


class StreamClient:
    """Streaming API client using the unified WebSocket endpoint.

    This client provides:
    - Real-time state streaming at configurable frequency
    - Low-latency target updates (fire-and-forget)
    - Async and blocking goto operations
    - Motor mode control

    Example:
        async with StreamClient() as client:
            await client.subscribe(fields=["head_pose"], frequency=50)
            await client.set_mode(MotorControlMode.Enabled)

            # Real-time control loop
            async for state in client.state_stream():
                target = compute_target(state)
                await client.set_target(body_rotation=target)

    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
    ):
        """Initialize the streaming client.

        Args:
            host: The daemon host address.
            port: The daemon HTTP port.

        """
        self.logger = logging.getLogger(__name__)
        self.host = host
        self.port = port
        self.uri = f"ws://{host}:{port}/api/stream/ws"

        self._ws: Optional[ClientConnection] = None
        self._state_queue: asyncio.Queue[FullState] = asyncio.Queue()
        self._event_handlers: dict[str, asyncio.Queue[Any]] = {}
        self._receive_task: Optional[asyncio.Task[None]] = None
        self._pending_gotos: dict[MoveId, asyncio.Future[MoveStatus]] = {}

    async def connect(self, timeout: float = 5.0) -> None:
        """Connect to the daemon streaming endpoint.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            ConnectionError: If unable to connect.

        """
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.uri),
                timeout=timeout,
            )
            self._receive_task = asyncio.create_task(self._receive_loop())
            self.logger.info("Connected to streaming endpoint at %s", self.uri)
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {self.uri}: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the daemon."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "StreamClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def _send(self, cmd: dict[str, Any]) -> None:
        """Send a command to the server."""
        if not self._ws:
            raise ConnectionError("Not connected")
        await self._ws.send(json.dumps(cmd))

    async def _receive_loop(self) -> None:
        """Background task to receive and dispatch events."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    event = json.loads(message)
                    event_type = event.get("event")

                    if event_type == "state":
                        state = FullState.model_validate(event["state"])
                        # Non-blocking put, drop old states if queue is full
                        try:
                            self._state_queue.put_nowait(state)
                        except asyncio.QueueFull:
                            # Drop oldest and add new
                            try:
                                self._state_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            self._state_queue.put_nowait(state)

                    elif event_type == "goto_done":
                        move_id = event.get("id")
                        status = MoveStatus(event["status"])
                        # Handle async goto completion
                        if move_id and move_id in self._pending_gotos:
                            self._pending_gotos[move_id].set_result(status)
                        # Handle blocking goto (put in event handler queue)
                        if "goto_done" in self._event_handlers:
                            await self._event_handlers["goto_done"].put(event)

                    elif event_type == "goto_started":
                        # Just acknowledgment for async goto, no action needed
                        pass

                    elif event_type in self._event_handlers:
                        await self._event_handlers[event_type].put(event)

                except Exception as e:
                    self.logger.warning("Error processing event: %s", e)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error("Receive loop error: %s", e)

    async def _wait_for_event(
        self, event_type: str, timeout: Optional[float] = None
    ) -> dict[str, Any]:
        """Wait for a specific event type."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._event_handlers[event_type] = queue
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        finally:
            del self._event_handlers[event_type]

    # --- Commands ---

    async def get_status(self) -> dict[str, Any]:
        """Get daemon status.

        Returns:
            Status dictionary with motor_ready, control_mode, available_sensors.

        """
        self._event_handlers["status"] = asyncio.Queue()
        try:
            await self._send({"cmd": "get_status"})
            event = await asyncio.wait_for(
                self._event_handlers["status"].get(), timeout=5.0
            )
            return {
                "motor_ready": event["motor_ready"],
                "control_mode": event.get("control_mode"),
                "available_sensors": event.get("available_sensors", []),
            }
        finally:
            del self._event_handlers["status"]

    async def subscribe(
        self,
        fields: Optional[List[str]] = None,
        sensors: Optional[List[str]] = None,
        frequency: float = 50.0,
    ) -> None:
        """Configure state streaming.

        Args:
            fields: State fields to include (None = all).
            sensors: Sensor types to include.
            frequency: Update frequency in Hz (max 100).

        """
        cmd = {"cmd": "subscribe", "frequency": frequency}
        if fields is not None:
            cmd["fields"] = fields
        if sensors is not None:
            cmd["sensors"] = sensors
        await self._send(cmd)

    async def set_mode(self, mode: MotorControlMode) -> None:
        """Set motor control mode.

        Args:
            mode: The desired motor mode.

        """
        self._event_handlers["mode_changed"] = asyncio.Queue()
        try:
            await self._send({"cmd": "set_mode", "mode": mode.value})
            await asyncio.wait_for(
                self._event_handlers["mode_changed"].get(), timeout=5.0
            )
        finally:
            del self._event_handlers["mode_changed"]

    async def set_target(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        head_joints: Optional[List[float]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
    ) -> None:
        """Set immediate target position (fire-and-forget).

        For head control, use either head (task-space) OR head_joints (joint-space),
        not both. If both are provided, head (task-space) takes precedence.

        Args:
            head: 4x4 pose matrix for head target (task-space control).
            head_joints: 6 stewart platform joint positions in radians (joint-space control).
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.

        """
        target: dict[str, Any] = {}
        if head is not None:
            target["head_pose"] = pose_from_numpy(head).model_dump()
        elif head_joints is not None:
            target["head_joints"] = head_joints
        if antennas is not None:
            target["antennas"] = [antennas[0], antennas[1]]
        if body_rotation is not None:
            target["body_rotation"] = body_rotation

        await self._send({"cmd": "target", "target": target})

    async def goto(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
        duration: float = 1.0,
        interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
    ) -> MoveStatus:
        """Execute a blocking interpolated movement.

        Args:
            head: 4x4 pose matrix for head target.
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.
            duration: Movement duration in seconds.
            interpolation: Interpolation technique.

        Returns:
            Final move status.

        """
        request: dict[str, Any] = {
            "duration": duration,
            "interpolation": interpolation.value,
        }
        if head is not None:
            request["head_pose"] = pose_from_numpy(head).model_dump()
        if antennas is not None:
            request["antennas"] = [antennas[0], antennas[1]]
        if body_rotation is not None:
            request["body_rotation"] = body_rotation

        # Register handler for goto_done (blocking mode)
        self._event_handlers["goto_done"] = asyncio.Queue()
        try:
            await self._send({"cmd": "goto", "request": request})

            # Wait for goto_done event
            event = await asyncio.wait_for(
                self._event_handlers["goto_done"].get(),
                timeout=duration + 10.0,
            )
            return MoveStatus(event["status"])
        finally:
            self._event_handlers.pop("goto_done", None)

    async def goto_async(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
        duration: float = 1.0,
        interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
        move_id: Optional[str] = None,
    ) -> MoveId:
        """Start an async interpolated movement.

        Args:
            head: 4x4 pose matrix for head target.
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.
            duration: Movement duration in seconds.
            interpolation: Interpolation technique.
            move_id: Optional client-provided move ID.

        Returns:
            Move ID for tracking.

        """
        import uuid

        if move_id is None:
            move_id = str(uuid.uuid4())

        request: dict[str, Any] = {
            "duration": duration,
            "interpolation": interpolation.value,
        }
        if head is not None:
            request["head_pose"] = pose_from_numpy(head).model_dump()
        if antennas is not None:
            request["antennas"] = [antennas[0], antennas[1]]
        if body_rotation is not None:
            request["body_rotation"] = body_rotation

        # Set up future for completion tracking
        self._pending_gotos[move_id] = asyncio.get_event_loop().create_future()

        await self._send({"cmd": "goto", "request": request, "id": move_id})
        return move_id

    async def wait_for_goto(self, move_id: MoveId, timeout: float = 30.0) -> MoveStatus:
        """Wait for an async goto to complete.

        Args:
            move_id: The move ID to wait for.
            timeout: Maximum time to wait.

        Returns:
            Final move status.

        Raises:
            TimeoutError: If move doesn't complete in time.

        """
        if move_id not in self._pending_gotos:
            raise ValueError(f"Unknown move ID: {move_id}")

        try:
            return await asyncio.wait_for(
                self._pending_gotos[move_id], timeout=timeout
            )
        finally:
            self._pending_gotos.pop(move_id, None)

    async def cancel(self, move_id: MoveId) -> None:
        """Cancel an async goto movement.

        Args:
            move_id: The move ID to cancel.

        """
        self._event_handlers["cancelled"] = asyncio.Queue()
        try:
            await self._send({"cmd": "cancel", "id": move_id})
            await asyncio.wait_for(
                self._event_handlers["cancelled"].get(), timeout=5.0
            )
        finally:
            del self._event_handlers["cancelled"]
            self._pending_gotos.pop(move_id, None)

    # --- State Streaming ---

    async def get_state(self) -> FullState:
        """Get the latest state from the stream.

        Returns:
            Latest robot state.

        Raises:
            TimeoutError: If no state received within timeout.
            ConnectionError: If WebSocket connection is closed.

        """
        # Check if WebSocket is still connected
        if self._ws is None:
            raise ConnectionError("WebSocket connection is closed")
        # Check if connection is closed by examining the close code
        if self._ws.close_code is not None:
            raise ConnectionError("WebSocket connection is closed")
        return await asyncio.wait_for(self._state_queue.get(), timeout=5.0)

    async def state_stream(self) -> "AsyncGenerator[FullState, None]":
        """Async generator yielding state updates.

        Yields:
            FullState objects as they arrive.

        Example:
            async for state in client.state_stream():
                print(state.head_pose)

        """
        while True:
            yield await self._state_queue.get()

    # --- Convenience Methods ---

    async def enable_motors(self) -> None:
        """Enable all motors."""
        await self.set_mode(MotorControlMode.Enabled)

    async def disable_motors(self) -> None:
        """Disable all motors."""
        await self.set_mode(MotorControlMode.Disabled)

    async def enable_gravity_compensation(self) -> None:
        """Enable gravity compensation mode."""
        await self.set_mode(MotorControlMode.GravityCompensation)

    async def set_automatic_body_rotation(self, enabled: bool) -> None:
        """Set automatic body rotation.

        When enabled, the body rotation is automatically computed during IK
        to stay within mechanical limits.

        Args:
            enabled: Whether to enable automatic body rotation.

        """
        self._event_handlers["automatic_body_rotation_changed"] = asyncio.Queue()
        try:
            await self._send({"cmd": "set_automatic_body_rotation", "enabled": enabled})
            await asyncio.wait_for(
                self._event_handlers["automatic_body_rotation_changed"].get(), timeout=5.0
            )
        finally:
            del self._event_handlers["automatic_body_rotation_changed"]

    async def get_automatic_body_rotation(self) -> bool:
        """Get automatic body rotation setting.

        Returns:
            True if automatic body rotation is enabled.

        """
        status = await self.get_status()
        return bool(status.get("automatic_body_rotation", False))

    async def get_daemon_status(self) -> dict[str, Any]:
        """Get full daemon status.

        Returns:
            Dictionary with full daemon status including state, simulation_enabled,
            error, motor_controller_status, wlan_ip, version, etc.

        """
        self._event_handlers["daemon_status"] = asyncio.Queue()
        try:
            await self._send({"cmd": "get_daemon_status"})
            event = await asyncio.wait_for(
                self._event_handlers["daemon_status"].get(), timeout=5.0
            )
            # Remove the 'event' key and return the rest
            return {k: v for k, v in event.items() if k != "event"}
        finally:
            del self._event_handlers["daemon_status"]
