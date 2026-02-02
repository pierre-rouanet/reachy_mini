"""Robot Backend for Reachy Mini.

This module provides the `RobotBackend` class, which interfaces with the Reachy Mini motor controller
to control the robot's movements and manage its status.
"""

import logging
import struct
import time
from datetime import timedelta
from typing import Any

import numpy as np
import numpy.typing as npt
from reachy_mini_motor_controller import ReachyMiniPyControlLoop

from reachy_mini.utils.hardware_config.parser import parse_yaml_config

from ..abstract import Backend, MotorControlMode


class RobotBackend(Backend):
    """Real robot backend for Reachy Mini."""

    control_frequency: float = 50.0

    def __init__(
        self,
        serialport: str,
        log_level: str = "INFO",
        check_collision: bool = False,
        kinematics_engine: str = "AnalyticalKinematics",
        hardware_error_check_frequency: float = 1.0,
        use_audio: bool = True,
        wireless_version: bool = False,
        hardware_config_filepath: str | None = None,
    ):
        """Initialize the RobotBackend.

        Args:
            serialport (str): The serial port to which the Reachy Mini is connected.
            log_level (str): The logging level for the backend. Default is "INFO".
            check_collision (bool): If True, enable collision checking. Default is False.
            kinematics_engine (str): Kinematics engine to use. Defaults to "AnalyticalKinematics".
            hardware_error_check_frequency (float): Frequency in seconds to check for hardware errors. Default is 1.0.
            use_audio (bool): If True, use audio. Default is True.
            wireless_version (bool): If True, indicates that the wireless version of Reachy Mini is used. Default is False.
            hardware_config_filepath (str | None): Path to the hardware configuration YAML file. Default is None.

        """
        super().__init__(
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            use_audio=use_audio,
            wireless_version=wireless_version,
        )

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        self.c: ReachyMiniPyControlLoop | None = ReachyMiniPyControlLoop(
            serialport,
            read_position_loop_period=timedelta(
                seconds=1.0 / self.control_frequency
            ),
            allowed_retries=5,
            stats_pub_period=timedelta(seconds=1.0),
        )

        self.name2id = self.c.get_motor_name_id()
        if hardware_config_filepath is not None:
            config = parse_yaml_config(hardware_config_filepath)
            for motor_name, motor_conf in config.motors.items():
                if motor_conf.pid is not None:
                    motor_id = self.name2id[motor_name]
                    p, i, d = motor_conf.pid
                    self.logger.info(
                        f"Setting PID gains for motor '{motor_name}' (ID: {motor_id}): P={p}, I={i}, D={d}"
                    )
                    self.c.async_write_pid_gains(motor_id, p, i, d)

        self.motor_control_mode = self._infer_control_mode()
        self._torque_enabled = self.motor_control_mode != MotorControlMode.Disabled
        self.logger.info(f"Motor control mode: {self.motor_control_mode}")

        # Update status with inferred motor control mode
        self._status.motor_control_mode = self.motor_control_mode

        # Operation modes
        self._current_head_operation_mode = -1
        self._current_antennas_operation_mode = -1
        self.target_antenna_joint_current = None
        self.target_head_joint_current = None

        # Hardware error checking
        if hardware_error_check_frequency <= 0:
            raise ValueError(
                "hardware_error_check_frequency must be positive and non-zero (Hz)."
            )
        self.hardware_error_check_period = 1.0 / hardware_error_check_frequency
        self._last_hardware_error_check_time = 0.0

        # Initialize IMU for wireless version
        self.bmi088: Any = None
        if wireless_version:
            try:
                from bmi088 import BMI088

                self.bmi088 = BMI088(i2c_bus=4)
                self.logger.info("BMI088 IMU initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize IMU: {e}")

    # Template method hooks

    def _on_start(self) -> None:
        """Initialize kinematics state and hardware error check timing."""
        self._last_hardware_error_check_time = time.time()

        # Compute forward kinematics for initial head pose (important for wake_up)
        head_positions, _ = self._read_joint_positions()
        self.current_head_pose = self.head_kinematics.fk(
            np.array(head_positions),
            no_iterations=20,
        )
        assert self.current_head_pose is not None
        self.head_kinematics.ik(self.current_head_pose, no_iterations=20)

    def _on_update(self) -> None:
        """Check for hardware errors periodically."""
        if (
            time.time() - self._last_hardware_error_check_time
            > self.hardware_error_check_period
        ):
            self._check_hardware_errors()

    def _collect_control_loop_stats(self) -> None:
        """Collect control loop statistics including motor controller stats."""
        # Call base class to compute common stats
        super()._collect_control_loop_stats()

        # Add motor controller specific stats
        if self.c is not None:
            self._status.control_loop_stats["motor_controller"] = str(self.c.get_stats())

    def _check_hardware_errors(self) -> None:
        """Check for hardware errors and log them."""
        hardware_errors = self.read_hardware_errors()
        if hardware_errors:
            for motor_name, errors in hardware_errors.items():
                self.logger.error(f"Motor '{motor_name}' hardware errors: {errors}")
        self._last_hardware_error_check_time = time.time()

    # Abstract method implementations

    def _read_joint_positions(
        self,
    ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        """Read joint positions from motor controller."""
        assert self.c is not None, "Motor controller not initialized or already closed."
        positions = self.c.get_last_position()

        yaw = positions.body_yaw
        antennas = positions.antennas
        dofs = positions.stewart

        head_pos = np.array([yaw] + list(dofs))
        antenna_pos = np.array(list(antennas))
        return head_pos, antenna_pos

    def _apply_targets(self) -> None:
        """Apply target positions to motor controller."""
        assert self.c is not None, "Motor controller not initialized or already closed."

        if not self._torque_enabled:
            return

        if self._current_head_operation_mode != 0:  # Position control mode
            if self.target_head_joint_positions is not None:
                self.c.set_stewart_platform_position(
                    self.target_head_joint_positions[1:].tolist()
                )
                self.c.set_body_rotation(self.target_head_joint_positions[0])
        else:  # Torque control mode
            if self.gravity_compensation_mode:
                self.compensate_head_gravity()
            if self.target_head_joint_current is not None:
                self.c.set_stewart_platform_goal_current(
                    np.round(self.target_head_joint_current[1:], 0)
                    .astype(int)
                    .tolist()
                )

        if self._current_antennas_operation_mode != 0:  # Position control mode
            if self.target_antenna_joint_positions is not None:
                self.c.set_antennas_positions(
                    self.target_antenna_joint_positions.tolist()
                )

    def close(self) -> None:
        """Close the motor controller connection and release resources."""
        if self.c is not None:
            self.c.close()
        self.c = None
        super().close()

    # Motor control methods

    def enable_motors(self) -> None:
        """Enable the motors by turning the torque on."""
        assert self.c is not None, "Motor controller not initialized or already closed."
        self.c.enable_torque()
        self._torque_enabled = True

    def disable_motors(self) -> None:
        """Disable the motors by turning the torque off."""
        assert self.c is not None, "Motor controller not initialized or already closed."
        self.c.disable_torque()
        self._torque_enabled = False

    def set_head_operation_mode(self, mode: int) -> None:
        """Change the operation mode of the head motors.

        Args:
            mode (int): 0=torque control, 3=position control, 5=current-based position control.

        """
        assert self.c is not None, "Motor controller not initialized or already closed."
        assert mode in [0, 3, 5], f"Invalid operation mode: {mode}"

        if self._torque_enabled:
            self.c.enable_stewart_platform(False)

        self.c.set_stewart_platform_operating_mode(mode)

        if mode != 0:
            motor_pos = self.c.get_last_position()
            self.target_head_joint_positions = np.array(
                [motor_pos.body_yaw] + motor_pos.stewart
            )
            self.c.set_stewart_platform_position(
                self.target_head_joint_positions[1:].tolist()
            )
            self.c.set_body_rotation(self.target_head_joint_positions[0])
            self.c.enable_body_rotation(True)
            self.c.set_body_rotation_operating_mode(0)
        else:
            self.c.enable_body_rotation(False)

        if self._torque_enabled:
            self.c.enable_stewart_platform(True)

        self._current_head_operation_mode = mode

    def set_antennas_operation_mode(self, mode: int) -> None:
        """Change the operation mode of the antennas motors.

        Args:
            mode (int): 0=torque control, 3=position control, 5=current-based position control.

        """
        assert self.c is not None, "Motor controller not initialized or already closed."
        assert mode in [0, 3, 5], f"Invalid operation mode: {mode}"

        if self._current_antennas_operation_mode != mode:
            if mode != 0:
                self.target_antenna_joint_positions = np.array(
                    self.c.get_last_position().antennas
                )
                self.c.set_antennas_positions(
                    self.target_antenna_joint_positions.tolist()
                )
                self.c.enable_antennas(True)
            else:
                self.c.enable_antennas(False)

            self._current_antennas_operation_mode = mode

    def get_motor_control_mode(self) -> MotorControlMode:
        """Get the motor control mode."""
        return self.motor_control_mode

    def set_motor_control_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode."""
        if mode == self.motor_control_mode:
            return

        if mode == MotorControlMode.Enabled:
            if self.motor_control_mode == MotorControlMode.GravityCompensation:
                self.disable_motors()
                self.set_head_operation_mode(3)
                self.set_antennas_operation_mode(3)
            self.gravity_compensation_mode = False
            self.enable_motors()

        elif mode == MotorControlMode.Disabled:
            self.gravity_compensation_mode = False
            self.disable_motors()

        elif mode == MotorControlMode.GravityCompensation:
            if self.kinematics_engine != "Placo":
                raise RuntimeError(
                    "Gravity compensation mode is only supported with the Placo kinematics engine."
                )
            self.disable_motors()
            self.set_head_operation_mode(0)
            self.set_antennas_operation_mode(0)
            self.gravity_compensation_mode = True
            self.enable_motors()

        self.motor_control_mode = mode

    def set_motor_torque_ids(self, ids: list[str], on: bool) -> None:
        """Set the torque state for specific motor names."""
        assert self.c is not None, "Motor controller not initialized or already closed."
        assert ids is not None and len(ids) > 0, "IDs list cannot be empty or None."

        ids_int = [self.name2id[name] for name in ids]
        if on:
            self.c.enable_torque_on_ids(ids_int)
        else:
            self.c.disable_torque_on_ids(ids_int)

    # IMU and hardware methods

    def get_imu_data(self) -> dict[str, list[float] | float] | None:
        """Get current IMU data (accelerometer, gyroscope, quaternion, temperature)."""
        if self.bmi088 is None:
            return None

        try:
            accel_x, accel_y, accel_z = self.bmi088.read_accelerometer(m_per_s2=True)
            gyro_x, gyro_y, gyro_z = self.bmi088.read_gyroscope(deg_per_s=False)
            dt = 1.0 / self.control_frequency
            quat = self.bmi088.get_quat(dt)
            temperature = self.bmi088.read_temperature()

            return {
                "accelerometer": [float(accel_x), float(accel_y), float(accel_z)],
                "gyroscope": [float(gyro_x), float(gyro_y), float(gyro_z)],
                "quaternion": [float(q) for q in quat],
                "temperature": float(temperature),
            }
        except Exception as e:
            self.logger.error(f"Error reading IMU data: {e}")
            return None

    def compensate_head_gravity(self) -> None:
        """Calculate the currents necessary to compensate for gravity."""
        assert self.kinematics_engine == "Placo", (
            "Gravity compensation is only supported with the Placo kinematics engine."
        )

        from_Nm_to_mA = 1.47 / 0.52 * 1000
        correction_factor = 4.0

        head_joints = self.get_present_head_joint_positions()
        gravity_torque = self.head_kinematics.compute_gravity_torque(  # type: ignore [union-attr]
            np.array(head_joints)
        )
        current = gravity_torque * from_Nm_to_mA / correction_factor
        self.set_target_head_joint_current(current)

    def _infer_control_mode(self) -> MotorControlMode:
        """Infer the current motor control mode from hardware state."""
        assert self.c is not None, "Motor controller not initialized or already closed."

        torque = self.c.is_torque_enabled()
        if not torque:
            return MotorControlMode.Disabled

        mode = self.c.get_stewart_platform_operating_mode()
        if mode == 3:
            return MotorControlMode.Enabled
        elif mode == 1:
            return MotorControlMode.GravityCompensation
        else:
            raise ValueError(f"Unknown motor control mode: {mode}")

    def read_hardware_errors(self) -> dict[str, list[str]]:
        """Read hardware errors from the motor controller."""
        if self.c is None:
            return {}

        def decode_hardware_error_byte(err_byte: int) -> list[str]:
            bits_to_error = {
                0: "Input Voltage Error",
                2: "Overheating Error",
                4: "Electrical Shock Error",
                5: "Overload Error",
            }
            err_bits = [i for i in range(8) if (err_byte & (1 << i)) != 0]
            return [bits_to_error[b] for b in err_bits if b in bits_to_error]

        def voltage_ok(id: int, allowed_max_voltage: float = 7.8) -> bool:
            assert self.c is not None
            resp_bytes = self.c.async_read_raw_bytes(id, 144, 2)
            resp = struct.unpack("h", bytes(resp_bytes))[0]
            voltage: float = resp / 10.0
            return voltage <= allowed_max_voltage

        errors = {}
        for name, id in self.c.get_motor_name_id().items():
            err_byte = self.c.async_read_raw_bytes(id, 70, 1)
            assert len(err_byte) == 1
            err = decode_hardware_error_byte(err_byte[0])
            if err:
                if "Input Voltage Error" in err:
                    if voltage_ok(id):
                        err.remove("Input Voltage Error")
                if len(err) > 0:
                    errors[name] = err

        return errors

    def write_raw_packet(self, packet: bytes) -> bytes:
        """Write a raw packet to the motor controller and return the response."""
        assert self.c is not None, "Motor controller not initialized or already closed."
        result: bytes = bytes(self.c.write_raw_packet(packet))
        return result
