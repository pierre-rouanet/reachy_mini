"""Backend manager for Reachy Mini robot.

This module provides the BackendManager class that handles the lifecycle
of robot backends (MuJoCo simulation, mockup simulation, or real hardware).
"""

import logging
from dataclasses import dataclass
from threading import Thread
from typing import Any, Optional

from reachy_mini.daemon.backend.abstract import Backend, BackendStatus, MotorControlMode
from reachy_mini.daemon.utils import find_serial_port
from reachy_mini.tools.reflash_motors import reflash_motors

from .backend.mockup_sim import MockupSimBackend
from .backend.mujoco import MujocoBackend
from .backend.robot import RobotBackend


@dataclass
class BackendManagerStatus:
    """Status of the BackendManager."""

    ready: bool
    error: Optional[str]
    backend_status: Optional[BackendStatus]


class BackendManager:
    """Manages robot backend lifecycle.

    Handles creation, startup, shutdown, and monitoring of the robot backend
    (MuJoCo simulation, mockup simulation, or real hardware).
    """

    def __init__(
        self,
        log_level: str = "INFO",
        wireless_version: bool = False,
    ) -> None:
        """Initialize the BackendManager.

        Args:
            log_level: Logging level for the backend manager.
            wireless_version: Whether running on wireless Reachy Mini hardware.

        """
        self.log_level = log_level
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(self.log_level)

        self.wireless_version = wireless_version

        self.backend: Backend | None = None
        self._backend_thread: Thread | None = None
        self._start_params: dict[str, Any] = {}
        self._error: Optional[str] = None

    @property
    def ready(self) -> bool:
        """Check if the backend is ready."""
        return self.backend is not None and self.backend.ready.is_set()

    @property
    def error(self) -> Optional[str]:
        """Get the last error message."""
        if self.backend is not None and self.backend.error:
            return self.backend.error
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
        use_audio: bool = True,
        hardware_config_filepath: str | None = None,
        reflash_motors_on_start: bool = True,
    ) -> None:
        """Start the backend.

        Args:
            sim: If True, run in simulation mode using MuJoCo.
            mockup_sim: If True, run in lightweight simulation mode (no MuJoCo).
            serialport: Serial port for real motors. "auto" to auto-detect.
            scene: Name of the scene to load in simulation mode.
            check_collision: If True, enable collision checking.
            kinematics_engine: Kinematics engine to use.
            headless: If True, run MuJoCo in headless mode (no GUI).
            use_audio: If True, enable audio.
            hardware_config_filepath: Path to the hardware configuration YAML file.
            reflash_motors_on_start: If True, reflash motors on startup.

        Raises:
            RuntimeError: If the backend fails to start.

        """
        if self.backend is not None:
            self.logger.warning("Backend already running, stop it first.")
            return

        self._start_params = {
            "sim": sim,
            "mockup_sim": mockup_sim,
            "serialport": serialport,
            "scene": scene,
            "headless": headless,
            "use_audio": use_audio,
            "check_collision": check_collision,
            "kinematics_engine": kinematics_engine,
            "hardware_config_filepath": hardware_config_filepath,
        }

        self.logger.info(
            f"Starting backend: sim={sim}, mockup_sim={mockup_sim}, "
            f"serialport={serialport}, scene={scene}"
        )

        # Create the backend
        self.backend = self._create_backend(
            sim=sim,
            mockup_sim=mockup_sim,
            serialport=serialport,
            scene=scene,
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            headless=headless,
            use_audio=use_audio,
            hardware_config_filepath=hardware_config_filepath,
            reflash_motors_on_start=reflash_motors_on_start,
        )

        # Start the backend thread
        def backend_wrapped_run() -> None:
            assert self.backend is not None
            try:
                self.backend.wrapped_run()
            except Exception as e:
                self.logger.error(f"Backend encountered an error: {e}")
                self._error = str(e)
                self.backend = None

        self._backend_thread = Thread(target=backend_wrapped_run)
        self._backend_thread.start()

        # Wait for backend to be ready
        if not self.backend.ready.wait(timeout=2.0):
            self._error = self.backend.error or "Backend not ready after 2 seconds"
            self.logger.error(self._error)
            raise RuntimeError(self._error)

        self.logger.info("Backend started successfully.")

    async def stop(self, goto_sleep: bool = True) -> None:
        """Stop the backend.

        Args:
            goto_sleep: If True, put the robot to sleep before stopping.

        """
        if self.backend is None:
            self.logger.info("Backend not running.")
            return

        self.logger.info("Stopping backend...")
        self.backend.is_shutting_down = True

        if goto_sleep:
            try:
                self.logger.info("Putting robot to sleep...")
                self.backend.set_motor_control_mode(MotorControlMode.Enabled)
                await self.backend.goto_sleep()
                self.backend.set_motor_control_mode(MotorControlMode.Disabled)
            except Exception as e:
                self.logger.error(f"Error while putting robot to sleep: {e}")
            except KeyboardInterrupt:
                self.logger.warning("Sleep interrupted by user.")

        # Signal backend to stop and wait for thread
        self.backend.should_stop.set()
        if self._backend_thread is not None:
            self._backend_thread.join(timeout=5.0)
            if self._backend_thread.is_alive():
                self.logger.warning("Backend did not stop in time.")

        # Clean up
        self.backend.close()
        self.backend.ready.clear()
        self.backend = None
        self._backend_thread = None

        self.logger.info("Backend stopped.")

    async def wake_up(self) -> None:
        """Wake up the robot (enable motors and move to initial position)."""
        if self.backend is None:
            raise RuntimeError("Backend not running")

        self.logger.info("Waking up robot...")
        self.backend.set_motor_control_mode(MotorControlMode.Enabled)
        await self.backend.wake_up()

    async def goto_sleep(self) -> None:
        """Put the robot to sleep (move to sleep position and disable motors)."""
        if self.backend is None:
            raise RuntimeError("Backend not running")

        self.logger.info("Putting robot to sleep...")
        self.backend.set_motor_control_mode(MotorControlMode.Enabled)
        await self.backend.goto_sleep()
        self.backend.set_motor_control_mode(MotorControlMode.Disabled)

    def status(self) -> BackendManagerStatus:
        """Get the current status of the backend manager."""
        backend_status = None
        if self.backend is not None:
            backend_status = self.backend.get_status()

        return BackendManagerStatus(
            ready=self.ready,
            error=self.error,
            backend_status=backend_status,
        )

    def _create_backend(
        self,
        sim: bool,
        mockup_sim: bool,
        serialport: str,
        scene: str,
        check_collision: bool,
        kinematics_engine: str,
        headless: bool,
        use_audio: bool,
        hardware_config_filepath: str | None = None,
        reflash_motors_on_start: bool = True,
    ) -> Backend:
        """Create the appropriate backend instance.

        Args:
            sim: If True, create MuJoCo simulation backend.
            mockup_sim: If True, create mockup simulation backend.
            serialport: Serial port for real motors.
            scene: Scene to load in simulation.
            check_collision: Enable collision checking.
            kinematics_engine: Kinematics engine to use.
            headless: Run MuJoCo without GUI.
            use_audio: Enable audio.
            hardware_config_filepath: Path to hardware config.
            reflash_motors_on_start: Reflash motors on startup.

        Returns:
            The created backend instance.

        Raises:
            RuntimeError: If serial port auto-detection fails.

        """
        if mockup_sim:
            return MockupSimBackend(
                check_collision=check_collision,
                kinematics_engine=kinematics_engine,
                use_audio=use_audio,
            )
        elif sim:
            return MujocoBackend(
                scene=scene,
                check_collision=check_collision,
                kinematics_engine=kinematics_engine,
                headless=headless,
                use_audio=use_audio,
            )
        else:
            # Real robot backend
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
                f"Creating RobotBackend: serialport={serialport}, "
                f"check_collision={check_collision}, kinematics_engine={kinematics_engine}"
            )

            if reflash_motors_on_start:
                reflash_motors(serialport, dont_light_up=True)

            return RobotBackend(
                serialport=serialport,
                log_level=self.log_level,
                check_collision=check_collision,
                kinematics_engine=kinematics_engine,
                use_audio=use_audio,
                wireless_version=self.wireless_version,
                hardware_config_filepath=hardware_config_filepath,
            )
