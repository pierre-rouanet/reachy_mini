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
from reachy_mini.daemon.app.args import DaemonArgs, KinematicsEngine
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
    """

    def __init__(
        self,
        log_level: str = "INFO",
        robot_name: str = "reachy_mini",
        wireless_version: bool = False,
        desktop_app_daemon: bool = False,
    ) -> None:
        """Initialize the Reachy Mini daemon.

        Args:
            log_level: Logging level for all components.
            robot_name: Name of the robot (for topic namespacing).
            wireless_version: Whether running on wireless Reachy Mini hardware.
            desktop_app_daemon: Whether running as desktop app daemon.

        """
        self.log_level = log_level
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(self.log_level)

        self.robot_name = robot_name
        self.wireless_version = wireless_version
        self.desktop_app_daemon = desktop_app_daemon

        # Get package version
        try:
            package_version = version("reachy_mini")
            self.logger.info(f"Daemon version: {package_version}")
        except PackageNotFoundError:
            package_version = None
            self.logger.warning("Could not determine daemon version")

        # Initialize status
        self._status = DaemonStatus(
            robot_name=robot_name,
            state=DaemonState.NOT_INITIALIZED,
            wireless_version=wireless_version,
            desktop_app_daemon=desktop_app_daemon,
            simulation_enabled=None,
            mockup_sim_enabled=None,
            backend_status=None,
            error=None,
            wlan_ip=None,
            version=package_version,
        )

        # Create managers
        self._backend_manager = BackendManager(
            log_level=log_level,
            wireless_version=wireless_version,
        )
        self._app_manager = AppManager(
            wireless_version=wireless_version,
            desktop_app_daemon=desktop_app_daemon,
            daemon=self,
        )
        # InterfaceManager created after app_manager since it needs access to daemon
        self._interface_manager = InterfaceManager(
            daemon=self,
            log_level=log_level,
            wireless_version=wireless_version,
        )

        # Store start params for restart
        self._start_params: dict[str, Any] = {}

    def __del__(self) -> None:
        """Destructor to ensure proper cleanup."""
        self.logger.debug("Cleaning up Daemon resources...")

    @property
    def backend(self) -> Optional["Backend"]:
        """Convenience access to the current backend."""
        return self._backend_manager.backend

    @property
    def app_manager(self) -> AppManager:
        """Get the AppManager instance."""
        return self._app_manager

    async def start(
        self,
        sim: bool = False,
        mockup_sim: bool = False,
        serialport: str = "auto",
        scene: str = "empty",
        localhost_only: bool = True,
        wake_up_on_start: bool = True,
        check_collision: bool = False,
        kinematics_engine: KinematicsEngine = KinematicsEngine.ANALYTICAL,
        headless: bool = False,
        use_audio: bool = True,
        hardware_config_filepath: str | None = None,
        fastapi_host: str = "127.0.0.1",
        fastapi_port: int = 8000,
    ) -> DaemonState:
        """Start the Reachy Mini daemon.

        Args:
            sim: If True, run in simulation mode using MuJoCo.
            mockup_sim: If True, run in lightweight simulation mode (no MuJoCo).
            serialport: Serial port for real motors. "auto" to auto-detect.
            scene: Name of the scene to load in simulation mode.
            localhost_only: If True, restrict server to localhost only.
            wake_up_on_start: If True, wake up the robot on start.
            check_collision: If True, enable collision checking.
            kinematics_engine: Kinematics engine to use.
            headless: If True, run MuJoCo in headless mode (no GUI).
            use_audio: If True, enable audio.
            hardware_config_filepath: Path to hardware configuration YAML.
            fastapi_host: Host address for FastAPI server.
            fastapi_port: Port for FastAPI server.

        Returns:
            DaemonState: The current state after attempting to start.

        """
        if self._status.state == DaemonState.RUNNING:
            self.logger.warning("Daemon is already running.")
            return self._status.state

        self.logger.info(
            f"Daemon start parameters: sim={sim}, mockup_sim={mockup_sim}, "
            f"serialport={serialport}, scene={scene}, localhost_only={localhost_only}, "
            f"wake_up_on_start={wake_up_on_start}, check_collision={check_collision}, "
            f"kinematics_engine={kinematics_engine}, headless={headless}, "
            f"hardware_config_filepath={hardware_config_filepath}"
        )

        # Update status
        self._status.simulation_enabled = sim
        self._status.mockup_sim_enabled = mockup_sim
        if not localhost_only:
            self._status.wlan_ip = get_ip_address()

        # Store params for restart
        self._start_params = {
            "sim": sim,
            "mockup_sim": mockup_sim,
            "serialport": serialport,
            "scene": scene,
            "headless": headless,
            "use_audio": use_audio,
            "localhost_only": localhost_only,
            "check_collision": check_collision,
            "kinematics_engine": kinematics_engine,
            "hardware_config_filepath": hardware_config_filepath,
            "fastapi_host": fastapi_host,
            "fastapi_port": fastapi_port,
        }

        self.logger.info("Starting Reachy Mini daemon...")
        self._status.state = DaemonState.STARTING

        # 1. Start the backend
        try:
            await self._backend_manager.start(
                sim=sim,
                mockup_sim=mockup_sim,
                serialport=serialport,
                scene=scene,
                check_collision=check_collision,
                kinematics_engine=kinematics_engine.value,
                headless=headless,
                use_audio=use_audio,
                hardware_config_filepath=hardware_config_filepath,
            )
        except Exception as e:
            self.logger.error(f"Error while starting backend: {e}")
            self._status.state = DaemonState.ERROR
            self._status.error = str(e)
            return self._status.state

        # 2. Wake up if requested
        if wake_up_on_start:
            try:
                await self._backend_manager.wake_up()
            except Exception as e:
                self.logger.error(f"Error while waking up Reachy Mini: {e}")
                self._status.state = DaemonState.ERROR
                self._status.error = str(e)
                return self._status.state
            except KeyboardInterrupt:
                self.logger.warning("Wake up interrupted by user.")
                self._status.state = DaemonState.STOPPING
                return self._status.state

        # 3. Start WebRTC interface (if enabled)
        await self._interface_manager.start_webrtc()

        # 4. Start FastAPI server
        server_args = DaemonArgs(
            fastapi_host=fastapi_host,
            fastapi_port=fastapi_port,
            # Fill in other fields from start params
            sim=sim,
            mockup_sim=mockup_sim,
            serialport=serialport,
            scene=scene,
            headless=headless,
            use_audio=use_audio,
            check_collision=check_collision,
            hardware_config_filepath=hardware_config_filepath,
        )
        await self._interface_manager.start_server(server_args)

        self.logger.info("Daemon started successfully.")
        self._status.state = DaemonState.RUNNING
        return self._status.state

    async def stop(self, goto_sleep_on_stop: bool = True) -> DaemonState:
        """Stop the Reachy Mini daemon.

        Args:
            goto_sleep_on_stop: If True, put the robot to sleep before stopping.

        Returns:
            DaemonState: The current state after attempting to stop.

        """
        if self._status.state == DaemonState.STOPPED:
            self.logger.warning("Daemon is already stopped.")
            return self._status.state

        if not self._backend_manager.ready:
            self.logger.info("Daemon backend is not initialized.")
            self._status.state = DaemonState.STOPPED
            return self._status.state

        try:
            if self._status.state in (DaemonState.STOPPING, DaemonState.ERROR):
                goto_sleep_on_stop = False

            self.logger.info("Stopping Reachy Mini daemon...")
            self._status.state = DaemonState.STOPPING

            # 1. Pause WebRTC (keep signaling server running for restart)
            self._interface_manager.pause_webrtc()

            # 2. Stop the backend
            await self._backend_manager.stop(goto_sleep=goto_sleep_on_stop)

            # 3. Stop FastAPI server
            await self._interface_manager.stop_server()

            if self._status.state != DaemonState.ERROR:
                self.logger.info("Daemon stopped successfully.")
                self._status.state = DaemonState.STOPPED

        except Exception as e:
            self.logger.error(f"Error while stopping the daemon: {e}")
            self._status.state = DaemonState.ERROR
            self._status.error = str(e)
        except KeyboardInterrupt:
            self.logger.warning("Daemon already stopping...")

        return self._status.state

    async def restart(
        self,
        sim: Optional[bool] = None,
        mockup_sim: Optional[bool] = None,
        serialport: Optional[str] = None,
        scene: Optional[str] = None,
        headless: Optional[bool] = None,
        use_audio: Optional[bool] = None,
        localhost_only: Optional[bool] = None,
        wake_up_on_start: Optional[bool] = None,
        goto_sleep_on_stop: Optional[bool] = None,
    ) -> DaemonState:
        """Restart the Reachy Mini daemon.

        Args:
            sim: If True, run in simulation mode. None uses previous value.
            mockup_sim: If True, run mockup sim. None uses previous value.
            serialport: Serial port. None uses previous value.
            scene: Scene to load. None uses previous value.
            headless: Run headless. None uses previous value.
            use_audio: Enable audio. None uses previous value.
            localhost_only: Localhost only. None uses previous value.
            wake_up_on_start: Wake up on start. None means False.
            goto_sleep_on_stop: Go to sleep on stop. None means False.

        Returns:
            DaemonState: The current state after attempting to restart.

        """
        if self._status.state == DaemonState.STOPPED:
            self.logger.warning("Daemon is not running.")
            return self._status.state

        if self._status.state in (DaemonState.RUNNING, DaemonState.ERROR):
            self.logger.info("Restarting Reachy Mini daemon...")

            await self.stop(
                goto_sleep_on_stop=goto_sleep_on_stop
                if goto_sleep_on_stop is not None
                else False
            )

            params = {
                "sim": sim if sim is not None else self._start_params.get("sim", False),
                "mockup_sim": mockup_sim
                if mockup_sim is not None
                else self._start_params.get("mockup_sim", False),
                "serialport": serialport
                if serialport is not None
                else self._start_params.get("serialport", "auto"),
                "scene": scene
                if scene is not None
                else self._start_params.get("scene", "empty"),
                "headless": headless
                if headless is not None
                else self._start_params.get("headless", False),
                "use_audio": use_audio
                if use_audio is not None
                else self._start_params.get("use_audio", True),
                "localhost_only": localhost_only
                if localhost_only is not None
                else self._start_params.get("localhost_only", True),
                "wake_up_on_start": wake_up_on_start
                if wake_up_on_start is not None
                else False,
                "check_collision": self._start_params.get("check_collision", False),
                "kinematics_engine": self._start_params.get(
                    "kinematics_engine", KinematicsEngine.ANALYTICAL
                ),
                "hardware_config_filepath": self._start_params.get(
                    "hardware_config_filepath"
                ),
                "fastapi_host": self._start_params.get("fastapi_host", "127.0.0.1"),
                "fastapi_port": self._start_params.get("fastapi_port", 8000),
            }

            return await self.start(**params)

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

    async def run4ever(self, args: DaemonArgs) -> None:
        """Run the Reachy Mini daemon indefinitely.

        Starts the daemon (backend + FastAPI server) and blocks until shutdown.
        This is the main entry point when running from main.py.

        Args:
            args: Configuration arguments (DaemonArgs dataclass).

        """
        # Determine localhost_only from args
        localhost_only = args.localhost_only
        if localhost_only is None:
            localhost_only = not self.wireless_version

        await self.start(
            sim=args.sim,
            mockup_sim=args.mockup_sim,
            serialport=args.serialport,
            scene=args.scene,
            localhost_only=localhost_only,
            wake_up_on_start=args.wake_up_on_start,
            check_collision=args.check_collision,
            kinematics_engine=args.kinematics_engine,
            headless=args.headless,
            use_audio=args.use_audio,
            hardware_config_filepath=args.hardware_config_filepath,
            fastapi_host=args.fastapi_host,
            fastapi_port=args.fastapi_port,
        )

        if self._status.state == DaemonState.RUNNING:
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

        await self.stop(goto_sleep_on_stop=args.goto_sleep_on_stop)
