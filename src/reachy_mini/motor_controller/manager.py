"""Motor manager for Reachy Mini robot.

This module provides the MotorManager class that handles the lifecycle
of motor controllers (MuJoCo simulation, mockup simulation, or real hardware).
"""

import logging
from dataclasses import dataclass
from threading import Thread
from typing import Any, Optional

from reachy_mini.daemon.utils import find_serial_port
from reachy_mini.motor_controller.abstract import (
    MotorController,
    MotorControllerStatus,
)
from reachy_mini.tools.reflash_motors import reflash_motors

from .mockup_sim import MockupController
from .mujoco import MujocoController
from .robot import RobotController


@dataclass
class MotorManagerStatus:
    """Status of the MotorManager."""

    ready: bool
    error: Optional[str]
    motor_controller_status: Optional[MotorControllerStatus]


class MotorManager:
    """Manages motor controller lifecycle.

    Handles creation, startup, shutdown, and monitoring of the motor controller
    (MuJoCo simulation, mockup simulation, or real hardware).
    """

    def __init__(
        self,
        log_level: str = "INFO",
        wireless_version: bool = False,
    ) -> None:
        """Initialize the MotorManager.

        Args:
            log_level: Logging level for the motor manager.
            wireless_version: Whether running on wireless Reachy Mini hardware.

        """
        self.log_level = log_level
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(self.log_level)

        self.wireless_version = wireless_version

        self.motor_controller: MotorController | None = None
        self._motor_controller_thread: Thread | None = None
        self._start_params: dict[str, Any] = {}
        self._error: Optional[str] = None

    @property
    def ready(self) -> bool:
        """Check if the motor controller is ready."""
        return (
            self.motor_controller is not None and self.motor_controller.ready.is_set()
        )

    @property
    def error(self) -> Optional[str]:
        """Get the last error message."""
        if self.motor_controller is not None and self.motor_controller.error:
            return self.motor_controller.error
        return self._error

    async def start(
        self,
        sim: bool = False,
        mockup_sim: bool = False,
        serialport: str = "auto",
        scene: str = "empty",
        check_collision: bool = False,
        kinematics_engine: str = "AnalyticalKinematics",
        headless: bool = False,
        hardware_config_filepath: str | None = None,
        reflash_motors_on_start: bool = True,
    ) -> None:
        """Start the motor controller.

        Args:
            sim: If True, run in simulation mode using MuJoCo.
            mockup_sim: If True, run in lightweight simulation mode (no MuJoCo).
            serialport: Serial port for real motors. "auto" to auto-detect.
            scene: Name of the scene to load in simulation mode.
            check_collision: If True, enable collision checking.
            kinematics_engine: Kinematics engine to use.
            headless: If True, run MuJoCo in headless mode (no GUI).
            hardware_config_filepath: Path to the hardware configuration YAML file.
            reflash_motors_on_start: If True, reflash motors on startup.

        Raises:
            RuntimeError: If the motor controller fails to start.

        """
        if self.motor_controller is not None:
            self.logger.warning("Motor controller already running, stop it first.")
            return

        self._start_params = {
            "sim": sim,
            "mockup_sim": mockup_sim,
            "serialport": serialport,
            "scene": scene,
            "headless": headless,
            "check_collision": check_collision,
            "kinematics_engine": kinematics_engine,
            "hardware_config_filepath": hardware_config_filepath,
        }

        self.logger.info(
            f"Starting motor controller: sim={sim}, mockup_sim={mockup_sim}, "
            f"serialport={serialport}, scene={scene}"
        )

        # Create the motor controller
        self._error = None
        self.motor_controller = self._create_motor_controller(
            sim=sim,
            mockup_sim=mockup_sim,
            serialport=serialport,
            scene=scene,
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            headless=headless,
            hardware_config_filepath=hardware_config_filepath,
            reflash_motors_on_start=reflash_motors_on_start,
        )

        # Start the motor controller thread
        def motor_controller_wrapped_run() -> None:
            assert self.motor_controller is not None
            try:
                self.motor_controller.wrapped_run()
            except Exception as e:
                self.logger.error(f"Motor controller encountered an error: {e}")
                self._error = str(e)
                self.motor_controller = None

        self._motor_controller_thread = Thread(target=motor_controller_wrapped_run)
        self._motor_controller_thread.start()

        # Wait for motor controller to be ready
        if not self.motor_controller.ready.wait(timeout=2.0):
            self._error = (
                self.motor_controller.error
                or "Motor controller not ready after 2 seconds"
            )
            self.logger.error(self._error)
            raise RuntimeError(self._error)

        self.logger.info("Motor controller started successfully.")

    async def stop(self) -> None:
        """Stop the motor controller.

        Note:
            Call Daemon's motion_manager.goto_sleep() before this if you want
            the robot to go to sleep with sound.

        """
        if self.motor_controller is None:
            self.logger.info("Motor controller not running.")
            return

        self.logger.info("Stopping motor controller...")
        self.motor_controller.is_shutting_down = True

        # Signal motor controller to stop and wait for thread
        self.motor_controller.should_stop.set()
        if self._motor_controller_thread is not None:
            self._motor_controller_thread.join(timeout=5.0)
            if self._motor_controller_thread.is_alive():
                self.logger.warning("Motor controller did not stop in time.")

        # Clean up
        self.motor_controller.close()
        self.motor_controller.ready.clear()
        self.motor_controller = None
        self._motor_controller_thread = None

        self.logger.info("Motor controller stopped.")

    def status(self) -> MotorManagerStatus:
        """Get the current status of the motor manager."""
        motor_controller_status = None
        if self.motor_controller is not None:
            motor_controller_status = self.motor_controller.get_status()

        return MotorManagerStatus(
            ready=self.ready,
            error=self.error,
            motor_controller_status=motor_controller_status,
        )

    def _create_motor_controller(
        self,
        sim: bool,
        mockup_sim: bool,
        serialport: str,
        scene: str,
        check_collision: bool,
        kinematics_engine: str,
        headless: bool,
        hardware_config_filepath: str | None = None,
        reflash_motors_on_start: bool = True,
    ) -> MotorController:
        """Create the appropriate motor controller instance.

        Args:
            sim: If True, create MuJoCo simulation controller.
            mockup_sim: If True, create mockup simulation controller.
            serialport: Serial port for real motors.
            scene: Scene to load in simulation.
            check_collision: Enable collision checking.
            kinematics_engine: Kinematics engine to use.
            headless: Run MuJoCo without GUI.
            hardware_config_filepath: Path to hardware config.
            reflash_motors_on_start: Reflash motors on startup.

        Returns:
            The created motor controller instance.

        Raises:
            RuntimeError: If serial port auto-detection fails.

        """
        if mockup_sim:
            return MockupController(
                check_collision=check_collision,
                kinematics_engine=kinematics_engine,
            )
        elif sim:
            return MujocoController(
                scene=scene,
                check_collision=check_collision,
                kinematics_engine=kinematics_engine,
                headless=headless,
            )
        else:
            # Real robot motor controller
            if serialport == "auto":
                ports = find_serial_port(wireless_version=self.wireless_version)

                if len(ports) == 0:
                    raise RuntimeError(
                        "No Reachy Mini serial port found. "
                        "Check USB connection and permissions. "
                        "Or directly specify the serial port using --serialport."
                    )
                elif len(ports) > 1:
                    raise RuntimeError(
                        f"Multiple Reachy Mini serial ports found {ports}. "
                        "Please specify the serial port using --serialport."
                    )

                serialport = ports[0]
                self.logger.info(f"Found Reachy Mini serial port: {serialport}")

            self.logger.info(
                f"Creating RobotController: serialport={serialport}, "
                f"check_collision={check_collision}, kinematics_engine={kinematics_engine}"
            )

            if reflash_motors_on_start:
                reflash_motors(serialport, dont_light_up=True)

            return RobotController(
                serialport=serialport,
                log_level=self.log_level,
                check_collision=check_collision,
                kinematics_engine=kinematics_engine,
                wireless_version=self.wireless_version,
                hardware_config_filepath=hardware_config_filepath,
            )
