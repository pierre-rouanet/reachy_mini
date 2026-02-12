"""Daemon entry point for the Reachy Mini robot.

This script serves as the command-line interface (CLI) entry point for the Reachy Mini daemon.
It initializes the daemon with specified parameters such as simulation mode, serial port,
scene to load, and logging level. The daemon runs indefinitely, handling requests and
managing the robot's state.

"""

import asyncio
import logging
import sys
import types
from typing import Any

import tyro

from reachy_mini.daemon.args import DaemonArgs
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
    args = tyro.cli(DaemonArgs)

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
