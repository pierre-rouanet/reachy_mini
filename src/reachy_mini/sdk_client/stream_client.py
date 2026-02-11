"""Streaming API client for Reachy Mini.

This client uses the unified streaming endpoint for all communication -
state streaming and commands. It supports multiple transports (WebSocket,
WebRTC data channel) through the transport abstraction.

This is the recommended client for real-time control and teleoperation.

StreamClient is an async client for use with asyncio. For synchronous usage,
use the ReachyMini class which wraps StreamClient with a background event loop.
"""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from reachy_mini.daemon.models import (
    FullBodyTarget,
    FullState,
    GotoRequest,
    MotorControlMode,
)
from reachy_mini.daemon.models.pose import pose_from_numpy
from reachy_mini.daemon.streaming.messages import (
    CancelCommand,
    DaemonStatusEvent,
    GetDaemonStatusCommand,
    GetStatusCommand,
    GotoCommand,
    GotoDoneEvent,
    OutboundMessage,
    SetAutomaticBodyRotationCommand,
    SetModeCommand,
    StateEvent,
    StatusEvent,
    SubscribeCommand,
    TargetCommand,
    parse_outbound_message,
)
from reachy_mini.motion import MoveId, MoveStatus
from reachy_mini.sdk_client.transport import ClientTransport
from reachy_mini.utils.interpolation import InterpolationTechnique


class StreamClient:
    """Streaming API client using a pluggable transport.

    This client provides:
    - Real-time state streaming at configurable frequency
    - Low-latency target updates (fire-and-forget)
    - Async and blocking goto operations
    - Motor mode control

    The transport can be WebSocket (default) or WebRTC data channel.

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
        transport: Optional[ClientTransport] = None,
    ):
        """Initialize the streaming client.

        Args:
            host: The daemon host address.
            port: The daemon HTTP port.
            transport: Optional transport instance. If not provided,
                creates a WebSocket transport to ws://{host}:{port}/api/stream/ws.

        """
        self.logger = logging.getLogger(__name__)
        self.host = host
        self.port = port

        # Create default WebSocket transport if none provided
        # TODO: Make it possible to automatically find the best suitable transport.
        if transport is None:
            from reachy_mini.sdk_client.transports import WebSocketClientTransport

            transport = WebSocketClientTransport(host=host, port=port)

        self._transport = transport

        self._state_queue: asyncio.Queue[FullState] = asyncio.Queue()
        self._event_handlers: dict[str, asyncio.Queue[OutboundMessage]] = {}
        self._pending_gotos: dict[MoveId, asyncio.Future[MoveStatus]] = {}

    @property
    def uri(self) -> str:
        """Get the connection URI."""
        return self._transport.uri

    async def connect(self, timeout: float = 5.0) -> None:
        """Connect to the daemon streaming endpoint.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            ConnectionError: If unable to connect.

        """
        await self._transport.connect(timeout=timeout)
        self._transport.on_message(self._on_message)
        self._transport.on_close(self._on_close)
        self.logger.info("Connected to streaming endpoint at %s", self.uri)

    async def disconnect(self) -> None:
        """Disconnect from the daemon."""
        await self._transport.disconnect()

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

    async def _send_cmd(self, cmd: BaseModel) -> None:
        """Send a command to the server."""
        await self._transport.send(cmd.model_dump_json())

    async def _on_message(self, message: str) -> None:
        """Handle incoming message from transport."""
        try:
            event = parse_outbound_message(message)

            if isinstance(event, StateEvent):
                # Non-blocking put, drop old states if queue is full
                try:
                    self._state_queue.put_nowait(event.state)
                except asyncio.QueueFull:
                    try:
                        self._state_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    self._state_queue.put_nowait(event.state)

            elif isinstance(event, GotoDoneEvent):
                # Handle async goto completion
                if event.id and event.id in self._pending_gotos:
                    self._pending_gotos[event.id].set_result(event.status)
                # Handle blocking goto (put in event handler queue)
                if "goto_done" in self._event_handlers:
                    await self._event_handlers["goto_done"].put(event)

            elif event.event in self._event_handlers:
                await self._event_handlers[event.event].put(event)

        except Exception as e:
            self.logger.warning("Error processing event: %s", e)

    async def _on_close(self) -> None:
        """Handle transport close."""
        self.logger.debug("Transport connection closed")

    async def _request(
        self, cmd: BaseModel, event_type: str, timeout: float = 5.0
    ) -> OutboundMessage:
        """Send a command and wait for a specific response event.

        Args:
            cmd: The command to send.
            event_type: The event type to wait for.
            timeout: Maximum time to wait for the response.

        Returns:
            The parsed event model from the server.

        """
        self._event_handlers[event_type] = asyncio.Queue()
        try:
            await self._send_cmd(cmd)
            return await asyncio.wait_for(
                self._event_handlers[event_type].get(), timeout=timeout
            )
        finally:
            self._event_handlers.pop(event_type, None)

    # --- Commands ---

    async def get_status(self) -> StatusEvent:
        """Get daemon status.

        Returns:
            StatusEvent with motor_ready, control_mode, available_sensors.

        """
        event = await self._request(GetStatusCommand(), "status")
        assert isinstance(event, StatusEvent)
        return event

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
        await self._send_cmd(SubscribeCommand(fields=fields, sensors=sensors, frequency=frequency))

    async def set_mode(self, mode: MotorControlMode) -> None:
        """Set motor control mode.

        Args:
            mode: The desired motor mode.

        """
        await self._request(SetModeCommand(mode=mode), "mode_changed")

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
        target = FullBodyTarget(
            head_pose=pose_from_numpy(head) if head is not None else None,
            head_joints=head_joints if head is None else None,
            antennas=tuple(antennas) if antennas is not None else None,
            body_rotation=body_rotation,
        )
        await self._send_cmd(TargetCommand(target=target))

    @staticmethod
    def _build_goto_request(
        head: Optional[npt.NDArray[np.float64]] = None,
        head_joints: Optional[List[float]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
        duration: float = 1.0,
        interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
    ) -> GotoRequest:
        """Build a GotoRequest from the given parameters."""
        return GotoRequest(
            head_pose=pose_from_numpy(head) if head is not None else None,
            head_joints=head_joints if head is None else None,
            antennas=tuple(antennas) if antennas is not None else None,
            body_rotation=body_rotation,
            duration=duration,
            interpolation=interpolation,
        )

    async def goto(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        head_joints: Optional[List[float]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
        duration: float = 1.0,
        interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
    ) -> MoveStatus:
        """Execute a blocking interpolated movement.

        For head control, use either head (task-space) OR head_joints (joint-space),
        not both. If both are provided, head (task-space) takes precedence.

        Args:
            head: 4x4 pose matrix for head target (task-space control).
            head_joints: 6 stewart platform joint positions in radians (joint-space control).
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.
            duration: Movement duration in seconds.
            interpolation: Interpolation technique.

        Returns:
            Final move status.

        """
        request = self._build_goto_request(
            head, head_joints, antennas, body_rotation, duration, interpolation
        )
        event = await self._request(
            GotoCommand(request=request), "goto_done", timeout=duration + 10.0
        )
        assert isinstance(event, GotoDoneEvent)
        return event.status

    async def goto_async(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,
        head_joints: Optional[List[float]] = None,
        antennas: Optional[Union[npt.NDArray[np.float64], List[float]]] = None,
        body_rotation: Optional[float] = None,
        duration: float = 1.0,
        interpolation: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
        move_id: Optional[str] = None,
    ) -> MoveId:
        """Start an async interpolated movement.

        For head control, use either head (task-space) OR head_joints (joint-space),
        not both. If both are provided, head (task-space) takes precedence.

        Args:
            head: 4x4 pose matrix for head target (task-space control).
            head_joints: 6 stewart platform joint positions in radians (joint-space control).
            antennas: [right_angle, left_angle] in radians.
            body_rotation: Body rotation angle in radians.
            duration: Movement duration in seconds.
            interpolation: Interpolation technique.
            move_id: Optional client-provided move ID.

        Returns:
            Move ID for tracking.

        """
        if move_id is None:
            move_id = str(uuid.uuid4())

        request = self._build_goto_request(
            head, head_joints, antennas, body_rotation, duration, interpolation
        )

        # Set up future for completion tracking
        self._pending_gotos[move_id] = asyncio.get_event_loop().create_future()

        await self._send_cmd(GotoCommand(request=request, id=move_id))
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
            return await asyncio.wait_for(self._pending_gotos[move_id], timeout=timeout)
        finally:
            self._pending_gotos.pop(move_id, None)

    async def cancel(self, move_id: MoveId) -> None:
        """Cancel an async goto movement.

        Args:
            move_id: The move ID to cancel.

        """
        await self._request(CancelCommand(id=move_id), "cancelled")
        self._pending_gotos.pop(move_id, None)

    # --- State Streaming ---

    async def get_state(self) -> FullState:
        """Get the latest state from the stream.

        Returns:
            Latest robot state.

        Raises:
            TimeoutError: If no state received within timeout.
            ConnectionError: If connection is closed.

        """
        if not self._transport.is_connected:
            raise ConnectionError("Transport connection is closed")
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
        await self._request(
            SetAutomaticBodyRotationCommand(enabled=enabled),
            "automatic_body_rotation_changed",
        )

    async def get_automatic_body_rotation(self) -> bool:
        """Get automatic body rotation setting.

        Returns:
            True if automatic body rotation is enabled.

        """
        status = await self.get_status()
        return bool(status.automatic_body_rotation)

    async def get_daemon_status(self) -> DaemonStatusEvent:
        """Get full daemon status.

        Returns:
            DaemonStatusEvent with state, simulation_enabled,
            error, motor_controller_status, wlan_ip, version, etc.

        """
        event = await self._request(GetDaemonStatusCommand(), "daemon_status")
        assert isinstance(event, DaemonStatusEvent)
        return event
