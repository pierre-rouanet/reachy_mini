"""Base class for motor controllers, simulated or real.

This module defines the `MotorController` class, which serves as a base for implementing
different types of motor controllers, whether they are simulated (like Mujoco) or real
(connected via serial port). The class provides methods for managing joint positions,
torque control, and other controller-specific functionalities.
It is designed to be extended by subclasses that implement the specific behavior for
each type of controller.
"""

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

if typing.TYPE_CHECKING:
    from reachy_mini.kinematics import AnyKinematics
from reachy_mini.utils.constants import MODELS_ROOT_PATH, URDF_ROOT_PATH


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
    automatic_body_yaw: bool = True


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
        wireless_version: bool = False,
    ) -> None:
        """Initialize the backend."""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

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
        # Target/current body yaw (separate from stewart platform joints)
        self.target_body_yaw: float | None = None
        self.current_body_yaw: float = 0.0

        # Target/current stewart platform joint positions (6 joints)
        self.target_stewart_positions: Annotated[NDArray[np.float64], (6,)] | None = None
        self.current_stewart_positions: Annotated[NDArray[np.float64], (6,)] | None = None

        # Target/current antenna joint positions (2 joints)
        self.target_antenna_joint_positions: Annotated[NDArray[np.float64], (2,)] | None = None
        self.current_antenna_joint_positions: Annotated[NDArray[np.float64], (2,)] | None = None

        self.error: str | None = None  # To store any error that occurs during execution

        # variables to store the last computed head joint positions and pose
        self._last_target_body_yaw: float | None = (
            None  # Last body yaw used in IK computations
        )
        self._last_target_head_pose: Annotated[NDArray[np.float64], (4, 4)] | None = (
            None  # Last head pose used in IK computations
        )
        self.target_stewart_current: Annotated[NDArray[np.float64], (6,)] | None = None
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

        # Flag to signal that a trajectory move (goto/play_move) is in progress.
        # Used by set_target handlers to avoid fighting with running trajectories.
        self._move_running = False

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
            body_yaw, stewart_positions, antenna_positions = self._read_joint_positions()

            # 3. Update kinematics model (FK)
            self.update_head_kinematics_model(body_yaw, np.array(stewart_positions))
            self.current_antenna_joint_positions = np.array(antenna_positions)

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
    ) -> tuple[float, NDArray[np.float64], NDArray[np.float64]]:
        """Read current joint positions from hardware/simulation.

        Returns:
            Tuple of (body_yaw, stewart_positions, antenna_positions).

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
        sleep_time = max(
            0.001, self._tick_period - elapsed
        )  # At least 1ms to release GIL
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

    @property
    def is_move_running(self) -> bool:
        """Return True if a trajectory move is currently executing."""
        return self._move_running

    def _start_move(self) -> None:
        """Mark that a trajectory move has started."""
        self._move_running = True

    def _end_move(self) -> None:
        """Mark that a trajectory move has ended."""
        self._move_running = False

    def get_status(self) -> MotorControllerStatus:
        """Return backend status.

        Returns the common MotorControllerStatus. Subclasses can override to return
        a MotorControllerStatus subclass with additional fields if needed.
        """
        self._status.error = self.error
        self._status.motor_control_mode = self.get_motor_control_mode()
        self._status.automatic_body_yaw = self.head_kinematics.automatic_body_yaw
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

        self.target_body_yaw, self.target_stewart_positions = unpack_joints(joints)

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

    def set_target_stewart_positions(
        self, positions: Annotated[NDArray[np.float64], (6,)]
    ) -> None:
        """Set the stewart platform joint positions (6 joints).

        Args:
            positions: Array of 6 stewart platform joint positions in radians.

        """
        self.target_stewart_positions = positions
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

    def set_target_stewart_current(
        self,
        current: Annotated[NDArray[np.float64], (6,)],
    ) -> None:
        """Set the stewart platform joint current (for gravity compensation).

        Args:
            current: Array of 6 current values for the stewart platform motors.

        """
        self.target_stewart_current = current
        self.ik_required = False

    def get_present_stewart_positions(self) -> Annotated[NDArray[np.float64], (6,)]:
        """Return the present stewart platform joint positions (6 joints)."""
        assert self.current_stewart_positions is not None, (
            "Stewart positions not set. Is the control loop running?"
        )
        return self.current_stewart_positions

    def get_present_body_yaw(self) -> float:
        """Return the present body yaw."""
        return self.current_body_yaw

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
        assert self.current_antenna_joint_positions is not None, (
            "Antenna positions not set. Is the control loop running?"
        )
        return self.current_antenna_joint_positions

    # Kinematics methods
    def update_head_kinematics_model(
        self,
        body_yaw: float | None = None,
        stewart_positions: Annotated[NDArray[np.float64], (6,)] | None = None,
    ) -> None:
        """Update the head kinematics model (forward kinematics).

        Computes FK from the given joints and updates current_head_pose,
        current_body_yaw, and current_stewart_positions.

        If not provided, falls back to the stored current values.

        Args:
            body_yaw: Body yaw angle in radians.
            stewart_positions: 6 stewart platform joint positions in radians.

        """
        if body_yaw is None:
            body_yaw = self.current_body_yaw
        if stewart_positions is None:
            stewart_positions = self.get_present_stewart_positions()

        # Pack for FK (kinematics expects 7-elem array)
        joints_7 = pack_joints(body_yaw, stewart_positions)

        # Compute the forward kinematics to get the current head pose
        self.current_head_pose = self.head_kinematics.fk(joints_7)

        # Check if the FK was successful
        assert self.current_head_pose is not None, (
            "FK failed to compute the current head pose."
        )

        # Store the current joint positions
        self.current_body_yaw = body_yaw
        self.current_stewart_positions = stewart_positions

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

    # Basic move definitions
    INIT_HEAD_POSE = np.eye(4)

    SLEEP_BODY_YAW: float = 0.0
    SLEEP_STEWART_POSITIONS = [
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


def pack_joints(body_yaw: float, stewart: NDArray[np.float64]) -> NDArray[np.float64]:
    """Pack body_yaw + 6 stewart joints into 7-elem array for FK/IK."""
    return np.concatenate([[body_yaw], stewart])


def unpack_joints(joints_7: NDArray[np.float64]) -> tuple[float, NDArray[np.float64]]:
    """Unpack 7-elem FK/IK array into (body_yaw, stewart_6)."""
    return float(joints_7[0]), joints_7[1:]
