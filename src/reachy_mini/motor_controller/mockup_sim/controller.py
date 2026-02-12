"""Mockup Simulation Motor Controller for Reachy Mini.

A lightweight simulation controller that doesn't require MuJoCo.
Target positions become current positions immediately (no physics).
The kinematics engine is still used for FK/IK computations.

Apps open the webcam/microphone directly (like with a real robot).
"""

import numpy as np
import numpy.typing as npt

from ..abstract import MotorController, MotorControlMode


class MockupController(MotorController):
    """Lightweight simulated Reachy Mini without MuJoCo.

    This controller provides a simple simulation where target positions
    are applied immediately without physics simulation.

    Apps access webcam/microphone directly (not via UDP streaming).
    """

    def __init__(
        self,
        check_collision: bool = False,
        kinematics_engine: str = "AnalyticalKinematics",
    ) -> None:
        """Initialize the MockupController.

        Args:
            check_collision: If True, enable collision checking. Default is False.
            kinematics_engine: Kinematics engine to use. Defaults to "AnalyticalKinematics".

        """
        super().__init__(
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
        )

        self._body_yaw: float = self.SLEEP_BODY_YAW
        self._stewart_positions: npt.NDArray[np.float64] = np.array(
            self.SLEEP_STEWART_POSITIONS, dtype=np.float64
        )
        self._antenna_positions: npt.NDArray[np.float64] = np.array(
            self.SLEEP_ANTENNAS_JOINT_POSITIONS, dtype=np.float64
        )

        # Set initial motor control mode
        self._status.motor_control_mode = MotorControlMode.Enabled

    # Abstract method implementations

    def _read_joint_positions(
        self,
    ) -> tuple[float, npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        """Read current joint positions (returns stored positions)."""
        return self._body_yaw, self._stewart_positions.copy(), self._antenna_positions.copy()

    def _apply_targets(self) -> None:
        """Apply target positions immediately (no physics)."""
        if self.target_body_yaw is not None:
            self._body_yaw = self.target_body_yaw
        if self.target_stewart_positions is not None:
            self._stewart_positions = self.target_stewart_positions.copy()
        if self.target_antenna_joint_positions is not None:
            self._antenna_positions = self.target_antenna_joint_positions.copy()

    def get_motor_control_mode(self) -> MotorControlMode:
        """Get the motor control mode."""
        return self._status.motor_control_mode

    def set_motor_control_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode."""
        self._status.motor_control_mode = mode

    def set_motor_torque_ids(self, ids: list[str], on: bool) -> None:
        """Set the motor torque state for specific motor names.

        No-op in mockup-sim mode.
        """
        pass
