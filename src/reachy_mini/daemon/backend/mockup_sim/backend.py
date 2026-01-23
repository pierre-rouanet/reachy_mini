"""Mockup Simulation Backend for Reachy Mini.

A lightweight simulation backend that doesn't require MuJoCo.
Target positions become current positions immediately (no physics).
The kinematics engine is still used for FK/IK computations.

Apps open the webcam/microphone directly (like with a real robot).
"""

from typing import Annotated

import numpy as np
import numpy.typing as npt

from ..abstract import Backend, BackendStatus, MotorControlMode


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

        self._motor_control_mode = MotorControlMode.Enabled

        # Control loop frequency
        self.control_frequency = 50.0  # Hz

    def _get_control_period(self) -> float:
        """Return the control loop period in seconds."""
        return 1.0 / self.control_frequency

    def _initialize_loop(self) -> None:
        """Initialize kinematics with current positions."""
        self.update_head_kinematics_model(
            self._head_joint_positions,
            self._antenna_joint_positions,
        )

    def _update(self) -> None:
        """Execute one iteration of the mockup simulation control loop.

        In mockup-sim mode, target positions are applied immediately (no physics).
        """
        # Apply target positions immediately (no physics)
        if self.target_head_joint_positions is not None:
            self._head_joint_positions = self.target_head_joint_positions.copy()
        if self.target_antenna_joint_positions is not None:
            self._antenna_joint_positions = self.target_antenna_joint_positions.copy()

        # Update current states
        self.current_head_joint_positions = self._head_joint_positions.copy()
        self.current_antenna_joint_positions = self._antenna_joint_positions.copy()

        # Common update logic (kinematics, IK, publishing)
        self._common_update_logic()

    def get_status(self) -> "BackendStatus":
        """Get the status of the backend."""
        return BackendStatus(
            error=None,
            motor_control_mode=self._motor_control_mode,
            control_loop_stats=self.get_control_loop_stats(),
        )

    def get_current_head_joint_positions(
        self,
    ) -> Annotated[npt.NDArray[np.float64], (7,)]:
        """Get the current joint positions of the head."""
        result: npt.NDArray[np.float64] = self._head_joint_positions.copy()
        return result

    def get_current_antenna_joint_positions(
        self,
    ) -> Annotated[npt.NDArray[np.float64], (2,)]:
        """Get the current joint positions of the antennas."""
        result: npt.NDArray[np.float64] = self._antenna_joint_positions.copy()
        return result

    def get_motor_control_mode(self) -> MotorControlMode:
        """Get the motor control mode."""
        return self._motor_control_mode

    def set_motor_control_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode."""
        self._motor_control_mode = mode

    def set_motor_torque_ids(self, ids: list[str], on: bool) -> None:
        """Set the motor torque state for specific motor names.

        No-op in mockup-sim mode.
        """
        pass
