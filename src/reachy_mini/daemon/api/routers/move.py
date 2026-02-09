"""Movement-related API routes.

This exposes:
- goto
- play (wake_up, goto_sleep)
- stop running moves
- set_target (HTTP endpoint)

For real-time target streaming, use the unified WebSocket endpoint at /api/stream/ws.
"""

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from huggingface_hub.errors import RepositoryNotFoundError

from reachy_mini.daemon.models import FullBodyTarget, GotoRequest
from reachy_mini.daemon.streaming.messages import (
    GotoDoneEvent,
    GotoStartedEvent,
    MoveStatus,
)
from reachy_mini.motion import MoveTracker
from reachy_mini.motion.manager import MotionManager
from reachy_mini.motion.recorded_move import RecordedMoves
from reachy_mini.motor_controller.abstract import MotorController

from ..dependencies import (
    get_motion_manager,
    get_motor_controller,
    get_move_tracker,
)


router = APIRouter(prefix="/move")


@router.get("/running")
async def get_running_moves(
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> list[dict[str, str]]:
    """Get a list of currently running move tasks."""
    return [{"id": move_id} for move_id in move_tracker.get_running_moves()]


@router.post("/goto")
async def goto(
    goto_req: GotoRequest,
    motion_manager: MotionManager = Depends(get_motion_manager),
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> GotoStartedEvent:
    """Request a movement to a specific target."""
    move_id = move_tracker.create_move_task(
        motion_manager.goto_target(
            head=goto_req.head_pose.to_pose_array() if goto_req.head_pose else None,
            antennas=np.array(goto_req.antennas) if goto_req.antennas else None,
            body_yaw=goto_req.body_rotation,  # API uses body_rotation, internal uses body_yaw
            duration=goto_req.duration,
        )
    )
    return GotoStartedEvent(id=move_id)


@router.post("/play/wake_up")
async def play_wake_up(
    motion_manager: MotionManager = Depends(get_motion_manager),
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> GotoStartedEvent:
    """Request the robot to wake up."""
    move_id = move_tracker.create_move_task(motion_manager.wake_up())
    return GotoStartedEvent(id=move_id)


@router.post("/play/goto_sleep")
async def play_goto_sleep(
    motion_manager: MotionManager = Depends(get_motion_manager),
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> GotoStartedEvent:
    """Request the robot to go to sleep."""
    move_id = move_tracker.create_move_task(motion_manager.goto_sleep())
    return GotoStartedEvent(id=move_id)


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
    move_tracker: MoveTracker = Depends(get_move_tracker),
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
    move_id = move_tracker.create_move_task(motion_manager.play_move(move))
    return GotoStartedEvent(id=move_id)


@router.post("/goto/{move_id}/cancel")
async def cancel_goto(
    move_id: str,
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> GotoDoneEvent:
    """Cancel a running goto movement."""
    status = await move_tracker.stop_move_task(move_id)
    return GotoDoneEvent(id=move_id, status=status)


@router.get("/goto/{move_id}")
async def get_goto_status(
    move_id: str,
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> GotoDoneEvent:
    """Get the status of a goto movement."""
    status = move_tracker.get_move_status(move_id)
    return GotoDoneEvent(id=move_id, status=status)


@router.post("/stop")
async def stop_move(
    move_id: str,
    move_tracker: MoveTracker = Depends(get_move_tracker),
) -> GotoDoneEvent:
    """Stop a running move task (deprecated, use DELETE /goto/{id})."""
    status = await move_tracker.stop_move_task(move_id)
    return GotoDoneEvent(id=move_id, status=status)


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
        # API uses 6 stewart joints; motor controller expects 7 (body_yaw + stewart)
        # Prepend current body_yaw to the stewart joints
        body_yaw = motor_controller.target_body_yaw or motor_controller.get_present_body_yaw()
        full_joints = np.concatenate([[body_yaw], target.head_joints])
        motor_controller.set_target_head_joint_positions(full_joints)

    if target.antennas is not None:
        motor_controller.set_target_antenna_joint_positions(np.array(target.antennas))

    if target.body_rotation is not None:
        motor_controller.set_target_body_yaw(target.body_rotation)  # API uses body_rotation, internal uses body_yaw

    return {"status": "ok"}
