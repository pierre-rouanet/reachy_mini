"""Daemon entry point for the Reachy Mini robot.

This script serves as the command-line interface (CLI) entry point for the Reachy Mini daemon.
It initializes the daemon with specified parameters such as simulation mode, serial port,
scene to load, and logging level. The daemon runs indefinitely, handling requests and
managing the robot's state.

"""

import argparse
import asyncio
import logging
import types
from pathlib import Path
from typing import Any

from reachy_mini.daemon.daemon import Args, Daemon
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


def run_app(args: Args) -> None:
    """Run the FastAPI app with Uvicorn."""
    # Configure logging to ensure all logs go to stderr (captured by systemd)
    import sys

    root_logger = logging.getLogger()
    root_logger.setLevel(args.log_level)

    # Create handler that writes to stderr with immediate flush
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(args.log_level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root_logger.addHandler(handler)

    # Explicitly configure the apps.manager logger to ensure propagation
    apps_logger = logging.getLogger("reachy_mini.apps.manager")
    apps_logger.setLevel(args.log_level)
    apps_logger.propagate = True  # Ensure it propagates to root logger

    # Install exception hook to catch uncaught exceptions
    def exception_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: types.TracebackType | None,
    ) -> None:
        """Log uncaught exceptions with full traceback."""
        if issubclass(exc_type, KeyboardInterrupt):
            # Allow KeyboardInterrupt to exit normally
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        root_logger.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )
        sys.stderr.flush()

    sys.excepthook = exception_hook

    async def run_server() -> None:
        # Set up asyncio exception handler to catch unhandled task exceptions
        loop = asyncio.get_running_loop()

        def asyncio_exception_handler(
            loop: asyncio.AbstractEventLoop, context: dict[str, Any]
        ) -> None:
            """Handle exceptions in asyncio tasks."""
            exception = context.get("exception")
            if exception:
                root_logger.error(
                    f"Unhandled exception in asyncio task: {context.get('message', 'No message')}",
                    exc_info=(type(exception), exception, exception.__traceback__),
                )
            else:
                root_logger.error(f"Asyncio error: {context}")
            sys.stderr.flush()

        loop.set_exception_handler(asyncio_exception_handler)

        health_check_event = asyncio.Event()
        health_check_task = None

        daemon = Daemon(args=args)

        async def health_check_timeout(timeout_seconds: float) -> None:
            while True:
                try:
                    await asyncio.wait_for(
                        health_check_event.wait(),
                        timeout=timeout_seconds,
                    )
                    health_check_event.clear()
                except asyncio.TimeoutError:
                    logging.warning("Health check timeout reached, stopping app.")
                    # server.should_exit = True
                    await daemon.stop()
                    break
                except asyncio.CancelledError:
                    logging.info("Health check task cancelled.")
                    break

        try:
            if args.timeout_health_check is not None:
                health_check_task = asyncio.create_task(
                    health_check_timeout(args.timeout_health_check)
                )
            localhost_only = (
                args.localhost_only
                if args.localhost_only is not None
                else (False if args.wireless_version else True)
            )
            await daemon.run4ever(
                serialport=args.serialport,
                sim=args.sim,
                mockup_sim=args.mockup_sim,
                scene=args.scene,
                headless=args.headless,
                websocket_uri=args.websocket_uri,
                stream_media=args.stream_media,
                use_audio=args.use_audio,
                kinematics_engine=args.kinematics_engine,
                check_collision=args.check_collision,
                wake_up_on_start=args.wake_up_on_start,
                localhost_only=localhost_only,
            )
        except KeyboardInterrupt:
            logging.info("Received Ctrl-C, shutting down gracefully.")
        except Exception as e:
            logging.exception(f"Error during server operation: {e}")
            raise
        finally:
            # Cancel health check task if it exists
            if health_check_task and not health_check_task.done():
                health_check_task.cancel()
                try:
                    await health_check_task
                except asyncio.CancelledError:
                    pass

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logging.info("Shutdown complete.")
    except Exception as e:
        logging.exception(f"Error during shutdown: {e}")
        sys.stderr.flush()
        raise


def main() -> None:
    """Run the FastAPI app with Uvicorn."""
    default_args = Args()

    parser = argparse.ArgumentParser(description="Run the Reachy Mini daemon.")
    parser.add_argument(
        "--wireless-version",
        action="store_true",
        default=default_args.wireless_version,
        help="Use the wireless version of Reachy Mini (default: False).",
    )
    parser.add_argument(
        "--desktop-app-daemon",
        action="store_true",
        default=default_args.desktop_app_daemon,
        help="Use the desktop version of Reachy Mini (default: False).",
    )

    parser.add_argument(
        "--robot-name",
        type=str,
        default=default_args.robot_name,
        help="Name of the robot (default: reachy_mini).",
    )

    # Real robot mode
    parser.add_argument(
        "-p",
        "--serialport",
        type=str,
        default=default_args.serialport,
        help="Serial port for real motors (default: will try to automatically find the port).",
    )
    default_hw_config_path = str(
        (
            Path(__file__).parent.parent.parent
            / "assets"
            / "config"
            / "hardware_config.yaml"
        ).resolve()
    )
    parser.add_argument(
        "--hardware-config-filepath",
        type=str,
        default=default_hw_config_path,
        help=f"Path to the hardware configuration YAML file (default: {default_hw_config_path}).",
    )
    # Simulation mode
    parser.add_argument(
        "--sim",
        action="store_true",
        default=default_args.sim,
        help="Run in simulation mode using Mujoco.",
    )
    parser.add_argument(
        "--mockup-sim",
        action="store_true",
        default=default_args.mockup_sim,
        help="Run in mockup simulation mode (no MuJoCo required).",
    )
    parser.add_argument(
        "--scene",
        type=str,
        default=default_args.scene,
        help="Name of the scene to load (default: empty)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=default_args.headless,
        help="Run the daemon in headless mode (default: False).",
    )
    parser.add_argument(
        "--websocket-uri",
        type=str,
        default=default_args.websocket_uri,
        help="WebSocket URI for remote control and streaming of the robot (default: None). Example: ws://localhost:8000",
    )
    parser.add_argument(
        "--stream-media",
        action="store_true",
        default=default_args.stream_media,
        help="Stream media to the WebSocket. Requires a WebSocket URI to be set. (default: False).",
    )
    parser.add_argument(
        "--deactivate-audio",
        action="store_false",
        dest="use_audio",
        default=default_args.use_audio,
        help="Deactivate audio (default: True).",
    )
    # Daemon options
    parser.add_argument(
        "--autostart",
        action="store_true",
        default=default_args.autostart,
        help="Automatically start the daemon on launch (default: True).",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_false",
        dest="autostart",
        help="Do not automatically start the daemon on launch (default: False).",
    )
    parser.add_argument(
        "--timeout-health-check",
        type=float,
        default=None,
        help="Set the health check timeout in seconds (default: None).",
    )
    parser.add_argument(
        "--wake-up-on-start",
        action="store_true",
        default=default_args.wake_up_on_start,
        help="Wake up the robot on daemon start (default: True).",
    )
    parser.add_argument(
        "--no-wake-up-on-start",
        action="store_false",
        dest="wake_up_on_start",
        help="Do not wake up the robot on daemon start (default: False).",
    )
    parser.add_argument(
        "--goto-sleep-on-stop",
        action="store_true",
        default=default_args.goto_sleep_on_stop,
        help="Put the robot to sleep on daemon stop (default: True).",
    )
    parser.add_argument(
        "--no-goto-sleep-on-stop",
        action="store_false",
        dest="goto_sleep_on_stop",
        help="Do not put the robot to sleep on daemon stop (default: False).",
    )
    parser.add_argument(
        "--preload-datasets",
        action="store_true",
        default=default_args.preload_datasets,
        help="Pre-download recorded move datasets (emotions, dances) at startup (default: False).",
    )
    parser.add_argument(
        "--no-preload-datasets",
        action="store_false",
        dest="preload_datasets",
        help="Do not pre-download datasets at startup (default: False).",
    )
    parser.add_argument(
        "--dataset-update-interval",
        type=float,
        default=default_args.dataset_update_interval_hours,
        dest="dataset_update_interval_hours",
        help="Interval in hours for background dataset update checks (default: 24.0, 0 to disable).",
    )
    # Server connectivity options
    parser.add_argument(
        "--localhost-only",
        action="store_true",
        default=default_args.localhost_only,
        help="Restrict the server to localhost only (default: True).",
    )
    parser.add_argument(
        "--no-localhost-only",
        action="store_false",
        dest="localhost_only",
        help="Allow the server to listen on all interfaces (default: False).",
    )
    # Kinematics options
    parser.add_argument(
        "--check-collision",
        action="store_true",
        default=default_args.check_collision,
        help="Enable collision checking (default: False).",
    )

    parser.add_argument(
        "--kinematics-engine",
        type=str,
        default=default_args.kinematics_engine,
        choices=["Placo", "NN", "AnalyticalKinematics"],
        help="Set the kinematics engine (default: AnalyticalKinematics).",
    )
    # FastAPI server options
    parser.add_argument(
        "--fastapi-host",
        type=str,
        default=default_args.fastapi_host,
    )
    parser.add_argument(
        "--fastapi-port",
        type=int,
        default=default_args.fastapi_port,
    )
    # Logging options
    parser.add_argument(
        "--log-level",
        type=str,
        default=default_args.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO).",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=default_args.log_file,
        help="Path to a file to write logs to.",
    )

    args = parser.parse_args()

    if args.log_file:
        file_handler = logging.FileHandler(args.log_file, mode="a")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logging.getLogger().addHandler(file_handler)
        logging.getLogger().setLevel(args.log_level)

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

    run_app(Args(**vars(args)))


if __name__ == "__main__":
    main()
