"""Daemon entry point for the Reachy Mini robot.

This script serves as the command-line interface (CLI) entry point for the Reachy Mini daemon.
It initializes the daemon with specified parameters such as simulation mode, serial port,
scene to load, and logging level. The daemon runs indefinitely, handling requests and
managing the robot's state.

"""

import argparse
import asyncio
import logging
import sys
import types
from typing import Any

from reachy_mini.daemon.args import DaemonArgs, KinematicsEngine, LogLevel
from reachy_mini.daemon.daemon import Daemon
from reachy_mini.media.audio_utils import (
    check_reachymini_asoundrc,
    write_asoundrc_to_home,
)
from reachy_mini.utils.wireless_version.startup_check import (
    check_and_fix_restore_venv,
    check_and_fix_venvs_ownership,
    check_and_sync_apps_venv_sdk,
    check_and_update_bluetooth_service,
    check_and_update_wireless_launcher,
)


def _setup_logging(args: DaemonArgs) -> None:
    """Configure logging for the daemon."""
    root_logger = logging.getLogger()
    root_logger.setLevel(args.log_level.value)

    # Create handler that writes to stderr with immediate flush
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(args.log_level.value)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root_logger.addHandler(handler)

    # Explicitly configure the apps.manager logger to ensure propagation
    apps_logger = logging.getLogger("reachy_mini.apps.manager")
    apps_logger.setLevel(args.log_level.value)
    apps_logger.propagate = True

    # Install exception hook to catch uncaught exceptions
    def exception_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: types.TracebackType | None,
    ) -> None:
        """Log uncaught exceptions with full traceback."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        root_logger.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )
        sys.stderr.flush()

    sys.excepthook = exception_hook


def _setup_asyncio_exception_handler() -> None:
    """Set up asyncio exception handler to catch unhandled task exceptions."""
    loop = asyncio.get_running_loop()

    def asyncio_exception_handler(
        loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        """Handle exceptions in asyncio tasks."""
        exception = context.get("exception")
        if exception:
            logging.error(
                f"Unhandled exception in asyncio task: {context.get('message', 'No message')}",
                exc_info=(type(exception), exception, exception.__traceback__),
            )
        else:
            logging.error(f"Asyncio error: {context}")
        sys.stderr.flush()

    loop.set_exception_handler(asyncio_exception_handler)


def _create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with all daemon options."""
    defaults = DaemonArgs()

    parser = argparse.ArgumentParser(description="Run the Reachy Mini daemon.")

    # Logging options
    parser.add_argument(
        "--log-level",
        type=str,
        choices=[level.value for level in LogLevel],
        default=defaults.log_level.value,
        help=f"Set the logging level (default: {defaults.log_level.value}).",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=defaults.log_file,
        help="Path to a file to write logs to.",
    )

    # Daemon mode options
    parser.add_argument(
        "--wireless-version",
        action="store_true",
        default=defaults.wireless_version,
        help="Use the wireless version of Reachy Mini.",
    )
    parser.add_argument(
        "--desktop-app-daemon",
        action="store_true",
        default=defaults.desktop_app_daemon,
        help="Use the desktop version of Reachy Mini.",
    )

    # Robot identification
    parser.add_argument(
        "--robot-name",
        type=str,
        default=defaults.robot_name,
        help=f"Name of the robot (default: {defaults.robot_name}).",
    )

    # Real robot mode
    parser.add_argument(
        "-p",
        "--serialport",
        type=str,
        default=defaults.serialport,
        help="Serial port for real motors (auto to find automatically).",
    )
    parser.add_argument(
        "--hardware-config-filepath",
        type=str,
        default=None,
        help="Path to the hardware configuration YAML file.",
    )

    # Simulation mode
    parser.add_argument(
        "--sim",
        action="store_true",
        default=defaults.sim,
        help="Run in simulation mode using MuJoCo.",
    )
    parser.add_argument(
        "--mockup-sim",
        action="store_true",
        default=defaults.mockup_sim,
        help="Run in mockup simulation mode (no MuJoCo required).",
    )
    parser.add_argument(
        "--scene",
        type=str,
        default=defaults.scene,
        help=f"Name of the scene to load (default: {defaults.scene}).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=defaults.headless,
        help="Run the daemon in headless mode.",
    )
    parser.add_argument(
        "--use-audio",
        action=argparse.BooleanOptionalAction,
        default=defaults.use_audio,
        help="Enable audio.",
    )

    # Kinematics options
    parser.add_argument(
        "--kinematics-engine",
        type=str,
        choices=[engine.value for engine in KinematicsEngine],
        default=defaults.kinematics_engine.value,
        help=f"Set the kinematics engine (default: {defaults.kinematics_engine.value}).",
    )
    parser.add_argument(
        "--check-collision",
        action="store_true",
        default=defaults.check_collision,
        help="Enable collision checking.",
    )

    # Daemon lifecycle options
    parser.add_argument(
        "--autostart",
        action=argparse.BooleanOptionalAction,
        default=defaults.autostart,
        help="Automatically start the backend on launch.",
    )
    parser.add_argument(
        "--timeout-health-check",
        type=float,
        default=defaults.timeout_health_check,
        help="Set the health check timeout in seconds.",
    )
    parser.add_argument(
        "--wake-up-on-start",
        action=argparse.BooleanOptionalAction,
        default=defaults.wake_up_on_start,
        help="Wake up the robot on backend start.",
    )
    parser.add_argument(
        "--goto-sleep-on-stop",
        action=argparse.BooleanOptionalAction,
        default=defaults.goto_sleep_on_stop,
        help="Put the robot to sleep on backend stop.",
    )
    parser.add_argument(
        "--preload-datasets",
        action=argparse.BooleanOptionalAction,
        default=defaults.preload_datasets,
        help="Pre-download recorded move datasets at startup.",
    )
    parser.add_argument(
        "--dataset-update-interval-hours",
        type=float,
        default=defaults.dataset_update_interval_hours,
        help="Interval in hours for background dataset update checks (0 to disable).",
    )

    # Server options
    parser.add_argument(
        "--fastapi-host",
        type=str,
        default=defaults.fastapi_host,
        help=f"Host address for FastAPI server (default: {defaults.fastapi_host}).",
    )
    parser.add_argument(
        "--fastapi-port",
        type=int,
        default=defaults.fastapi_port,
        help=f"Port for FastAPI server (default: {defaults.fastapi_port}).",
    )
    parser.add_argument(
        "--localhost-only",
        action=argparse.BooleanOptionalAction,
        default=defaults.localhost_only,
        help="Restrict the server to localhost only.",
    )

    return parser


def _parse_args_to_daemon_args(namespace: argparse.Namespace) -> DaemonArgs:
    """Convert parsed argparse namespace to DaemonArgs dataclass."""
    return DaemonArgs(
        log_level=LogLevel(namespace.log_level),
        log_file=namespace.log_file,
        wireless_version=namespace.wireless_version,
        desktop_app_daemon=namespace.desktop_app_daemon,
        robot_name=namespace.robot_name,
        serialport=namespace.serialport,
        hardware_config_filepath=namespace.hardware_config_filepath,
        sim=namespace.sim,
        mockup_sim=namespace.mockup_sim,
        scene=namespace.scene,
        headless=namespace.headless,
        use_audio=namespace.use_audio,
        kinematics_engine=KinematicsEngine(namespace.kinematics_engine),
        check_collision=namespace.check_collision,
        autostart=namespace.autostart,
        timeout_health_check=namespace.timeout_health_check,
        wake_up_on_start=namespace.wake_up_on_start,
        goto_sleep_on_stop=namespace.goto_sleep_on_stop,
        preload_datasets=namespace.preload_datasets,
        dataset_update_interval_hours=namespace.dataset_update_interval_hours,
        fastapi_host=namespace.fastapi_host,
        fastapi_port=namespace.fastapi_port,
        localhost_only=namespace.localhost_only,
    )


def run_daemon(args: DaemonArgs) -> None:
    """Run the daemon with the given arguments."""
    _setup_logging(args)

    async def _run() -> None:
        _setup_asyncio_exception_handler()

        daemon = Daemon(args)
        await daemon.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logging.info("Shutdown complete.")
    except Exception as e:
        logging.exception(f"Error during shutdown: {e}")
        sys.stderr.flush()
        raise


def main() -> None:
    """Parse arguments and run the daemon."""
    parser = _create_parser()
    namespace = parser.parse_args()
    args = _parse_args_to_daemon_args(namespace)

    if args.log_file:
        file_handler = logging.FileHandler(args.log_file, mode="a")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logging.getLogger().addHandler(file_handler)
        logging.getLogger().setLevel(args.log_level.value)

    if args.wireless_version:
        # Check and fix ownership of /venvs directory
        check_and_fix_venvs_ownership(custom_logger=logging.getLogger())

        # Check and update bluetooth service if needed
        check_and_update_bluetooth_service()

        # Check and update wireless launcher if needed
        check_and_update_wireless_launcher()

        # Check and sync apps_venv SDK version with daemon
        check_and_sync_apps_venv_sdk()

        # Check and fix restore venv if it has legacy editable install
        check_and_fix_restore_venv()

        if check_reachymini_asoundrc():
            logging.getLogger().info(
                "~/.asoundrc correctly configured for Reachy Mini Audio."
            )
        else:
            logging.getLogger().warning(
                "~/.asoundrc not found or not correctly configured for Reachy Mini Audio. "
                "Creating a new one."
            )
            write_asoundrc_to_home()

    run_daemon(args)


if __name__ == "__main__":
    main()
