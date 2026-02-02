"""Motion manager for Reachy Mini robot.

This module provides the MotionManager class that orchestrates motion,
combining motor control and audio for synchronized movements.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Annotated, Optional

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


class MotionManager:
    """Manages robot motion with synchronized audio.

    Orchestrates movement commands that require both motor control and audio,
    such as wake_up, goto_sleep, and play_move with sound.
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

    def set_motor_controller(self, motor_controller: Optional["MotorController"]) -> None:
        """Set the motor controller reference."""
        self._motor_controller = motor_controller

    def set_audio(self, audio: Optional[MediaManager]) -> None:
        """Set the audio manager reference."""
        self._audio = audio

    @property
    def motor_controller(self) -> Optional["MotorController"]:
        """Get the motor controller."""
        return self._motor_controller

    # Audio methods

    def play_sound(self, sound_file: str) -> None:
        """Play a sound file.

        Args:
            sound_file: The name of the sound file to play (e.g., "wake_up.wav").

        """
        if self._audio:
            self._audio.start_playing()
            self._audio.play_sound(sound_file)

    def stop_sound(self) -> None:
        """Stop any currently playing sound."""
        if self._audio:
            self._audio.stop_playing()

    # Motion methods

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
            start_antennas=np.array(self._motor_controller.get_present_antenna_joint_positions()),
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

        if not self._motor_controller._try_start_move():
            self.logger.warning("Ignoring play_move request: another move is running.")
            return

        try:
            if initial_goto_duration > 0.0:
                start_head_pose, start_antennas_positions, start_body_yaw = move.evaluate(0.0)
                await self.goto_target(
                    head=start_head_pose,
                    antennas=start_antennas_positions,
                    duration=initial_goto_duration,
                    body_yaw=start_body_yaw,
                )
            sleep_period = 1.0 / play_frequency

            if move.sound_path is not None:
                self.play_sound(str(move.sound_path))

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
                self.stop_sound()
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
        self.play_sound("wake_up.wav")

        # Roll 20° to the left
        pose = self.INIT_HEAD_POSE.copy()
        pose[:3, :3] = R.from_euler("xyz", [20, 0, 0], degrees=True).as_matrix()
        await self.goto_target(pose, duration=0.2)

        # Go back to the initial position
        await self.goto_target(self.INIT_HEAD_POSE, duration=0.2)
        self.stop_sound()

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

            self.play_sound("go_sleep.wav")

            await self.goto_target(
                self.SLEEP_HEAD_POSE,
                antennas=self.SLEEP_ANTENNAS_JOINT_POSITIONS,
                duration=2,
            )
        else:
            self.play_sound("go_sleep.wav")
            sleep_time += 3

        await asyncio.sleep(sleep_time)
        self.stop_sound()
