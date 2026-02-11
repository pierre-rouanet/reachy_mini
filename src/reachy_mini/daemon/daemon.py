"""Daemon for Reachy Mini robot.

This module provides the main Daemon class that orchestrates all components:
- MotorManager: Motor control lifecycle (simulation or real hardware)
- ApiManager: FastAPI HTTP server for REST API and WebSocket endpoints
- WebRTCManager: Real-time streaming (video, audio, motor data)
- AppManager: User application lifecycle
- MotionManager: Motion with synchronized audio (wake_up, goto_sleep, play_move)

The Daemon provides a simple high-level API: start(), stop(), run_forever(), status().
"""

import asyncio
import logging
from dataclasses import asdict
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Optional

from reachy_mini.apps.manager import AppManager
from reachy_mini.daemon.api_manager import ApiManager, is_port_available
from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.models import DaemonStatus
from reachy_mini.daemon.utils import get_ip_address
from reachy_mini.daemon.webrtc_manager import WebRTCManager
from reachy_mini.media.media_manager import MediaBackend, MediaManager
from reachy_mini.motion.manager import MotionManager
from reachy_mini.motor_controller.abstract import MotorControlMode
from reachy_mini.motor_controller.manager import MotorManager

if TYPE_CHECKING:
    from reachy_mini.motor_controller.abstract import MotorController


class DaemonState(Enum):
    """Enum representing the state of the Reachy Mini daemon."""

    NOT_INITIALIZED = "not_initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class Daemon:
    """Main daemon orchestrator for Reachy Mini robot.

    Orchestrates:
    - MotorManager: Robot motor control
    - ApiManager: FastAPI HTTP server
    - WebRTCManager: Real-time streaming (video, audio, motor data)
    - AppManager: User applications

    Can be used as an async context manager:
        async with Daemon(DaemonArgs(sim=True, headless=True)) as daemon:
            # daemon is running
        # automatically stopped

    Or manually:
        daemon = Daemon()
        await daemon.start(DaemonArgs(sim=True))
        # ...
        await daemon.stop()
    """

    def __init__(self, config: DaemonArgs | None = None) -> None:
        """Initialize the Reachy Mini daemon.

        Args:
            config: Configuration for the daemon. If None, uses defaults.

        """
        self._config = config if config is not None else DaemonArgs()

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(self._config.log_level.value)

        # Get package version
        try:
            package_version = version("reachy_mini")
            self.logger.info(f"Daemon version: {package_version}")
        except PackageNotFoundError:
            package_version = None
            self.logger.warning("Could not determine daemon version")

        # Initialize status tracking
        self._state = DaemonState.NOT_INITIALIZED
        self._error: Optional[str] = None
        self._wlan_ip: Optional[str] = None
        self._version: Optional[str] = package_version
        self._simulation_enabled: Optional[bool] = None
        self._mockup_sim_enabled: Optional[bool] = None

        # Create managers
        self._motor_manager = MotorManager(
            log_level=self._config.log_level.value,
            wireless_version=self._config.wireless_version,
        )
        self._audio_manager: Optional[MediaManager] = None
        self._motion_manager = MotionManager(
            log_level=self._config.log_level.value,
        )
        self._app_manager = AppManager(
            wireless_version=self._config.wireless_version,
            desktop_app_daemon=self._config.desktop_app_daemon,
            daemon=self,
        )
        # ApiManager created after app_manager since it needs access to daemon
        self._api_manager = ApiManager(
            daemon=self,
            log_level=self._config.log_level.value,
            wireless_version=self._config.wireless_version,
        )
        self._webrtc_manager = WebRTCManager(
            log_level=self._config.log_level.value,
            enabled=self._config.wireless_version,
        )

    def __del__(self) -> None:
        """Destructor to ensure proper cleanup."""
        self.logger.debug("Cleaning up Daemon resources...")

    async def __aenter__(self) -> "Daemon":
        """Enter context manager: start the daemon with stored config."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit context manager: stop the daemon."""
        await self.stop()

    @property
    def motor_controller(self) -> Optional["MotorController"]:
        """Convenience access to the current motor controller."""
        return self._motor_manager.motor_controller

    @property
    def app_manager(self) -> AppManager:
        """Get the AppManager instance."""
        return self._app_manager

    @property
    def config(self) -> DaemonArgs:
        """Get the current configuration."""
        return self._config

    @property
    def motor_manager(self) -> MotorManager:
        """Get the MotorManager instance."""
        return self._motor_manager

    @property
    def motion_manager(self) -> MotionManager:
        """Get the MotionManager instance."""
        return self._motion_manager

    @property
    def audio(self) -> Optional[MediaManager]:
        """Get the MediaManager instance for audio."""
        return self._audio_manager

    async def start_components(self) -> bool:
        """Start all components except the API server.

        Starts audio, motor controller, motion manager, and WebRTC.

        Returns:
            True if motor controller started successfully.

        """
        # Handle localhost_only default based on wireless_version
        localhost_only = self._config.localhost_only
        if localhost_only is None:
            localhost_only = not self._config.wireless_version

        # Update status
        self._simulation_enabled = self._config.sim
        self._mockup_sim_enabled = self._config.mockup_sim
        if not localhost_only:
            self._wlan_ip = get_ip_address()

        self._state = DaemonState.STARTING
        self._error = None

        # 1. Start the audio manager (if audio enabled)
        if self._config.use_audio:
            try:
                self.logger.info("Initializing daemon audio backend.")
                self._audio_manager = MediaManager(
                    backend=MediaBackend.GSTREAMER_NO_VIDEO,
                    log_level=self._config.log_level.value,
                )
            except Exception as e:
                self.logger.warning(f"Failed to initialize audio: {e}")
                # Audio failure is not critical, continue without it

        # 2. Start the motor controller
        motor_started = False
        try:
            await self._motor_manager.start(
                sim=self._config.sim,
                mockup_sim=self._config.mockup_sim,
                serialport=self._config.serialport,
                scene=self._config.scene,
                check_collision=self._config.check_collision,
                kinematics_engine=self._config.kinematics_engine.value,
                headless=self._config.headless,
                hardware_config_filepath=self._config.hardware_config_filepath,
            )
            motor_started = True
        except Exception as e:
            self.logger.error(f"Error while starting motor controller: {e}")
            self._state = DaemonState.ERROR
            self._error = str(e)

        # 3. Wire up motion manager with motor controller and audio
        self._motion_manager.set_motor_controller(self._motor_manager.motor_controller)
        self._motion_manager.set_audio(self._audio_manager)

        # 4. Wake up if requested (only if motor controller started successfully)
        if motor_started and self._config.wake_up_on_start:
            assert self.motor_controller is not None  # Guaranteed by motor_started
            try:
                self.motor_controller.set_motor_control_mode(MotorControlMode.Enabled)
                await self._motion_manager.wake_up()
            except Exception as e:
                self.logger.error(f"Error while waking up Reachy Mini: {e}")
                self._state = DaemonState.ERROR
                self._error = str(e)
            except KeyboardInterrupt:
                self.logger.warning("Wake up interrupted by user.")
                self._state = DaemonState.STOPPING

        # 5. Start WebRTC interface (if enabled and motor controller started)
        if motor_started:
            self._webrtc_manager.set_daemon(self)
            await self._webrtc_manager.start()

        if motor_started and self._state != DaemonState.ERROR:
            self._state = DaemonState.RUNNING

        return motor_started

    async def start(self) -> DaemonState:
        """Start the Reachy Mini daemon (components + API server).

        Returns:
            DaemonState: The current state after attempting to start.

        """
        if self._state == DaemonState.RUNNING:
            self.logger.warning("Daemon is already running.")
            return self._state

        self.logger.info(
            f"Daemon start parameters: sim={self._config.sim}, "
            f"mockup_sim={self._config.mockup_sim}, "
            f"serialport={self._config.serialport}, scene={self._config.scene}, "
            f"wake_up_on_start={self._config.wake_up_on_start}, "
            f"check_collision={self._config.check_collision}, "
            f"kinematics_engine={self._config.kinematics_engine}, "
            f"headless={self._config.headless}, "
            f"hardware_config_filepath={self._config.hardware_config_filepath}"
        )

        # Check port availability early (before starting motor controller)
        if not is_port_available(self._config.fastapi_host, self._config.fastapi_port):
            raise RuntimeError(
                f"Port {self._config.fastapi_port} is already in use on {self._config.fastapi_host}. "
                "Another daemon or process may be running on this port."
            )

        self.logger.info("Starting Reachy Mini daemon...")
        motor_started = await self.start_components()

        # Start FastAPI server (always start so status can be queried)
        await self._api_manager.start(self._config)

        if motor_started and self._state != DaemonState.ERROR:
            self.logger.info("Daemon started successfully.")
        else:
            self.logger.warning("Daemon started with errors (motor controller failed).")

        return self._state

    async def stop_components(self, goto_sleep_on_stop: bool | None = None) -> None:
        """Stop all components except the API server.

        Args:
            goto_sleep_on_stop: If True, put the robot to sleep before stopping.
                If None, uses the value from config.

        """
        # Use config value if not specified
        if goto_sleep_on_stop is None:
            goto_sleep_on_stop = self._config.goto_sleep_on_stop

        if self._state in (DaemonState.STOPPING, DaemonState.ERROR):
            goto_sleep_on_stop = False

        self.logger.info("Stopping components...")
        self._state = DaemonState.STOPPING

        # 1. Pause WebRTC (keep signaling server running for restart)
        self._webrtc_manager.pause()

        # 2. Go to sleep if requested (uses motion manager for sound)
        if goto_sleep_on_stop and self._motor_manager.ready:
            assert (
                self.motor_controller is not None
            )  # Guaranteed by _motor_manager.ready
            try:
                self.logger.info("Putting robot to sleep...")
                self.motor_controller.set_motor_control_mode(
                    MotorControlMode.Enabled
                )
                await self._motion_manager.goto_sleep()
                self.motor_controller.set_motor_control_mode(
                    MotorControlMode.Disabled
                )
            except Exception as e:
                self.logger.error(f"Error while putting robot to sleep: {e}")
            except KeyboardInterrupt:
                self.logger.warning("Sleep interrupted by user.")

        # 3. Stop the motor controller (if running)
        if self._motor_manager.ready:
            await self._motor_manager.stop()

        # 4. Stop audio manager
        if self._audio_manager is not None:
            self._audio_manager.close()
            self._audio_manager = None

        self._state = DaemonState.STOPPED
        self.logger.info("Components stopped.")

    async def stop(self, goto_sleep_on_stop: bool | None = None) -> DaemonState:
        """Stop the Reachy Mini daemon (components + API server).

        Args:
            goto_sleep_on_stop: If True, put the robot to sleep before stopping.
                If None, uses the value from config.

        Returns:
            DaemonState: The current state after attempting to stop.

        """
        if self._state == DaemonState.STOPPED:
            self.logger.warning("Daemon is already stopped.")
            return self._state

        try:
            await self.stop_components(goto_sleep_on_stop)
            await self._api_manager.stop()
            self.logger.info("Daemon stopped successfully.")
        except Exception as e:
            self.logger.error(f"Error while stopping the daemon: {e}")
            self._state = DaemonState.ERROR
            self._error = str(e)
        except KeyboardInterrupt:
            self.logger.warning("Daemon already stopping...")

        return self._state

    async def restart(self, config: DaemonArgs | None = None) -> DaemonState:
        """Restart the Reachy Mini daemon components (API stays up).

        Args:
            config: Optional new configuration. If None, reuses current config.

        Returns:
            DaemonState: The current state after attempting to restart.

        """
        if self._state == DaemonState.STOPPED:
            self.logger.warning("Daemon is not running.")
            return self._state

        if self._state in (DaemonState.RUNNING, DaemonState.ERROR):
            self.logger.info("Restarting Reachy Mini daemon...")

            await self.stop_components(goto_sleep_on_stop=False)

            if config is not None:
                self._config = config

            await self.start_components()
            return self._state

        raise NotImplementedError(
            "Restarting is only supported when daemon is in RUNNING or ERROR state."
        )

    def status(self) -> DaemonStatus:
        """Get the current status of the Reachy Mini daemon.

        Returns:
            DaemonStatus: The current daemon status.

        """
        motor_status = self._motor_manager.status()

        if motor_status.error:
            self._state = DaemonState.ERROR
            self._error = motor_status.error

        # Convert MotorControllerStatus dataclass to dict if present
        motor_controller_dict = None
        if motor_status.motor_controller_status is not None:
            motor_controller_dict = asdict(motor_status.motor_controller_status)

        return DaemonStatus(
            robot_name=self._config.robot_name,
            state=self._state.value,
            wireless_version=self._config.wireless_version,
            desktop_app_daemon=self._config.desktop_app_daemon,
            simulation_enabled=self._simulation_enabled,
            mockup_sim_enabled=self._mockup_sim_enabled,
            motor_controller_status=motor_controller_dict,
            error=self._error,
            wlan_ip=self._wlan_ip,
            version=self._version,
        )

    async def run_forever(self) -> None:
        """Run the Reachy Mini daemon indefinitely.

        Starts the daemon components and runs the API server until shutdown.
        Uvicorn handles SIGINT/SIGTERM internally for graceful shutdown.

        Respects config options:
        - preload_datasets: Pre-download recorded move datasets at startup
        - dataset_update_interval_hours: Interval for background dataset updates
        """
        from reachy_mini.motion.recorded_move import preload_default_datasets

        dataset_updater_task: asyncio.Task[None] | None = None

        # Pre-download recorded move datasets in background
        if self._config.preload_datasets:

            def preload_with_logging() -> None:
                try:
                    preload_default_datasets()
                    self.logger.info("Recorded move datasets pre-loaded successfully")
                except Exception as e:
                    self.logger.warning(f"Failed to pre-load some datasets: {e}")

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, preload_with_logging)

        # Start periodic dataset updater if enabled
        if self._config.dataset_update_interval_hours > 0:

            async def dataset_updater(interval_hours: float) -> None:
                interval_seconds = interval_hours * 3600
                while True:
                    try:
                        await asyncio.sleep(interval_seconds)
                        self.logger.info("Checking for dataset updates...")
                        preload_default_datasets()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        self.logger.warning(f"Error in dataset updater: {e}")

            dataset_updater_task = asyncio.create_task(
                dataset_updater(self._config.dataset_update_interval_hours)
            )
            self.logger.info(
                f"Dataset updater started (interval: {self._config.dataset_update_interval_hours}h)"
            )

        await self.start_components()

        if self._state not in (DaemonState.RUNNING, DaemonState.ERROR):
            await self.stop_components()
            return

        try:
            self.logger.info("Daemon is running. Press Ctrl+C to stop.")
            await self._api_manager.serve(self._config)
        finally:
            # Cancel dataset updater task
            if dataset_updater_task is not None:
                dataset_updater_task.cancel()
                try:
                    await dataset_updater_task
                except asyncio.CancelledError:
                    pass

            await self.stop_components()
