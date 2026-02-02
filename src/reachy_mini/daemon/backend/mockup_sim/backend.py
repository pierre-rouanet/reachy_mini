"""Mockup Simulation Backend for Reachy Mini.

A lightweight simulation backend that doesn't require MuJoCo.
Target positions become current positions immediately (no physics).
The kinematics engine is still used for FK/IK computations.

Apps open the webcam/microphone directly (like with a real robot).
"""

import numpy as np
import numpy.typing as npt

from ..abstract import Backend, MotorControlMode


class MockupSimBackend(Backend):
    """Lightweight simulated Reachy Mini without MuJoCo.

    This backend provides a simple simulation where target positions
    are applied immediately without physics simulation.

    Apps access webcam/microphone directly (not via UDP streaming).
    """

    def __init__(
        self,
        check_collision: bool = False,
        kinematics_engine: str = "AnalyticalKinematics",
        use_audio: bool = True,
    ) -> None:
        """Initialize the MockupSimBackend.

        Args:
            check_collision: If True, enable collision checking. Default is False.
            kinematics_engine: Kinematics engine to use. Defaults to "AnalyticalKinematics".
            use_audio: If True, use audio. Default is True.

        """
        super().__init__(
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            use_audio=use_audio,
        )

        from reachy_mini.reachy_mini import (
            SLEEP_ANTENNAS_JOINT_POSITIONS,
            SLEEP_HEAD_JOINT_POSITIONS,
        )

        # Initialize with sleep positions
        self._head_joint_positions: npt.NDArray[np.float64] = np.array(
            SLEEP_HEAD_JOINT_POSITIONS, dtype=np.float64
        )
        self._antenna_joint_positions: npt.NDArray[np.float64] = np.array(
            SLEEP_ANTENNAS_JOINT_POSITIONS, dtype=np.float64
        )

        # Set initial motor control mode
        self._status.motor_control_mode = MotorControlMode.Enabled

    # Abstract method implementations

    def _read_joint_positions(
        self,
    ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        """Read current joint positions (returns stored positions)."""
        return self._head_joint_positions.copy(), self._antenna_joint_positions.copy()

    def _apply_targets(self) -> None:
        """Apply target positions immediately (no physics)."""
        if self.target_head_joint_positions is not None:
            self._head_joint_positions = self.target_head_joint_positions.copy()
        if self.target_antenna_joint_positions is not None:
            self._antenna_joint_positions = self.target_antenna_joint_positions.copy()

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
