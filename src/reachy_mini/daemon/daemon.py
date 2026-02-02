"""Daemon for Reachy Mini robot.

This module provides the main Daemon class that orchestrates all components:
- BackendManager: Robot control lifecycle (simulation or real hardware)
- InterfaceManager: Communication interfaces (FastAPI, WebRTC)
- AppManager: User application lifecycle

The Daemon provides a simple high-level API: start(), stop(), run4ever(), status().
"""

import asyncio
import logging
import signal
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Optional

from reachy_mini.apps.manager import AppManager
from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.backend.abstract import BackendStatus
from reachy_mini.daemon.backend_manager import BackendManager
from reachy_mini.daemon.interface_manager import InterfaceManager
from reachy_mini.daemon.utils import get_ip_address

if TYPE_CHECKING:
    from reachy_mini.daemon.backend.abstract import Backend


class DaemonState(Enum):
    """Enum representing the state of the Reachy Mini daemon."""

    NOT_INITIALIZED = "not_initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class DaemonStatus:
    """Dataclass representing the status of the Reachy Mini daemon."""

    robot_name: str
    state: DaemonState
    wireless_version: bool
    desktop_app_daemon: bool
    simulation_enabled: Optional[bool]
    mockup_sim_enabled: Optional[bool]
    backend_status: Optional[BackendStatus]
    error: Optional[str] = None
    wlan_ip: Optional[str] = None
    version: Optional[str] = None


class Daemon:
    """Main daemon orchestrator for Reachy Mini robot.

    Orchestrates BackendManager (robot control), InterfaceManager (HTTP/WebRTC),
    and AppManager (user apps) to provide a unified daemon interface.

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

        # Initialize status
        self._status = DaemonStatus(
            robot_name=self._config.robot_name,
            state=DaemonState.NOT_INITIALIZED,
            wireless_version=self._config.wireless_version,
            desktop_app_daemon=self._config.desktop_app_daemon,
            simulation_enabled=None,
            mockup_sim_enabled=None,
            backend_status=None,
            error=None,
            wlan_ip=None,
            version=package_version,
        )

        # Create managers
        self._backend_manager = BackendManager(
            log_level=self._config.log_level.value,
            wireless_version=self._config.wireless_version,
        )
        self._app_manager = AppManager(
            wireless_version=self._config.wireless_version,
            desktop_app_daemon=self._config.desktop_app_daemon,
            daemon=self,
        )
        # InterfaceManager created after app_manager since it needs access to daemon
        self._interface_manager = InterfaceManager(
            daemon=self,
            log_level=self._config.log_level.value,
            wireless_version=self._config.wireless_version,
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
    def backend(self) -> Optional["Backend"]:
        """Convenience access to the current backend."""
        return self._backend_manager.backend

    @property
    def app_manager(self) -> AppManager:
        """Get the AppManager instance."""
        return self._app_manager

    @property
    def config(self) -> DaemonArgs:
        """Get the current configuration."""
        return self._config

    async def start(self) -> DaemonState:
        """Start the Reachy Mini daemon.

        Returns:
            DaemonState: The current state after attempting to start.

        """
        if self._status.state == DaemonState.RUNNING:
            self.logger.warning("Daemon is already running.")
            return self._status.state

        # Handle localhost_only default based on wireless_version
        localhost_only = self._config.localhost_only
        if localhost_only is None:
            localhost_only = not self._config.wireless_version

        self.logger.info(
            f"Daemon start parameters: sim={self._config.sim}, "
            f"mockup_sim={self._config.mockup_sim}, "
            f"serialport={self._config.serialport}, scene={self._config.scene}, "
            f"localhost_only={localhost_only}, "
            f"wake_up_on_start={self._config.wake_up_on_start}, "
            f"check_collision={self._config.check_collision}, "
            f"kinematics_engine={self._config.kinematics_engine}, "
            f"headless={self._config.headless}, "
            f"hardware_config_filepath={self._config.hardware_config_filepath}"
        )

        # Update status
        self._status.simulation_enabled = self._config.sim
        self._status.mockup_sim_enabled = self._config.mockup_sim
        if not localhost_only:
            self._status.wlan_ip = get_ip_address()

        self.logger.info("Starting Reachy Mini daemon...")
        self._status.state = DaemonState.STARTING

        # 1. Start the backend
        backend_started = False
        try:
            await self._backend_manager.start(
                sim=self._config.sim,
                mockup_sim=self._config.mockup_sim,
                serialport=self._config.serialport,
                scene=self._config.scene,
                check_collision=self._config.check_collision,
                kinematics_engine=self._config.kinematics_engine.value,
                headless=self._config.headless,
                use_audio=self._config.use_audio,
                hardware_config_filepath=self._config.hardware_config_filepath,
            )
            backend_started = True
        except Exception as e:
            self.logger.error(f"Error while starting backend: {e}")
            self._status.state = DaemonState.ERROR
            self._status.error = str(e)
            # Continue to start FastAPI so status can be queried

        # 2. Wake up if requested (only if backend started successfully)
        if backend_started and self._config.wake_up_on_start:
            try:
                await self._backend_manager.wake_up()
            except Exception as e:
                self.logger.error(f"Error while waking up Reachy Mini: {e}")
                self._status.state = DaemonState.ERROR
                self._status.error = str(e)
            except KeyboardInterrupt:
                self.logger.warning("Wake up interrupted by user.")
                self._status.state = DaemonState.STOPPING

        # 3. Start WebRTC interface (if enabled and backend started)
        if backend_started:
            await self._interface_manager.start_webrtc()

        # 4. Start FastAPI server (always start so status can be queried)
        await self._interface_manager.start_server(self._config)

        if backend_started and self._status.state != DaemonState.ERROR:
            self.logger.info("Daemon started successfully.")
            self._status.state = DaemonState.RUNNING
        else:
            self.logger.warning("Daemon started with errors (backend failed).")

        return self._status.state

    async def stop(self, goto_sleep_on_stop: bool | None = None) -> DaemonState:
        """Stop the Reachy Mini daemon.

        Args:
            goto_sleep_on_stop: If True, put the robot to sleep before stopping.
                If None, uses the value from config.

        Returns:
            DaemonState: The current state after attempting to stop.

        """
        if self._status.state == DaemonState.STOPPED:
            self.logger.warning("Daemon is already stopped.")
            return self._status.state

        # Use config value if not specified
        if goto_sleep_on_stop is None:
            goto_sleep_on_stop = self._config.goto_sleep_on_stop

        try:
            if self._status.state in (DaemonState.STOPPING, DaemonState.ERROR):
                goto_sleep_on_stop = False

            self.logger.info("Stopping Reachy Mini daemon...")
            self._status.state = DaemonState.STOPPING

            # 1. Pause WebRTC (keep signaling server running for restart)
            self._interface_manager.pause_webrtc()

            # 2. Stop the backend (if running)
            if self._backend_manager.ready:
                await self._backend_manager.stop(goto_sleep=goto_sleep_on_stop)

            # 3. Stop FastAPI server
            await self._interface_manager.stop_server()

            self.logger.info("Daemon stopped successfully.")
            self._status.state = DaemonState.STOPPED

        except Exception as e:
            self.logger.error(f"Error while stopping the daemon: {e}")
            self._status.state = DaemonState.ERROR
            self._status.error = str(e)
        except KeyboardInterrupt:
            self.logger.warning("Daemon already stopping...")

        return self._status.state

    async def restart(self, config: DaemonArgs | None = None) -> DaemonState:
        """Restart the Reachy Mini daemon.

        Args:
            config: Optional new configuration. If None, reuses current config.

        Returns:
            DaemonState: The current state after attempting to restart.

        """
        if self._status.state == DaemonState.STOPPED:
            self.logger.warning("Daemon is not running.")
            return self._status.state

        if self._status.state in (DaemonState.RUNNING, DaemonState.ERROR):
            self.logger.info("Restarting Reachy Mini daemon...")

            # Use goto_sleep=False during restart to avoid unnecessary movement
            await self.stop(goto_sleep_on_stop=False)

            if config is not None:
                self._config = config

            return await self.start()

        raise NotImplementedError(
            "Restarting is only supported when daemon is in RUNNING or ERROR state."
        )

    def status(self) -> DaemonStatus:
        """Get the current status of the Reachy Mini daemon.

        Returns:
            DaemonStatus: The current daemon status.

        """
        backend_status = self._backend_manager.status()
        self._status.backend_status = backend_status.backend_status

        if backend_status.error:
            self._status.state = DaemonState.ERROR
            self._status.error = backend_status.error

        return self._status

    async def run4ever(self) -> None:
        """Run the Reachy Mini daemon indefinitely.

        Starts the daemon (backend + FastAPI server) and blocks until shutdown.
        This is the main entry point when running from main.py.

        Respects config options:
        - autostart: If False, only starts FastAPI server (backend via API)
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

        await self.start()

        if self._status.state in (DaemonState.RUNNING, DaemonState.ERROR):
            # Set up shutdown event for signal handling
            shutdown_event = asyncio.Event()

            def signal_handler() -> None:
                self.logger.warning("Received shutdown signal.")
                # Signal server to stop immediately
                if self._interface_manager._uvicorn_server is not None:
                    self._interface_manager._uvicorn_server.should_exit = True
                shutdown_event.set()

            # Register signal handlers
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, signal_handler)

            try:
                self.logger.info("Daemon is running. Press Ctrl+C to stop.")
                # Wait for shutdown signal or server thread to stop
                while (
                    not shutdown_event.is_set()
                    and self._interface_manager._server_thread is not None
                    and self._interface_manager._server_thread.is_alive()
                ):
                    await asyncio.sleep(0.1)
            except Exception as e:
                self.logger.error(f"An error occurred: {e}")
                self._status.state = DaemonState.ERROR
                self._status.error = str(e)
            finally:
                # Remove signal handlers
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)

                # Cancel dataset updater task
                if dataset_updater_task is not None:
                    dataset_updater_task.cancel()
                    try:
                        await dataset_updater_task
                    except asyncio.CancelledError:
                        pass

        await self.stop()
