"""Protocol handler for streaming commands.

This module contains the business logic for handling streaming protocol
commands. It is transport-agnostic - the same handler works for WebSocket
and WebRTC data channels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from reachy_mini.daemon.streaming.messages import (
    AutomaticBodyRotationChangedEvent,
    CancelCommand,
    CancelledEvent,
    DaemonStatusEvent,
    ErrorEvent,
    GetDaemonStatusCommand,
    GetStatusCommand,
    GotoCommand,
    InboundMessage,
    ModeChangedEvent,
    SetAutomaticBodyRotationCommand,
    SetModeCommand,
    StatusEvent,
    SubscribeCommand,
    TargetCommand,
)
from reachy_mini.motion.manager import DuplicateMoveIdError

if TYPE_CHECKING:
    from reachy_mini.daemon.daemon import Daemon
    from reachy_mini.daemon.streaming.session import StreamingSession
    from reachy_mini.media.media_manager import MediaManager
    from reachy_mini.motion.manager import MotionManager
    from reachy_mini.motor_controller.abstract import MotorController


logger = logging.getLogger(__name__)


class ProtocolHandler:
    """Handles streaming protocol commands.

    This class contains all the business logic for processing streaming
    protocol commands. It is shared across all streaming sessions and
    is transport-agnostic.

    All responses are sent via session.send_event() rather than return values.
    This allows handlers to send multiple events or no events as needed.
    """

    def __init__(
        self,
        motor_controller: MotorController,
        motion_manager: MotionManager,
        audio: MediaManager | None,
        daemon: Daemon,
    ) -> None:
        """Initialize the protocol handler.

        Args:
            motor_controller: The motor controller instance.
            motion_manager: The motion manager for goto and move tracking.
            audio: Optional audio/media manager for DoA sensor.
            daemon: The daemon instance for status queries.

        """
        self._motor_controller = motor_controller
        self._motion_manager = motion_manager
        self._audio = audio
        self._daemon = daemon

    @property
    def motor_controller(self) -> MotorController:
        """Get the motor controller."""
        return self._motor_controller

    @property
    def motion_manager(self) -> MotionManager:
        """Get the motion manager."""
        return self._motion_manager

    @property
    def audio(self) -> MediaManager | None:
        """Get the audio manager (may be None)."""
        return self._audio

    @property
    def daemon(self) -> Daemon:
        """Get the daemon instance."""
        return self._daemon

    async def handle_command(
        self,
        cmd: InboundMessage,
        session: StreamingSession,
    ) -> None:
        """Process an incoming command.

        Responses are sent via session.send_event(). Some commands
        (like target) are fire-and-forget and send no response.

        Args:
            cmd: The parsed inbound command.
            session: The session that received the command.

        """
        if isinstance(cmd, TargetCommand):
            self._handle_target(cmd)

        elif isinstance(cmd, GotoCommand):
            await self._handle_goto(cmd, session)

        elif isinstance(cmd, SetModeCommand):
            await self._handle_set_mode(cmd, session)

        elif isinstance(cmd, CancelCommand):
            await self._handle_cancel(cmd, session)

        elif isinstance(cmd, SubscribeCommand):
            await self._handle_subscribe(cmd, session)

        elif isinstance(cmd, GetStatusCommand):
            await self._handle_get_status(session)

        elif isinstance(cmd, SetAutomaticBodyRotationCommand):
            await self._handle_set_automatic_body_rotation(cmd, session)

        elif isinstance(cmd, GetDaemonStatusCommand):
            await self._handle_get_daemon_status(session)

        else:
            logger.warning(f"Unknown command type: {type(cmd)}")
            await session.send_event(
                ErrorEvent(
                    message=f"Unknown command type: {type(cmd).__name__}",
                    code="UNKNOWN_COMMAND",
                )
            )

    def _handle_target(self, cmd: TargetCommand) -> None:
        """Handle target command (fire-and-forget, no response)."""
        target = cmd.target
        mc = self._motor_controller

        if mc.is_move_running:
            return  # Ignore while move is running

        if target.head_pose is not None:
            mc.set_target_head_pose(target.head_pose.to_pose_array())
        elif target.head_joints is not None:
            # API uses 6 stewart joints; motor controller expects 7 (body_yaw + stewart)
            body_yaw = mc.target_body_yaw or mc.get_present_body_yaw()
            full_joints = np.concatenate([[body_yaw], target.head_joints])
            mc.set_target_head_joint_positions(full_joints)

        if target.antennas is not None:
            mc.set_target_antenna_joint_positions(np.array(target.antennas))

        if target.body_rotation is not None:
            mc.set_target_body_yaw(target.body_rotation)

    async def _handle_goto(self, cmd: GotoCommand, session: StreamingSession) -> None:
        """Handle goto command (blocking or async)."""
        try:
            # TODO: Add support for head_joints (joint-space goto)
            # Currently only head_pose (task-space) is supported by MotionManager.goto_target()
            # When head_joints is provided, we should either:
            # 1. Add a goto_joints method to MotionManager, or
            # 2. Convert joints to pose via forward kinematics
            coro = self._motion_manager.goto_target(
                head=cmd.request.head_pose.to_pose_array()
                if cmd.request.head_pose
                else None,
                antennas=np.array(cmd.request.antennas)
                if cmd.request.antennas
                else None,
                body_yaw=cmd.request.body_rotation,
                duration=cmd.request.duration,
            )

            # Delegate to session for tracking and completion events
            await session.start_goto(coro, cmd.id)

        except DuplicateMoveIdError as e:
            await session.send_event(
                ErrorEvent(message=str(e), code="DUPLICATE_MOVE_ID")
            )

    async def _handle_set_mode(
        self, cmd: SetModeCommand, session: StreamingSession
    ) -> None:
        """Handle set_mode command."""
        self._motor_controller.set_motor_control_mode(cmd.mode)
        await session.send_event(ModeChangedEvent(mode=cmd.mode))

    async def _handle_cancel(
        self, cmd: CancelCommand, session: StreamingSession
    ) -> None:
        """Handle cancel command."""
        success = await session.cancel_goto(cmd.id)
        if success:
            await session.send_event(CancelledEvent(id=cmd.id))
        else:
            await session.send_event(
                ErrorEvent(
                    message=f"Unknown move ID: {cmd.id}",
                    code="UNKNOWN_MOVE_ID",
                )
            )

    async def _handle_subscribe(
        self, cmd: SubscribeCommand, session: StreamingSession
    ) -> None:
        """Handle subscribe command."""
        await session.start_state_stream(cmd)

    async def _handle_get_status(self, session: StreamingSession) -> None:
        """Handle get_status command."""
        mc = self._motor_controller
        available_sensors: list[str] = []

        if self._audio:
            available_sensors.append("doa")

        if hasattr(mc, "get_imu_data") and mc.get_imu_data() is not None:
            available_sensors.append("imu")

        await session.send_event(
            StatusEvent(
                motor_ready=mc.ready.is_set(),
                control_mode=mc.get_motor_control_mode(),
                available_sensors=available_sensors,
                automatic_body_rotation=mc.head_kinematics.automatic_body_yaw,
            )
        )

    async def _handle_set_automatic_body_rotation(
        self, cmd: SetAutomaticBodyRotationCommand, session: StreamingSession
    ) -> None:
        """Handle set_automatic_body_rotation command."""
        self._motor_controller.set_automatic_body_yaw(cmd.enabled)
        await session.send_event(AutomaticBodyRotationChangedEvent(enabled=cmd.enabled))

    async def _handle_get_daemon_status(self, session: StreamingSession) -> None:
        """Handle get_daemon_status command."""
        status = self._daemon.status()
        await session.send_event(DaemonStatusEvent(**status.model_dump()))
