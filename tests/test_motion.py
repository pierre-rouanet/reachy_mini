"""Unit tests for MotionManager.

Part 1: Move tracking state machine (no motor controller needed).
Part 2: Actual motion execution with MockupController (goto, play_move).
"""

import asyncio
import threading
from collections.abc import Generator

import numpy as np
import pytest

from reachy_mini.motion.manager import (
    DuplicateMoveIdError,
    MotionManager,
    MoveStatus,
)
from reachy_mini.motion.recorded_move import RecordedMove
from reachy_mini.motor_controller.mockup_sim.controller import MockupController

# ---------------------------------------------------------------------------
# Helper coroutines
# ---------------------------------------------------------------------------


async def instant() -> None:
    """Complete immediately."""


async def slow() -> None:
    """Block until cancelled."""
    await asyncio.sleep(10)


async def failing() -> None:
    """Raise an exception."""
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def motion_env() -> Generator[tuple[MotionManager, MockupController]]:
    """MotionManager + MockupController without a full Daemon."""
    controller = MockupController()
    t = threading.Thread(target=controller.wrapped_run, daemon=True)
    t.start()
    assert controller.ready.wait(timeout=5.0), "MockupController did not become ready"

    mgr = MotionManager()
    mgr.set_motor_controller(controller)
    mgr.set_audio(None)

    yield mgr, controller

    controller.should_stop.set()
    t.join(timeout=2.0)
    controller.close()


# ---------------------------------------------------------------------------
# Part 1: Move tracking (pure async, no motor controller)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_move_task_auto_id() -> None:
    """create_move_task with no ID generates a UUID string."""
    mgr = MotionManager()
    move_id, _ = mgr.create_move_task(instant())
    assert isinstance(move_id, str)
    assert len(move_id) > 0
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_create_move_task_custom_id() -> None:
    """create_move_task with a custom ID returns that exact ID."""
    mgr = MotionManager()
    move_id, _ = mgr.create_move_task(slow(), move_id="my-id")
    assert move_id == "my-id"
    await mgr.stop_move_task("my-id")


@pytest.mark.asyncio
async def test_create_move_task_duplicate_id() -> None:
    """Duplicate in-progress ID raises DuplicateMoveIdError."""
    mgr = MotionManager()
    mgr.create_move_task(slow(), move_id="dup")  # first call ok

    with pytest.raises(DuplicateMoveIdError):
        mgr.create_move_task(slow(), move_id="dup")  # duplicate raises

    await mgr.stop_move_task("dup")


@pytest.mark.asyncio
async def test_completed_id_can_be_reused() -> None:
    """A completed move ID can be reused for a new move."""
    mgr = MotionManager()
    move_id, task = mgr.create_move_task(instant(), move_id="reuse")
    await task
    assert mgr.get_move_status("reuse") == MoveStatus.Completed

    move_id, task = mgr.create_move_task(instant(), move_id="reuse")
    await task
    assert mgr.get_move_status("reuse") == MoveStatus.Completed


@pytest.mark.asyncio
async def test_move_status_completed() -> None:
    """An instant move transitions to Completed."""
    mgr = MotionManager()
    move_id, task = mgr.create_move_task(instant())
    await task
    assert mgr.get_move_status(move_id) == MoveStatus.Completed


@pytest.mark.asyncio
async def test_move_status_failed() -> None:
    """A failing move transitions to Failed."""
    mgr = MotionManager()
    move_id, task = mgr.create_move_task(failing())
    try:
        await task
    except RuntimeError:
        pass
    assert mgr.get_move_status(move_id) == MoveStatus.Failed


@pytest.mark.asyncio
async def test_move_status_lifecycle() -> None:
    """A slow move is InProgress, then Cancelled after stop."""
    mgr = MotionManager()
    move_id, task = mgr.create_move_task(slow(), move_id="life")
    assert mgr.get_move_status("life") == MoveStatus.InProgress

    status = await mgr.stop_move_task("life")
    assert status == MoveStatus.Cancelled


@pytest.mark.asyncio
async def test_move_status_not_found() -> None:
    """Unknown move ID returns NotFound."""
    mgr = MotionManager()
    assert mgr.get_move_status("unknown") == MoveStatus.NotFound


@pytest.mark.asyncio
async def test_stop_move_task() -> None:
    """stop_move_task cancels and returns Cancelled."""
    mgr = MotionManager()
    move_id, task = mgr.create_move_task(slow(), move_id="stop-me")
    status = await mgr.stop_move_task("stop-me")
    assert status == MoveStatus.Cancelled


@pytest.mark.asyncio
async def test_stop_unknown_move() -> None:
    """stop_move_task on unknown ID returns NotFound."""
    mgr = MotionManager()
    status = await mgr.stop_move_task("nope")
    assert status == MoveStatus.NotFound


@pytest.mark.asyncio
async def test_get_running_moves() -> None:
    """get_running_moves returns IDs of in-flight tasks."""
    mgr = MotionManager()
    _, task_a = mgr.create_move_task(slow(), move_id="a")
    _, task_b = mgr.create_move_task(slow(), move_id="b")

    running = mgr.get_running_moves()
    assert set(running) == {"a", "b"}

    await mgr.stop_move_task("a")
    await mgr.stop_move_task("b")
    assert mgr.get_running_moves() == []


# ---------------------------------------------------------------------------
# Part 2: Motion execution (requires MockupController)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goto_target(motion_env: tuple[MotionManager, MockupController]) -> None:
    """goto_target moves the head to the requested pose."""
    mgr, controller = motion_env

    target_pose = np.eye(4)
    await mgr.goto_target(
        head=target_pose,
        antennas=np.array([0.0, 0.0]),
        duration=0.5,
    )

    pose = controller.get_present_head_pose()
    np.testing.assert_allclose(pose, target_pose, atol=0.15)


@pytest.mark.asyncio
async def test_goto_target_as_tracked_task(
    motion_env: tuple[MotionManager, MockupController],
) -> None:
    """goto_target wrapped in create_move_task completes normally."""
    mgr, _ = motion_env

    move_id, task = mgr.create_move_task(
        mgr.goto_target(head=np.eye(4), antennas=np.array([0.0, 0.0]), duration=0.1),
        move_id="goto-1",
    )
    assert mgr.get_move_status(move_id) == MoveStatus.InProgress

    # Wait for completion
    await task
    assert mgr.get_move_status(move_id) == MoveStatus.Completed


@pytest.mark.asyncio
async def test_play_recorded_move(
    motion_env: tuple[MotionManager, MockupController],
) -> None:
    """play_move executes a synthetic RecordedMove to completion."""
    mgr, controller = motion_env

    # First go to identity so we start from a known clean state
    await mgr.goto_target(head=np.eye(4), antennas=np.array([0.0, 0.0]), duration=0.5)

    start_pose = np.eye(4)
    end_antennas = [0.1, -0.1]

    move_data = {
        "description": "test move",
        "time": [0.0, 0.15],
        "set_target_data": [
            {
                "head": start_pose.tolist(),
                "antennas": [0.0, 0.0],
                "body_yaw": 0.0,
            },
            {
                "head": start_pose.tolist(),
                "antennas": end_antennas,
                "body_yaw": 0.0,
            },
        ],
    }
    move = RecordedMove(move_data, sound_path=None)
    await mgr.play_move(move)

    # Antennas should have moved toward the end target
    final_antennas = controller.get_present_antenna_joint_positions()
    np.testing.assert_allclose(final_antennas, end_antennas, atol=0.15)


@pytest.mark.asyncio
async def test_cancel_goto_in_progress(
    motion_env: tuple[MotionManager, MockupController],
) -> None:
    """A long goto can be cancelled mid-flight."""
    mgr, _ = motion_env

    move_id, task = mgr.create_move_task(
        mgr.goto_target(head=np.eye(4), antennas=np.array([0.0, 0.0]), duration=5.0),
        move_id="long-goto",
    )
    assert mgr.get_move_status(move_id) == MoveStatus.InProgress

    status = await mgr.stop_move_task(move_id)
    assert status == MoveStatus.Cancelled


@pytest.mark.asyncio
async def test_is_move_running_flag(
    motion_env: tuple[MotionManager, MockupController],
) -> None:
    """is_move_running is True during a trajectory and False after."""
    mgr, controller = motion_env

    assert not controller.is_move_running

    # Start a long goto
    move_id, task = mgr.create_move_task(
        mgr.goto_target(head=np.eye(4), antennas=np.array([0.0, 0.0]), duration=5.0),
    )
    await asyncio.sleep(0.05)  # let the move start

    assert controller.is_move_running

    # set_target should be rejected while move is running
    controller.set_target_antenna_joint_positions(np.array([0.5, 0.5]))
    # The antennas should NOT have changed to our set_target value
    # because the running goto is overwriting targets each tick

    # Cancel and verify flag clears
    await mgr.stop_move_task(move_id)
    assert not controller.is_move_running

    # set_target should work now
    controller.set_target_antenna_joint_positions(np.array([0.5, 0.5]))
