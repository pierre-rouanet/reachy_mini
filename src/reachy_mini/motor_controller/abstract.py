"""Base class for motor controllers, simulated or real.

This module defines the `MotorController` class, which serves as a base for implementing
different types of motor controllers, whether they are simulated (like Mujoco) or real
(connected via serial port). The class provides methods for managing joint positions,
torque control, and other controller-specific functionalities.
It is designed to be extended by subclasses that implement the specific behavior for
each type of controller.
"""

import asyncio
import logging
import threading
import time
import typing
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Dict, Optional

import numpy as np
from numpy.typing import NDArray
from scipy.spatial.transform import Rotation as R

if typing.TYPE_CHECKING:
    from reachy_mini.kinematics import AnyKinematics
from reachy_mini.media.media_manager import MediaBackend, MediaManager
from reachy_mini.motion.goto import GotoMove
from reachy_mini.motion.move import Move
from reachy_mini.utils.constants import MODELS_ROOT_PATH, URDF_ROOT_PATH
from reachy_mini.utils.interpolation import (
    InterpolationTechnique,
    distance_between_poses,
    time_trajectory,
)


class MotorControlMode(str, Enum):
    """Enum for motor control modes."""

    Enabled = "enabled"  # Torque ON and controlled in position
    Disabled = "disabled"  # Torque OFF
    GravityCompensation = "gravity_compensation"  # Torque ON and controlled in current to compensate for gravity


@dataclass
class MotorControllerStatus:
    """Base status for all motor controllers."""

    motor_control_mode: MotorControlMode
    error: str | None = None
    ready: bool = False
    last_alive: float | None = None
    control_loop_stats: dict[str, Any] = field(default_factory=dict)


class MotorController(ABC):
    """Abstract base class for motor controllers, simulated or real.

    This class implements a template method pattern for the control loop.
    Subclasses must implement the abstract methods to provide hardware/sim-specific behavior.

    Abstract methods (must implement):
        - _read_joint_positions(): Read current positions from hardware/sim
        - _apply_targets(): Write target positions to hardware/sim
        - get_motor_control_mode(): Get current motor control mode
        - set_motor_control_mode(): Set motor control mode
        - set_motor_torque_ids(): Set torque for specific motors

    Hook methods (may override):
        - _on_start(): Called once before the control loop starts
        - _on_update(): Called each iteration after state update
        - _on_stop(): Called once after the control loop ends
        - _wait_for_tick(): Controls timing (default: Python sleep, override for Rust-synced timing)
    """

    # Control loop frequency in Hz (can be overridden by subclasses)
    control_frequency: float = 50.0

    def __init__(
        self,
        log_level: str = "INFO",
        check_collision: bool = False,
        kinematics_engine: str = "AnalyticalKinematics",
        use_audio: bool = True,
        wireless_version: bool = False,
    ) -> None:
        """Initialize the backend."""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        self.use_audio = use_audio

        self.should_stop = threading.Event()
        self.ready = threading.Event()

        self.check_collision = (
            check_collision  # Flag to enable/disable collision checking
        )
        self.kinematics_engine = kinematics_engine

        self.logger.info(f"Using {self.kinematics_engine} kinematics engine")

        if self.check_collision:
            assert self.kinematics_engine == "Placo", (
                "Collision checking is only available with Placo Kinematics"
            )

        self.gravity_compensation_mode = False  # Flag for gravity compensation mode

        if self.gravity_compensation_mode:
            assert self.kinematics_engine == "Placo", (
                "Gravity compensation is only available with Placo kinematics"
            )

        if self.kinematics_engine == "Placo":
            from reachy_mini.kinematics import PlacoKinematics

            self.head_kinematics: AnyKinematics = PlacoKinematics(
                URDF_ROOT_PATH, check_collision=self.check_collision
            )
        elif self.kinematics_engine == "NN":
            from reachy_mini.kinematics import NNKinematics

            self.head_kinematics = NNKinematics(MODELS_ROOT_PATH)
        elif self.kinematics_engine == "AnalyticalKinematics":
            from reachy_mini.kinematics import AnalyticalKinematics

            self.head_kinematics = AnalyticalKinematics()
        else:
            raise ValueError(
                f"Unknown kinematics engine: {self.kinematics_engine}. Use 'Placo', 'NN' or 'AnalyticalKinematics'."
            )

        self.current_head_pose: Annotated[NDArray[np.float64], (4, 4)] | None = (
            None  # 4x4 pose matrix
        )
        self.target_head_pose: Annotated[NDArray[np.float64], (4, 4)] | None = (
            None  # 4x4 pose matrix
        )
        self.target_body_yaw: float | None = (
            None  # Last body yaw used in IK computations
        )

        self.target_head_joint_positions: (
            Annotated[NDArray[np.float64], (7,)] | None
        ) = None  # [yaw, 0, 1, 2, 3, 4, 5]
        self.current_head_joint_positions: (
            Annotated[NDArray[np.float64], (7,)] | None
        ) = None  # [yaw, 0, 1, 2, 3, 4, 5]
        self.target_antenna_joint_positions: (
            Annotated[NDArray[np.float64], (2,)] | None
        ) = None  # [0, 1]
        self.current_antenna_joint_positions: (
            Annotated[NDArray[np.float64], (2,)] | None
        ) = None  # [0, 1]

        self.error: str | None = None  # To store any error that occurs during execution

        # variables to store the last computed head joint positions and pose
        self._last_target_body_yaw: float | None = (
            None  # Last body yaw used in IK computations
        )
        self._last_target_head_pose: Annotated[NDArray[np.float64], (4, 4)] | None = (
            None  # Last head pose used in IK computations
        )
        self.target_head_joint_current: Annotated[NDArray[np.float64], (7,)] | None = (
            None  # Placeholder for head joint torque
        )
        self.ik_required = False  # Flag to indicate if IK computation is required

        self.is_shutting_down = False

        # Tolerance for kinematics computations
        # For Forward kinematics (around 0.25deg)
        # - FK is calculated at each timestep and is susceptible to noise
        self._fk_kin_tolerance = 1e-3  # rads
        # For Inverse kinematics (around 0.5mm and 0.1 degrees)
        # - IK is calculated only when the head pose is set by the user
        self._ik_kin_tolerance = {
            "rad": 2e-3,  # rads
            "m": 0.5e-3,  # m
        }

        self.audio: Optional[MediaManager] = None
        if self.use_audio:
            self.logger.debug("Initializing daemon audio backend.")
            self.audio = MediaManager(
                backend=MediaBackend.GSTREAMER_NO_VIDEO, log_level=log_level
            )

        # Guard to ensure only one play_move/goto is executed at a time (goto itself uses play_move, so we need an RLock)
        self._play_move_lock = threading.RLock()
        self._active_move_depth = (
            0  # Tracks nested acquisitions within the owning thread
        )

        # Stats tracking
        self._stats_record_period = 1.0  # seconds
        self._stats_timestamps: list[float] = []
        self._stats_error_count = 0
        self._stats_record_t0 = 0.0

        # Timing for control loop
        self._tick_period = 1.0 / self.control_frequency
        self._last_tick_time = 0.0

        # Status object (common for all backends)
        self._status = MotorControllerStatus(
            motor_control_mode=MotorControlMode.Disabled,
            ready=False,
        )

    # Life cycle methods
    def wrapped_run(self) -> None:
        """Run the backend in a try-except block to store errors."""
        try:
            self.run()
        except Exception as e:
            self.error = str(e)
            self.close()
            raise e

    def run(self) -> None:
        """Run the control loop (template method).

        This implements the common control loop structure. Subclasses customize
        behavior by implementing the abstract methods and optionally overriding hooks.
        """
        # Initialize timing
        self._stats_record_t0 = time.time()
        self._last_tick_time = time.time()

        # Initialization hook
        self._on_start()

        while not self.should_stop.is_set():
            # 1. Wait for next tick (handles timing)
            self._wait_for_tick()

            # 2. Read current joint positions from hardware/sim
            head_positions, antenna_positions = self._read_joint_positions()

            # 3. Update kinematics model (FK)
            self.update_head_kinematics_model(
                np.array(head_positions),
                np.array(antenna_positions),
            )

            # 4. Update IK if needed
            self._update_ik_if_needed()

            # 5. Apply targets to hardware/sim
            self._apply_targets()

            # 6. Mark as ready and update status
            self.ready.set()
            self._status.ready = True
            self._status.last_alive = time.time()

            # 7. Track timestamps for stats
            self._stats_timestamps.append(time.time())

            # 8. Collect stats periodically
            if time.time() - self._stats_record_t0 > self._stats_record_period:
                self._collect_control_loop_stats()

            # 9. Per-iteration hook (for backend-specific tasks)
            self._on_update()

        # Cleanup hook
        self._on_stop()

    def _update_ik_if_needed(self) -> None:
        """Update target joint positions from IK if required."""
        if not self.ik_required:
            return
        try:
            self.update_target_head_joints_from_ik(
                self.target_head_pose, self.target_body_yaw
            )
        except ValueError as e:
            self.logger.warning(f"IK error: {e}")

    # Abstract methods - subclasses must implement
    @abstractmethod
    def _read_joint_positions(
        self,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Read current joint positions from hardware/simulation.

        Returns:
            Tuple of (head_joint_positions, antenna_joint_positions) as numpy arrays.

        """
        pass

    @abstractmethod
    def _apply_targets(self) -> None:
        """Apply target positions to hardware/simulation."""
        pass

    # Hook methods - subclasses may override
    def _on_start(self) -> None:
        """Run initialization before the control loop starts.

        Override to perform initialization (e.g., initialize kinematics state).
        """
        pass

    def _on_update(self) -> None:
        """Handle per-iteration tasks after state update.

        Override to perform per-iteration tasks (e.g., stats collection, viewer sync).
        """
        pass

    def _on_stop(self) -> None:
        """Perform cleanup after the control loop ends.

        Override to perform cleanup (e.g., close viewer, join threads).
        """
        pass

    def _wait_for_tick(self) -> None:
        """Wait for the next control loop tick.

        This method handles timing synchronization for the control loop.
        The default implementation sleeps to maintain the target frequency.
        Always sleeps at least 1ms to release the GIL for other threads.

        Override for custom timing behavior:
        - RobotController: blocks until Rust signals next cycle
        - Simulation controllers: use default (Python timing)
        """
        now = time.time()
        elapsed = now - self._last_tick_time
        sleep_time = max(0.001, self._tick_period - elapsed)  # At least 1ms to release GIL
        time.sleep(sleep_time)
        self._last_tick_time = time.time()

    def _collect_control_loop_stats(self) -> None:
        """Collect control loop statistics.

        Override to add backend-specific stats (call super() to keep base stats).
        """
        dt = np.diff(self._stats_timestamps)
        if len(dt) > 1:
            self._status.control_loop_stats["mean_control_loop_frequency"] = float(
                np.mean(1.0 / dt)
            )
            self._status.control_loop_stats["max_control_loop_interval"] = float(
                np.max(dt)
            )
            self._status.control_loop_stats["nb_error"] = self._stats_error_count

        self._stats_timestamps.clear()
        self._stats_error_count = 0
        self._stats_record_t0 = time.time()

    def close(self) -> None:
        """Close the backend and release resources.

        Subclasses should override this method to add their own cleanup logic,
        and call super().close() at the end to ensure audio resources are released.

        Note: This base implementation handles common cleanup (audio).
        Subclasses must still implement their own cleanup for backend-specific resources.
        """
        self.logger.debug("MotorController.close() - cleaning up resources")
        if self.audio is not None:
            self.audio.close()
            self.audio = None

    @property
    def is_move_running(self) -> bool:
        """Return True if a move is currently executing."""
        return self._active_move_depth > 0

    def _try_start_move(self) -> bool:
        """Attempt to acquire the move guard, returning False if another client already owns it."""
        if not self._play_move_lock.acquire(blocking=False):
            return False
        self._active_move_depth += 1
        return True

    def _end_move(self) -> None:
        """Release the move guard; paired with every successful _try_start_move()."""
        if self._active_move_depth > 0:
            self._active_move_depth -= 1
        self._play_move_lock.release()

    def get_status(self) -> MotorControllerStatus:
        """Return backend status.

        Returns the common MotorControllerStatus. Subclasses can override to return
        a MotorControllerStatus subclass with additional fields if needed.
        """
        self._status.error = self.error
        self._status.motor_control_mode = self.get_motor_control_mode()
        return self._status

    # Present/Target joint positions
    def update_target_head_joints_from_ik(
        self,
        pose: Annotated[NDArray[np.float64], (4, 4)] | None = None,
        body_yaw: float | None = None,
    ) -> None:
        """Update the target head joint positions from inverse kinematics.

        Args:
            pose (np.ndarray): 4x4 pose matrix representing the head pose.
            body_yaw (float): The yaw angle of the body, used to adjust the head pose.

        """
        if pose is None:
            pose = (
                self.target_head_pose
                if self.target_head_pose is not None
                else np.eye(4)
            )

        if body_yaw is None:
            body_yaw = self.target_body_yaw if self.target_body_yaw is not None else 0.0

        # Compute the inverse kinematics to get the head joint positions
        joints = self.head_kinematics.ik(pose, body_yaw=body_yaw)
        if joints is None or np.any(np.isnan(joints)):
            raise ValueError("WARNING: Collision detected or head pose not achievable!")

        # update the target head pose and body yaw
        self._last_target_head_pose = pose
        self._last_target_body_yaw = body_yaw

        self.target_head_joint_positions = joints

    def set_target_head_pose(
        self,
        pose: Annotated[NDArray[np.float64], (4, 4)],
    ) -> None:
        """Set the target head pose for the robot.

        Args:
            pose (np.ndarray): 4x4 pose matrix representing the head pose.

        """
        self.target_head_pose = pose
        self.ik_required = True

    def set_target_body_yaw(self, body_yaw: float) -> None:
        """Set the target body yaw for the robot.

        Only used when doing a set_target() with a standalone body_yaw (no head pose).

        Args:
            body_yaw (float): The yaw angle of the body

        """
        self.target_body_yaw = body_yaw
        self.ik_required = True  # Do we need that here?

    def set_target_head_joint_positions(
        self, positions: Annotated[NDArray[np.float64], (7,)] | None
    ) -> None:
        """Set the head joint positions.

        Args:
            positions (List[float]): A list of joint positions for the head.

        """
        self.target_head_joint_positions = positions
        self.ik_required = False

    def set_target(
        self,
        head: Annotated[NDArray[np.float64], (4, 4)] | None = None,  # 4x4 pose matrix
        antennas: Annotated[NDArray[np.float64], (2,)]
        | None = None,  # [right_angle, left_angle] (in rads)
        body_yaw: float | None = None,  # Body yaw angle in radians
    ) -> None:
        """Set the target head pose and/or antenna positions and/or body_yaw."""
        if head is not None:
            self.set_target_head_pose(head)

        if body_yaw is not None:
            self.set_target_body_yaw(body_yaw)

        if antennas is not None:
            self.set_target_antenna_joint_positions(antennas)

    def set_target_antenna_joint_positions(
        self,
        positions: Annotated[NDArray[np.float64], (2,)],
    ) -> None:
        """Set the antenna joint positions.

        Args:
            positions (List[float]): A list of joint positions for the antenna.

        """
        self.target_antenna_joint_positions = positions

    def set_target_head_joint_current(
        self,
        current: Annotated[NDArray[np.float64], (7,)],
    ) -> None:
        """Set the head joint current.

        Args:
            current (Annotated[NDArray[np.float64], (7,)]): A list of current values for the head motors.

        """
        self.target_head_joint_current = current
        self.ik_required = False

    async def play_move(
        self,
        move: Move,
        play_frequency: float = 100.0,
        initial_goto_duration: float = 0.0,
    ) -> None:
        """Asynchronously play a Move.

        Args:
            move (Move): The Move object to be played.
            play_frequency (float): The frequency at which to evaluate the move (in Hz).
            initial_goto_duration (float): Duration for an initial goto to the move's starting position. If 0.0, no initial goto is performed.

        """
        if not self._try_start_move():
            self.logger.warning("Ignoring play_move request: another move is running.")
            return

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

            if move.sound_path is not None and self.audio is not None:
                self.play_sound(str(move.sound_path))

            t0 = time.time()
            while time.time() - t0 < move.duration:
                t = time.time() - t0

                head, antennas, body_yaw = move.evaluate(t)
                if head is not None:
                    self.set_target_head_pose(head)
                if body_yaw is not None:
                    self.set_target_body_yaw(body_yaw)
                if antennas is not None:
                    self.set_target_antenna_joint_positions(antennas)

                elapsed = time.time() - t0 - t
                if elapsed < sleep_period:
                    await asyncio.sleep(sleep_period - elapsed)
                else:
                    await asyncio.sleep(0.001)
        finally:
            if move.sound_path is not None and self.audio is not None:
                # release audio resources after playing the move sound
                self.audio.stop_playing()
            self._end_move()

    async def goto_target(
        self,
        head: Annotated[NDArray[np.float64], (4, 4)] | None = None,  # 4x4 pose matrix
        antennas: Annotated[NDArray[np.float64], (2,)]
        | None = None,  # [right_angle, left_angle] (in rads)
        duration: float = 0.5,  # Duration in seconds for the movement, default is 0.5 seconds.
        method: InterpolationTechnique = InterpolationTechnique.MIN_JERK,  # can be "linear", "minjerk", "ease" or "cartoon", default is "minjerk"
        body_yaw: float | None = 0.0,  # Body yaw angle in radians
    ) -> None:
        """Asynchronously go to a target head pose and/or antennas position using task space interpolation, in "duration" seconds.

        Args:
            head (np.ndarray | None): 4x4 pose matrix representing the target head pose.
            antennas (np.ndarray | list[float] | None): 1D array with two elements representing the angles of the antennas in radians.
            duration (float): Duration of the movement in seconds.
            method (str): Interpolation method to use ("linear", "minjerk", "ease", "cartoon"). Default is "minjerk".
            body_yaw (float | None): Body yaw angle in radians.

        Raises:
            ValueError: If neither head nor antennas are provided, or if duration is not positive.

        """
        return await self.play_move(
            move=GotoMove(
                start_head_pose=self.get_present_head_pose(),
                target_head_pose=head,
                start_body_yaw=self.get_present_body_yaw(),
                target_body_yaw=body_yaw,
                start_antennas=np.array(self.get_present_antenna_joint_positions()),
                target_antennas=np.array(antennas) if antennas is not None else None,
                duration=duration,
                method=method,
            )
        )

    async def goto_joint_positions(
        self,
        head_joint_positions: list[float]
        | None = None,  # [yaw, stewart_platform x 6] length 7
        antennas_joint_positions: list[float]
        | None = None,  # [right_angle, left_angle] length 2
        duration: float = 0.5,  # Duration in seconds for the movement
        method: InterpolationTechnique = InterpolationTechnique.MIN_JERK,  # can be "linear", "minjerk", "ease" or "cartoon", default is "minjerk"
    ) -> None:
        """Asynchronously go to a target head joint positions and/or antennas joint positions using joint space interpolation, in "duration" seconds.

        Go to a target head joint positions and/or antennas joint positions using joint space interpolation, in "duration" seconds.

        Args:
            head_joint_positions (Optional[List[float]]): List of head joint positions in radians (length 7).
            antennas_joint_positions (Optional[List[float]]): List of antennas joint positions in radians (length 2).
            duration (float): Duration of the movement in seconds. Default is 0.5 seconds.
            method (str): Interpolation method to use ("linear", "minjerk", "ease", "cartoon"). Default is "minjerk".

        Raises:
            ValueError: If neither head_joint_positions nor antennas_joint_positions are provided, or if duration is not positive.

        """
        if duration <= 0.0:
            raise ValueError(
                "Duration must be positive and non-zero. Use set_target() for immediate position setting."
            )

        start_head = np.array(self.get_present_head_joint_positions())
        start_antennas = np.array(self.get_present_antenna_joint_positions())

        target_head = (
            np.array(head_joint_positions)
            if head_joint_positions is not None
            else start_head
        )
        target_antennas = (
            np.array(antennas_joint_positions)
            if antennas_joint_positions is not None
            else start_antennas
        )

        t0 = time.time()
        while time.time() - t0 < duration:
            t = time.time() - t0

            interp_time = time_trajectory(t / duration, method=method)

            head_joint = start_head + (target_head - start_head) * interp_time
            antennas_joint = (
                start_antennas + (target_antennas - start_antennas) * interp_time
            )

            self.set_target_head_joint_positions(head_joint)
            self.set_target_antenna_joint_positions(antennas_joint)
            await asyncio.sleep(0.01)

    def get_present_head_joint_positions(self) -> Annotated[NDArray[np.float64], (7,)]:
        """Return the present head joint positions."""
        if self.current_head_joint_positions is None:
            # Fall back to reading directly if not yet set
            head_pos, _ = self._read_joint_positions()
            return np.array(head_pos)
        return self.current_head_joint_positions

    def get_present_body_yaw(self) -> float:
        """Return the present body yaw."""
        yaw: float = self.get_present_head_joint_positions()[0]
        return yaw

    def get_present_head_pose(self) -> Annotated[NDArray[np.float64], (4, 4)]:
        """Return the present head pose as a 4x4 matrix."""
        assert self.current_head_pose is not None, (
            "The current head pose is not set. Please call the update_head_kinematics_model method first."
        )
        return self.current_head_pose

    def get_current_head_pose(self) -> Annotated[NDArray[np.float64], (4, 4)]:
        """Return the present head pose as a 4x4 matrix."""
        return self.get_present_head_pose()

    def get_present_antenna_joint_positions(
        self,
    ) -> Annotated[NDArray[np.float64], (2,)]:
        """Return the present antenna joint positions."""
        if self.current_antenna_joint_positions is None:
            # Fall back to reading directly if not yet set
            _, antenna_pos = self._read_joint_positions()
            return np.array(antenna_pos)
        return self.current_antenna_joint_positions

    # Kinematics methods
    def update_head_kinematics_model(
        self,
        head_joint_positions: Annotated[NDArray[np.float64], (7,)] | None = None,
        antennas_joint_positions: Annotated[NDArray[np.float64], (2,)] | None = None,
    ) -> None:
        """Update the placo kinematics of the robot.

        Args:
            head_joint_positions (List[float] | None): The joint positions of the head.
            antennas_joint_positions (List[float] | None): The joint positions of the antennas.

        Returns:
            None: This method does not return anything.

        This method updates the head kinematics model with the given joint positions.
        - If the joint positions are not provided, it will use the current joint positions.
        - If the head joint positions have not changed, it will return without recomputing the forward kinematics.
        - If the head joint positions have changed, it will compute the forward kinematics to get the current head pose.
        - If the forward kinematics fails, it will raise an assertion error.
        - If the antennas joint positions are provided, it will update the current antenna joint positions.

        Note:
            This method will update the `current_head_pose` and `current_head_joint_positions`
            attributes of the backend instance with the computed values. And the `current_antenna_joint_positions` if provided.

        """
        if head_joint_positions is None:
            head_joint_positions = self.get_present_head_joint_positions()

        # Compute the forward kinematics to get the current head pose
        self.current_head_pose = self.head_kinematics.fk(head_joint_positions)

        # Check if the FK was successful
        assert self.current_head_pose is not None, (
            "FK failed to compute the current head pose."
        )

        # Store the last head joint positions
        self.current_head_joint_positions = head_joint_positions

        if antennas_joint_positions is not None:
            self.current_antenna_joint_positions = antennas_joint_positions

    def set_automatic_body_yaw(self, body_yaw: bool) -> None:
        """Set the automatic body yaw.

        Args:
            body_yaw (bool): The yaw angle of the body.

        """
        self.head_kinematics.set_automatic_body_yaw(automatic_body_yaw=body_yaw)

    def get_urdf(self) -> str:
        """Get the URDF representation of the robot."""
        urdf_path = Path(URDF_ROOT_PATH) / "robot.urdf"

        with open(urdf_path, "r") as f:
            return f.read()

    # Multimedia methods
    def play_sound(self, sound_file: str) -> None:
        """Play a sound file from the assets directory.

        If the file is not found in the assets directory, try to load the path itself.

        Args:
            sound_file (str): The name of the sound file to play (e.g., "wake_up.wav").

        """
        if self.audio:
            self.audio.start_playing()
            self.audio.play_sound(sound_file)

    # Basic move definitions
    INIT_HEAD_POSE = np.eye(4)

    SLEEP_HEAD_JOINT_POSITIONS = [
        0,
        -0.9848156658225817,
        1.2624661884298831,
        -0.24390294527381684,
        0.20555342557667577,
        -1.2363885150358267,
        1.0032234352772091,
    ]

    SLEEP_ANTENNAS_JOINT_POSITIONS = np.array((-3.05, 3.05))
    SLEEP_HEAD_POSE = np.array(
        [
            [0.911, 0.004, 0.413, -0.021],
            [-0.004, 1.0, -0.001, 0.001],
            [-0.413, -0.001, 0.911, -0.044],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )

    async def wake_up(self) -> None:
        """Wake up the robot - go to the initial head position and play the wake up emote and sound."""
        await asyncio.sleep(0.1)

        _, _, magic_distance = distance_between_poses(
            self.get_current_head_pose(), self.INIT_HEAD_POSE
        )

        await self.goto_target(
            self.INIT_HEAD_POSE,
            antennas=np.array((0.0, 0.0)),
            duration=magic_distance * 20 / 1000,  # ms_per_magic_mm = 10
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
        if self.audio:
            self.audio.stop_playing()

    async def goto_sleep(self) -> None:
        """Put the robot to sleep by moving the head and antennas to a predefined sleep position.

        - If we are already very close to the sleep position, we do nothing.
        - If we are far from the sleep position:
            - If we are far from the initial position, we move there first.
            - If we are close to the initial position, we move directly to the sleep position.
        """
        # Magic units
        _, _, dist_to_sleep_pose = distance_between_poses(
            self.get_current_head_pose(), self.SLEEP_HEAD_POSE
        )
        _, _, dist_to_init_pose = distance_between_poses(
            self.get_current_head_pose(), self.INIT_HEAD_POSE
        )
        sleep_time = 2.0

        # Thresholds found empirically.
        if dist_to_sleep_pose > 10:
            if dist_to_init_pose > 30:
                # Move to the initial position
                await self.goto_target(
                    self.INIT_HEAD_POSE, antennas=np.array((0.0, 0.0)), duration=1
                )
                await asyncio.sleep(0.2)

            self.play_sound("go_sleep.wav")

            # Move to the sleep position
            await self.goto_target(
                self.SLEEP_HEAD_POSE,
                antennas=self.SLEEP_ANTENNAS_JOINT_POSITIONS,
                duration=2,
            )
        else:
            # The sound doesn't play fully if we don't wait enough
            self.play_sound("go_sleep.wav")
            sleep_time += 3

        self._last_head_pose = self.SLEEP_HEAD_POSE
        await asyncio.sleep(sleep_time)
        if self.audio:
            self.audio.stop_playing()

    # Motor control modes
    @abstractmethod
    def get_motor_control_mode(self) -> MotorControlMode:
        """Get the motor control mode."""
        pass

    @abstractmethod
    def set_motor_control_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode."""
        pass

    @abstractmethod
    def set_motor_torque_ids(self, ids: list[str], on: bool) -> None:
        """Set the motor torque for specific motor names."""
        pass

    def write_raw_packet(self, packet: bytes) -> bytes:
        """Write a raw packet to the motor controller and return the response.

        Args:
            packet (bytes): The raw packet to send to the motor controller.

        Returns:
            bytes: The raw response packet from the motor controller.

        """
        raise NotImplementedError(
            "The method write_raw_packet is only available for the real robot backend."
        )

    def get_present_passive_joint_positions(self) -> Optional[Dict[str, float]]:
        """Get the present passive joint positions.

        Requires the Placo kinematics engine.
        """
        # This is would be better, and fix mypy issues, but Placo is dynamically imported
        # if not isinstance(self.head_kinematics, PlacoKinematics):
        if self.kinematics_engine != "Placo":
            return None
        return {
            "passive_1_x": self.head_kinematics.get_joint("passive_1_x"),  # type: ignore [union-attr]
            "passive_1_y": self.head_kinematics.get_joint("passive_1_y"),  # type: ignore [union-attr]
            "passive_1_z": self.head_kinematics.get_joint("passive_1_z"),  # type: ignore [union-attr]
            "passive_2_x": self.head_kinematics.get_joint("passive_2_x"),  # type: ignore [union-attr]
            "passive_2_y": self.head_kinematics.get_joint("passive_2_y"),  # type: ignore [union-attr]
            "passive_2_z": self.head_kinematics.get_joint("passive_2_z"),  # type: ignore [union-attr]
            "passive_3_x": self.head_kinematics.get_joint("passive_3_x"),  # type: ignore [union-attr]
            "passive_3_y": self.head_kinematics.get_joint("passive_3_y"),  # type: ignore [union-attr]
            "passive_3_z": self.head_kinematics.get_joint("passive_3_z"),  # type: ignore [union-attr]
            "passive_4_x": self.head_kinematics.get_joint("passive_4_x"),  # type: ignore [union-attr]
            "passive_4_y": self.head_kinematics.get_joint("passive_4_y"),  # type: ignore [union-attr]
            "passive_4_z": self.head_kinematics.get_joint("passive_4_z"),  # type: ignore [union-attr]
            "passive_5_x": self.head_kinematics.get_joint("passive_5_x"),  # type: ignore [union-attr]
            "passive_5_y": self.head_kinematics.get_joint("passive_5_y"),  # type: ignore [union-attr]
            "passive_5_z": self.head_kinematics.get_joint("passive_5_z"),  # type: ignore [union-attr]
            "passive_6_x": self.head_kinematics.get_joint("passive_6_x"),  # type: ignore [union-attr]
            "passive_6_y": self.head_kinematics.get_joint("passive_6_y"),  # type: ignore [union-attr]
            "passive_6_z": self.head_kinematics.get_joint("passive_6_z"),  # type: ignore [union-attr]
            "passive_7_x": self.head_kinematics.get_joint("passive_7_x"),  # type: ignore [union-attr]
            "passive_7_y": self.head_kinematics.get_joint("passive_7_y"),  # type: ignore [union-attr]
            "passive_7_z": self.head_kinematics.get_joint("passive_7_z"),  # type: ignore [union-attr]
        }
