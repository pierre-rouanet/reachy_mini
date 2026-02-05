"""Movement-related API routes.

This exposes:
- goto
- play (wake_up, goto_sleep)
- stop running moves
- set_target and streaming set_target
"""

import asyncio
import json
from typing import Any, Coroutine
from uuid import UUID, uuid4

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from huggingface_hub.errors import RepositoryNotFoundError

from reachy_mini.daemon.models import FullBodyTarget, GotoRequest
from reachy_mini.daemon.streaming.messages import GotoDoneEvent, GotoStartedEvent, MoveId, MoveStatus
from reachy_mini.motion.manager import MotionManager
from reachy_mini.motion.recorded_move import RecordedMoves
from reachy_mini.motor_controller.abstract import MotorController

from ..dependencies import (
    get_motion_manager,
    get_motor_controller,
    ws_get_motor_controller,
)

move_tasks: dict[MoveId, asyncio.Task[None]] = {}
move_completed: dict[MoveId, MoveStatus] = {}  # Cache of completed move statuses
move_listeners: list[WebSocket] = []

# Max number of completed moves to cache (prevents unbounded growth)
_MAX_COMPLETED_CACHE = 100


router = APIRouter(prefix="/move")


class DuplicateMoveIdError(Exception):
    """Raised when a move ID is already in use."""

    def __init__(self, move_id: MoveId) -> None:
        self.move_id = move_id
        super().__init__(f"Move ID '{move_id}' is already in use")


def is_move_id_in_progress(move_id: MoveId) -> bool:
    """Check if a move ID is currently in progress."""
    return move_id in move_tasks


def create_move_task(
    coro: Coroutine[Any, Any, None], move_id: MoveId | None = None
) -> GotoStartedEvent:
    """Create a new move task using async task coroutine.

    Args:
        coro: The coroutine to run as the move task.
        move_id: Optional client-provided move ID. If None, a UUID is generated.

    Returns:
        GotoStartedEvent with the move ID.

    Raises:
        DuplicateMoveIdError: If the provided move_id is already in use.
    """
    if move_id is None:
        move_id = str(uuid4())
    elif is_move_id_in_progress(move_id):
        raise DuplicateMoveIdError(move_id)

    async def notify_listeners(message: str, details: str = "") -> None:
        for ws in move_listeners:
            try:
                await ws.send_json(
                    {
                        "type": message,
                        "id": move_id,
                        "details": details,
                    }
                )
            except (RuntimeError, WebSocketDisconnect):
                move_listeners.remove(ws)

    async def wrap_coro() -> None:
        status = MoveStatus.Completed
        try:
            await notify_listeners("move_started")
            await coro
            await notify_listeners("move_completed")
        except asyncio.CancelledError:
            status = MoveStatus.Cancelled
            await notify_listeners("move_cancelled")
        except Exception as e:
            status = MoveStatus.Failed
            await notify_listeners("move_failed", details=str(e))
        finally:
            move_tasks.pop(move_id, None)
            # Cache the final status for HTTP polling
            if len(move_completed) >= _MAX_COMPLETED_CACHE:
                # Remove oldest entry (first key)
                oldest = next(iter(move_completed))
                move_completed.pop(oldest)
            move_completed[move_id] = status

    task = asyncio.create_task(wrap_coro())
    move_tasks[move_id] = task

    return GotoStartedEvent(id=move_id)


async def stop_move_task(move_id: MoveId) -> GotoDoneEvent:
    """Stop a running move task by cancelling it."""
    if move_id not in move_tasks:
        return GotoDoneEvent(id=move_id, status=MoveStatus.NotFound)

    task = move_tasks.pop(move_id, None)
    assert task is not None

    if task:
        if task.cancel():
            try:
                await task
            except asyncio.CancelledError:
                pass

    return GotoDoneEvent(id=move_id, status=MoveStatus.Cancelled)


@router.get("/running")
async def get_running_moves() -> list[dict[str, str]]:
    """Get a list of currently running move tasks."""
    return [{"id": move_id} for move_id in move_tasks.keys()]


@router.post("/goto")
async def goto(
    goto_req: GotoRequest, motion_manager: MotionManager = Depends(get_motion_manager)
) -> GotoStartedEvent:
    """Request a movement to a specific target."""
    return create_move_task(
        motion_manager.goto_target(
            head=goto_req.head_pose.to_pose_array() if goto_req.head_pose else None,
            antennas=np.array(goto_req.antennas) if goto_req.antennas else None,
            body_yaw=goto_req.body_rotation,  # API uses body_rotation, internal uses body_yaw
            duration=goto_req.duration,
        )
    )


@router.post("/play/wake_up")
async def play_wake_up(motion_manager: MotionManager = Depends(get_motion_manager)) -> GotoStartedEvent:
    """Request the robot to wake up."""
    return create_move_task(motion_manager.wake_up())


@router.post("/play/goto_sleep")
async def play_goto_sleep(motion_manager: MotionManager = Depends(get_motion_manager)) -> GotoStartedEvent:
    """Request the robot to go to sleep."""
    return create_move_task(motion_manager.goto_sleep())


@router.get("/recorded-move-datasets/list/{dataset_name:path}")
async def list_recorded_move_dataset(
    dataset_name: str,
) -> list[str]:
    """List available recorded moves in a dataset."""
    try:
        moves = RecordedMoves(dataset_name)
    except RepositoryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return moves.list_moves()


@router.post("/play/recorded-move-dataset/{dataset_name:path}/{move_name}")
async def play_recorded_move_dataset(
    dataset_name: str,
    move_name: str,
    motion_manager: MotionManager = Depends(get_motion_manager),
) -> GotoStartedEvent:
    """Request the robot to play a predefined recorded move from a dataset."""
    try:
        recorded_moves = RecordedMoves(dataset_name)
    except RepositoryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        move = recorded_moves.get(move_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return create_move_task(motion_manager.play_move(move))


@router.post("/goto/{move_id}/cancel")
async def cancel_goto(move_id: str) -> GotoDoneEvent:
    """Cancel a running goto movement."""
    return await stop_move_task(move_id)


@router.get("/goto/{move_id}")
async def get_goto_status(move_id: str) -> GotoDoneEvent:
    """Get the status of a goto movement."""
    if move_id in move_tasks:
        return GotoDoneEvent(id=move_id, status=MoveStatus.InProgress)
    if move_id in move_completed:
        return GotoDoneEvent(id=move_id, status=move_completed[move_id])
    return GotoDoneEvent(id=move_id, status=MoveStatus.NotFound)


@router.post("/stop")
async def stop_move(move_id: str) -> GotoDoneEvent:
    """Stop a running move task (deprecated, use DELETE /goto/{id})."""
    return await stop_move_task(move_id)


@router.websocket("/ws/updates")
async def ws_move_updates(
    websocket: WebSocket,
) -> None:
    """WebSocket route to stream move updates."""
    await websocket.accept()
    try:
        move_listeners.append(websocket)
        while True:
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        move_listeners.remove(websocket)


# --- FullBodyTarget streaming and single set_target ---
@router.post("/set_target")
async def set_target(
    target: FullBodyTarget,
    motor_controller: MotorController = Depends(get_motor_controller),
) -> dict[str, str]:
    """POST route to set a single FullBodyTarget."""
    if motor_controller.is_move_running:
        # Avoid fighting with the daemon while a trajectory is running
        motor_controller.logger.warning("Ignoring set_target request: move already running.")
        return {"status": "ignored", "reason": "move_running"}

    # Task-space (cartesian) control takes precedence over joint-space
    if target.head_pose is not None:
        motor_controller.set_target_head_pose(target.head_pose.to_pose_array())
    elif target.head_joints is not None:
        motor_controller.set_target_head_joint_positions(np.array(target.head_joints))

    if target.antennas is not None:
        motor_controller.set_target_antenna_joint_positions(np.array(target.antennas))

    if target.body_rotation is not None:
        motor_controller.set_target_body_yaw(target.body_rotation)  # API uses body_rotation, internal uses body_yaw

    return {"status": "ok"}


@router.websocket("/ws/set_target")
async def ws_set_target(
    websocket: WebSocket, motor_controller: MotorController = Depends(ws_get_motor_controller)
) -> None:
    """WebSocket route to stream FullBodyTarget set_target calls."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            try:
                target = FullBodyTarget.model_validate_json(data)
                await set_target(target, motor_controller)

            except Exception as e:
                await websocket.send_text(
                    json.dumps({"status": "error", "detail": str(e)})
                )
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/raw/write")
async def write(
    websocket: WebSocket,
    motor_controller: MotorController = Depends(ws_get_motor_controller),
) -> None:
    """WebSocket endpoint to stream raw packet to the serialport and return any response buffer.

    Returns an empty bytes if no response is received.
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_bytes()
            raw_response_packet: bytes = motor_controller.write_raw_packet(data)
            await websocket.send_bytes(raw_response_packet)
    except WebSocketDisconnect:
        pass
