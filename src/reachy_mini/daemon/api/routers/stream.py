"""Unified streaming API endpoint.

This provides a single WebSocket endpoint that handles all commands
and streams all events, as defined in the streaming protocol (messages.py).

Commands (client → server): target, goto, set_mode, cancel, subscribe, get_status
Events (server → client): state, goto_started, goto_done, mode_changed, cancelled, error, status
"""

import asyncio
import time
from typing import Callable

import numpy as np
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from reachy_mini.daemon.models import FullState, pose_from_numpy
from reachy_mini.daemon.streaming import (
    AutomaticBodyRotationChangedEvent,
    CancelCommand,
    CancelledEvent,
    DaemonStatusEvent,
    ErrorEvent,
    GetDaemonStatusCommand,
    GetStatusCommand,
    GotoCommand,
    GotoDoneEvent,
    GotoStartedEvent,
    ModeChangedEvent,
    MoveStatus,
    SetAutomaticBodyRotationCommand,
    SetModeCommand,
    StateEvent,
    StatusEvent,
    SubscribeCommand,
    TargetCommand,
    parse_inbound_message,
)
from reachy_mini.media.media_manager import MediaManager
from reachy_mini.motion.manager import MotionManager
from reachy_mini.motor_controller.abstract import MotorController

from ..dependencies import get_audio, ws_get_motor_controller
from .move import (
    DuplicateMoveIdError,
    create_move_task,
    move_completed,
    move_tasks,
    stop_move_task,
)

router = APIRouter(prefix="/stream")

# Maximum streaming frequency (Hz)
MAX_STREAMING_FREQUENCY = 100.0


def _get_daemon(websocket: WebSocket):
    """Get the daemon instance from websocket app state."""
    return websocket.app.state.daemon


def _get_motion_manager(websocket: WebSocket) -> MotionManager:
    """Get the motion manager from websocket app state."""
    daemon = _get_daemon(websocket)
    return daemon.motion_manager


def _get_audio(websocket: WebSocket) -> MediaManager | None:
    """Get the audio manager from websocket app state (may be None)."""
    daemon = _get_daemon(websocket)
    return daemon.audio


async def _build_state(
    motor_controller: MotorController,
    audio: MediaManager | None,
    fields: list[str] | None,
    sensors: list[str] | None,
    use_pose_matrix: bool = False,
) -> FullState:
    """Build a FullState object based on requested fields and sensors."""
    # Default to all fields if not specified
    include_all = fields is None

    result: dict = {}

    if include_all or "control_mode" in fields:  # type: ignore
        result["control_mode"] = motor_controller.get_motor_control_mode().value

    if include_all or "head_pose" in fields:  # type: ignore
        pose = motor_controller.get_present_head_pose()
        result["head_pose"] = pose_from_numpy(pose, use_pose_matrix)

    if include_all or "target_head_pose" in fields:  # type: ignore
        target_pose = motor_controller.target_head_pose
        if target_pose is not None:
            result["target_head_pose"] = pose_from_numpy(target_pose, use_pose_matrix)

    if include_all or "head_joints" in fields:  # type: ignore
        head_joints = motor_controller.get_present_head_joint_positions()
        result["head_joints"] = list(head_joints[1:])  # Exclude body_rotation at index 0

    if include_all or "target_head_joints" in fields:  # type: ignore
        target = motor_controller.target_head_joint_positions
        if target is not None:
            result["target_head_joints"] = list(target[1:])

    if include_all or "body_rotation" in fields:  # type: ignore
        result["body_rotation"] = motor_controller.get_present_body_yaw()

    if include_all or "target_body_rotation" in fields:  # type: ignore
        result["target_body_rotation"] = motor_controller.target_body_yaw

    if include_all or "antennas" in fields:  # type: ignore
        pos = motor_controller.get_present_antenna_joint_positions()
        result["antennas"] = (pos[0], pos[1])

    if include_all or "target_antennas" in fields:  # type: ignore
        target = motor_controller.target_antenna_joint_positions
        if target is not None:
            result["target_antennas"] = (target[0], target[1])

    # Handle sensors
    result["sensors"] = {}
    if sensors:
        if "doa" in sensors and audio:
            doa_result = audio.get_DoA()
            if doa_result:
                from reachy_mini.daemon.models import DoAData

                result["sensors"]["doa"] = DoAData(
                    angle=doa_result[0], speech_detected=doa_result[1]
                )

        if "imu" in sensors:
            # IMU is available via motor controller on wireless version
            imu_data = motor_controller.get_imu_data() if hasattr(motor_controller, "get_imu_data") else None
            if imu_data:
                from reachy_mini.daemon.models import IMUData

                result["sensors"]["imu"] = IMUData(
                    accelerometer=tuple(imu_data["accelerometer"]),  # type: ignore
                    gyroscope=tuple(imu_data["gyroscope"]),  # type: ignore
                    quaternion=tuple(imu_data["quaternion"]),  # type: ignore
                    temperature=imu_data.get("temperature"),
                )

    result["timestamp"] = time.time()
    return FullState.model_validate(result)


async def _stream_state(
    websocket: WebSocket,
    motor_controller: MotorController,
    audio: MediaManager | None,
    config: SubscribeCommand,
    stop_event: asyncio.Event,
) -> None:
    """Stream state events at the configured frequency."""
    frequency = min(config.frequency, MAX_STREAMING_FREQUENCY)
    period = 1.0 / frequency

    while not stop_event.is_set():
        try:
            state = await _build_state(
                motor_controller,
                audio,
                config.fields,
                config.sensors,
            )
            event = StateEvent(state=state)
            await websocket.send_text(event.model_dump_json())
            await asyncio.sleep(period)
        except Exception:
            # Connection closed or other error
            break


@router.websocket("/ws")
async def unified_stream(
    websocket: WebSocket,
    motor_controller: MotorController = Depends(ws_get_motor_controller),
) -> None:
    """Unified WebSocket endpoint for bidirectional streaming.

    Handles all commands (target, goto, set_mode, cancel, subscribe, get_status)
    and streams events (state, goto_started, goto_done, mode_changed, etc.).
    """
    await websocket.accept()

    motion_manager = _get_motion_manager(websocket)
    audio = _get_audio(websocket)

    # State streaming task and control
    state_task: asyncio.Task | None = None
    state_stop_event = asyncio.Event()

    # Track goto completions to send events
    pending_gotos: dict[str, asyncio.Task] = {}

    async def send_event(event: object) -> None:
        """Send an event to the client."""
        try:
            if hasattr(event, "model_dump_json"):
                await websocket.send_text(event.model_dump_json())  # type: ignore
        except Exception:
            pass  # Connection may be closed

    async def handle_goto_completion(move_id: str, task: asyncio.Task) -> None:
        """Wait for a goto to complete and send the done event."""
        try:
            await task
            # Check final status from cache
            status = move_completed.get(move_id, MoveStatus.Completed)
            await send_event(GotoDoneEvent(id=move_id, status=status))
        except asyncio.CancelledError:
            await send_event(GotoDoneEvent(id=move_id, status=MoveStatus.Cancelled))
        except Exception as e:
            await send_event(GotoDoneEvent(id=move_id, status=MoveStatus.Failed))
        finally:
            pending_gotos.pop(move_id, None)

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = parse_inbound_message(data)
            except Exception as e:
                await send_event(
                    ErrorEvent(message=f"Invalid command: {e}", code="INVALID_COMMAND")
                )
                continue

            # Handle each command type
            if isinstance(msg, SubscribeCommand):
                # Stop existing state stream if any
                if state_task is not None:
                    state_stop_event.set()
                    state_task.cancel()
                    try:
                        await state_task
                    except asyncio.CancelledError:
                        pass

                # Start new state stream
                state_stop_event.clear()
                state_task = asyncio.create_task(
                    _stream_state(
                        websocket, motor_controller, audio, msg, state_stop_event
                    )
                )

            elif isinstance(msg, TargetCommand):
                # Fire-and-forget target update
                target = msg.target
                if motor_controller.is_move_running:
                    continue  # Ignore while move is running

                if target.head_pose is not None:
                    motor_controller.set_target_head_pose(target.head_pose.to_pose_array())
                elif target.head_joints is not None:
                    motor_controller.set_target_head_joint_positions(
                        np.array(target.head_joints)
                    )

                if target.antennas is not None:
                    motor_controller.set_target_antenna_joint_positions(
                        np.array(target.antennas)
                    )

                if target.body_rotation is not None:
                    motor_controller.set_target_body_yaw(target.body_rotation)

            elif isinstance(msg, GotoCommand):
                try:
                    # Create the goto coroutine
                    coro = motion_manager.goto_target(
                        head=msg.request.head_pose.to_pose_array()
                        if msg.request.head_pose
                        else None,
                        antennas=np.array(msg.request.antennas)
                        if msg.request.antennas
                        else None,
                        body_yaw=msg.request.body_rotation,
                        duration=msg.request.duration,
                    )

                    # Create task with optional client-provided ID
                    started_event = create_move_task(coro, move_id=msg.id)
                    move_id = started_event.id

                    if msg.id is not None:
                        # Async mode: send started event, track for completion
                        await send_event(started_event)
                        task = move_tasks.get(move_id)
                        if task:
                            pending_gotos[move_id] = task
                            asyncio.create_task(
                                handle_goto_completion(move_id, task)
                            )
                    else:
                        # Blocking mode: wait for completion, then send done
                        task = move_tasks.get(move_id)
                        if task:
                            try:
                                await task
                                status = move_completed.get(move_id, MoveStatus.Completed)
                            except asyncio.CancelledError:
                                status = MoveStatus.Cancelled
                            except Exception:
                                status = MoveStatus.Failed
                            await send_event(GotoDoneEvent(status=status))

                except DuplicateMoveIdError as e:
                    await send_event(
                        ErrorEvent(message=str(e), code="DUPLICATE_MOVE_ID")
                    )

            elif isinstance(msg, SetModeCommand):
                motor_controller.set_motor_control_mode(msg.mode)
                await send_event(ModeChangedEvent(mode=msg.mode))

            elif isinstance(msg, CancelCommand):
                if msg.id in move_tasks:
                    await stop_move_task(msg.id)
                    await send_event(CancelledEvent(id=msg.id))
                else:
                    await send_event(
                        ErrorEvent(
                            message=f"Unknown move ID: {msg.id}",
                            code="UNKNOWN_MOVE_ID",
                        )
                    )

            elif isinstance(msg, GetStatusCommand):
                available_sensors = []
                if audio:
                    available_sensors.append("doa")
                if hasattr(motor_controller, "get_imu_data") and motor_controller.get_imu_data() is not None:
                    available_sensors.append("imu")
                await send_event(
                    StatusEvent(
                        motor_ready=motor_controller.ready.is_set(),
                        control_mode=motor_controller.get_motor_control_mode(),
                        available_sensors=available_sensors,
                        automatic_body_rotation=motor_controller.head_kinematics.automatic_body_yaw,
                    )
                )

            elif isinstance(msg, SetAutomaticBodyRotationCommand):
                motor_controller.set_automatic_body_yaw(msg.enabled)
                await send_event(
                    AutomaticBodyRotationChangedEvent(enabled=msg.enabled)
                )

            elif isinstance(msg, GetDaemonStatusCommand):
                daemon = _get_daemon(websocket)
                status = daemon.status()
                # status is already a Pydantic DaemonStatus model with proper serialization
                await send_event(
                    DaemonStatusEvent(
                        robot_name=status.robot_name,
                        state=status.state,
                        wireless_version=status.wireless_version,
                        desktop_app_daemon=status.desktop_app_daemon,
                        simulation_enabled=status.simulation_enabled,
                        mockup_sim_enabled=status.mockup_sim_enabled,
                        motor_controller_status=status.motor_controller_status,
                        error=status.error,
                        wlan_ip=status.wlan_ip,
                        version=status.version,
                    )
                )

    except WebSocketDisconnect:
        pass
    finally:
        # Clean up state streaming
        if state_task is not None:
            state_stop_event.set()
            state_task.cancel()
            try:
                await state_task
            except asyncio.CancelledError:
                pass

        # Cancel any pending goto tracking tasks
        for task in pending_gotos.values():
            task.cancel()
