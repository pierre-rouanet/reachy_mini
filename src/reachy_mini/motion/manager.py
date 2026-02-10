"""Motion manager for Reachy Mini robot.

This module provides the MotionManager class that orchestrates motion,
combining motor control, audio, and move task tracking.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Coroutine, Optional
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from scipy.spatial.transform import Rotation as R

from reachy_mini.media.media_manager import MediaManager
from reachy_mini.motion.goto import GotoMove
from reachy_mini.motion.move import Move
from reachy_mini.utils.interpolation import (
    InterpolationTechnique,
    distance_between_poses,
)

if TYPE_CHECKING:
    from reachy_mini.motor_controller.abstract import MotorController

# Type alias for move identifiers
MoveId = str


class MoveStatus(str, Enum):
    """Status of a movement operation."""

    InProgress = "in_progress"
    Completed = "completed"
    Failed = "failed"
    Cancelled = "cancelled"
    NotFound = "not_found"


class DuplicateMoveIdError(Exception):
    """Raised when a move ID is already in use."""

    def __init__(self, move_id: MoveId) -> None:
        """Initialize with the duplicate move ID."""
        self.move_id = move_id
        super().__init__(f"Move ID '{move_id}' is already in use")


class MotionManager:
    """Manages robot motion with synchronized audio and move tracking.

    Orchestrates movement commands that require both motor control and audio,
    such as wake_up, goto_sleep, and play_move with sound.

    Also tracks async move tasks (goto, play, etc.) for lifecycle management:
    creating, querying status, and cancelling in-progress moves.
    """

    # Basic pose definitions
    INIT_HEAD_POSE = np.eye(4)

    SLEEP_ANTENNAS_JOINT_POSITIONS = np.array((-3.05, 3.05))
    SLEEP_HEAD_POSE = np.array(
        [
            [0.911, 0.004, 0.413, -0.021],
            [-0.004, 1.0, -0.001, 0.001],
            [-0.413, -0.001, 0.911, -0.044],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )

    def __init__(
        self,
        log_level: str = "INFO",
    ) -> None:
        """Initialize the MotionManager.

        Args:
            log_level: Logging level.

        """
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        # These are set by Daemon after initialization
        self._motor_controller: Optional["MotorController"] = None
        self._audio: Optional[MediaManager] = None

        # Move task tracking
        self._tasks: dict[MoveId, asyncio.Task[None]] = {}
        self._completed: dict[MoveId, MoveStatus] = {}
        self._max_completed_cache = 100

    def set_motor_controller(
        self, motor_controller: Optional["MotorController"]
    ) -> None:
        """Set the motor controller reference."""
        self._motor_controller = motor_controller

    def set_audio(self, audio: Optional[MediaManager]) -> None:
        """Set the audio manager reference."""
        self._audio = audio

    # --- Audio methods (internal) ---

    def _play_sound(self, sound_file: str) -> None:
        """Play a sound file.

        Args:
            sound_file: The name of the sound file to play (e.g., "wake_up.wav").

        """
        if self._audio:
            self._audio.start_playing()
            self._audio.play_sound(sound_file)

    def _stop_sound(self) -> None:
        """Stop any currently playing sound."""
        if self._audio:
            self._audio.stop_playing()

    # --- Move tracking methods ---

    @staticmethod
    def task_status(task: asyncio.Task[None]) -> MoveStatus:
        """Derive the status of a completed asyncio.Task.

        Args:
            task: A finished task.

        Returns:
            MoveStatus based on the task outcome.

        """
        if task.cancelled():
            return MoveStatus.Cancelled
        if task.exception() is not None:
            return MoveStatus.Failed
        return MoveStatus.Completed

    def get_running_moves(self) -> list[MoveId]:
        """Get list of currently running move IDs."""
        return list(self._tasks.keys())

    def is_move_in_progress(self, move_id: MoveId) -> bool:
        """Check if a move ID is currently in progress."""
        return move_id in self._tasks

    def get_move_status(self, move_id: MoveId) -> MoveStatus:
        """Get the status of a move.

        Args:
            move_id: The move ID to check.

        Returns:
            MoveStatus indicating current state.

        """
        if move_id in self._tasks:
            return MoveStatus.InProgress
        if move_id in self._completed:
            return self._completed[move_id]
        return MoveStatus.NotFound

    def create_move_task(
        self,
        coro: Coroutine[Any, Any, None],
        move_id: MoveId | None = None,
    ) -> tuple[MoveId, asyncio.Task[None]]:
        """Create and track a new move task.

        Args:
            coro: The coroutine to run as the move task.
            move_id: Optional client-provided move ID. If None, a UUID is generated.

        Returns:
            Tuple of (move_id, task).

        Raises:
            DuplicateMoveIdError: If the provided move_id is already in use.

        """
        if move_id is None:
            move_id = str(uuid4())
        elif self.is_move_in_progress(move_id):
            raise DuplicateMoveIdError(move_id)

        task = asyncio.create_task(coro)
        self._tasks[move_id] = task
        task.add_done_callback(lambda _t: self._on_move_done(move_id, _t))

        return move_id, task

    async def stop_move_task(self, move_id: MoveId) -> MoveStatus:
        """Stop a running move task by cancelling it.

        Args:
            move_id: The move ID to stop.

        Returns:
            MoveStatus.Cancelled if stopped, MoveStatus.NotFound if not found.

        """
        task = self._tasks.get(move_id)
        if task is None:
            return MoveStatus.NotFound

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        return MoveStatus.Cancelled

    def _on_move_done(self, move_id: MoveId, task: asyncio.Task[None]) -> None:
        """Done callback: clean up _tasks and cache terminal status."""
        self._tasks.pop(move_id, None)
        self._cache_completed(move_id, self.task_status(task))

    def _cache_completed(self, move_id: MoveId, status: MoveStatus) -> None:
        """Cache a completed move status, evicting oldest if at capacity."""
        if len(self._completed) >= self._max_completed_cache:
            oldest = next(iter(self._completed))
            self._completed.pop(oldest)
        self._completed[move_id] = status

    # --- Motion methods ---

    async def goto_target(
        self,
        head: Annotated[NDArray[np.float64], (4, 4)] | None = None,
        antennas: Annotated[NDArray[np.float64], (2,)] | None = None,
        duration: float = 0.5,
        method: InterpolationTechnique = InterpolationTechnique.MIN_JERK,
        body_yaw: float | None = 0.0,
    ) -> None:
        """Go to a target pose using task space interpolation.

        Args:
            head: 4x4 pose matrix for target head pose.
            antennas: [right_angle, left_angle] in radians.
            duration: Duration of the movement in seconds.
            method: Interpolation method ("linear", "minjerk", "ease", "cartoon").
            body_yaw: Body yaw angle in radians.

        """
        if self._motor_controller is None:
            raise RuntimeError("Motor controller not available")

        move = GotoMove(
            start_head_pose=self._motor_controller.get_present_head_pose(),
            target_head_pose=head,
            start_body_yaw=self._motor_controller.get_present_body_yaw(),
            target_body_yaw=body_yaw,
            start_antennas=np.array(
                self._motor_controller.get_present_antenna_joint_positions()
            ),
            target_antennas=np.array(antennas) if antennas is not None else None,
            duration=duration,
            method=method,
        )
        await self.play_move(move)

    async def play_move(
        self,
        move: Move,
        play_frequency: float = 100.0,
        initial_goto_duration: float = 0.0,
    ) -> None:
        """Play a Move with synchronized audio.

        Args:
            move: The Move object to be played.
            play_frequency: The frequency at which to evaluate the move (in Hz).
            initial_goto_duration: Duration for an initial goto to the move's starting position.

        """
        if self._motor_controller is None:
            raise RuntimeError("Motor controller not available")

        if self._motor_controller.is_move_running:
            self.logger.warning("Ignoring play_move request: another move is running.")
            return

        self._motor_controller._start_move()
        try:
            if initial_goto_duration > 0.0:
                start_head_pose, start_antennas_positions, start_body_yaw = (
                    move.evaluate(0.0)
                )
                await self.goto_target(
                    head=start_head_pose,
                    antennas=start_antennas_positions,
                    duration=initial_goto_duration,
                    body_yaw=start_body_yaw,
                )
            sleep_period = 1.0 / play_frequency

            if move.sound_path is not None:
                self._play_sound(str(move.sound_path))

            t0 = time.time()
            while time.time() - t0 < move.duration:
                t = time.time() - t0

                head, antennas, body_yaw = move.evaluate(t)
                if head is not None:
                    self._motor_controller.set_target_head_pose(head)
                if body_yaw is not None:
                    self._motor_controller.set_target_body_yaw(body_yaw)
                if antennas is not None:
                    self._motor_controller.set_target_antenna_joint_positions(antennas)

                elapsed = time.time() - t0 - t
                if elapsed < sleep_period:
                    await asyncio.sleep(sleep_period - elapsed)
                else:
                    await asyncio.sleep(0.001)
        finally:
            if move.sound_path is not None:
                self._stop_sound()
            self._motor_controller._end_move()

    async def wake_up(self) -> None:
        """Wake up the robot - move to initial position and play wake up sound."""
        if self._motor_controller is None:
            raise RuntimeError("Motor controller not available")

        await asyncio.sleep(0.1)

        _, _, magic_distance = distance_between_poses(
            self._motor_controller.get_current_head_pose(), self.INIT_HEAD_POSE
        )

        await self.goto_target(
            self.INIT_HEAD_POSE,
            antennas=np.array((0.0, 0.0)),
            duration=magic_distance * 20 / 1000,
        )
        await asyncio.sleep(0.1)

        # Toudoum
        self._play_sound("wake_up.wav")

        # Roll 20° to the left
        pose = self.INIT_HEAD_POSE.copy()
        pose[:3, :3] = R.from_euler("xyz", [20, 0, 0], degrees=True).as_matrix()
        await self.goto_target(pose, duration=0.2)

        # Go back to the initial position
        await self.goto_target(self.INIT_HEAD_POSE, duration=0.2)
        self._stop_sound()

    async def goto_sleep(self) -> None:
        """Put the robot to sleep - move to sleep position and play sleep sound."""
        if self._motor_controller is None:
            raise RuntimeError("Motor controller not available")

        _, _, dist_to_sleep_pose = distance_between_poses(
            self._motor_controller.get_current_head_pose(), self.SLEEP_HEAD_POSE
        )
        _, _, dist_to_init_pose = distance_between_poses(
            self._motor_controller.get_current_head_pose(), self.INIT_HEAD_POSE
        )
        sleep_time = 2.0

        if dist_to_sleep_pose > 10:
            if dist_to_init_pose > 30:
                await self.goto_target(
                    self.INIT_HEAD_POSE, antennas=np.array((0.0, 0.0)), duration=1
                )
                await asyncio.sleep(0.2)

            self._play_sound("go_sleep.wav")

            await self.goto_target(
                self.SLEEP_HEAD_POSE,
                antennas=self.SLEEP_ANTENNAS_JOINT_POSITIONS,
                duration=2,
            )
        else:
            self._play_sound("go_sleep.wav")
            sleep_time += 3

        await asyncio.sleep(sleep_time)
        self._stop_sound()
