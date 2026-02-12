"""Arguments dataclass for the Reachy Mini daemon."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import tyro


class KinematicsEngine(str, Enum):
    """Available kinematics engines."""

    PLACO = "Placo"
    NN = "NN"
    ANALYTICAL = "AnalyticalKinematics"


class LogLevel(str, Enum):
    """Available log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def _default_hardware_config_path() -> str:
    """Get the default hardware config path."""
    return str(
        (
            Path(__file__).parent.parent / "assets" / "config" / "hardware_config.yaml"
        ).resolve()
    )


@dataclass
class DaemonArgs:
    """Run the Reachy Mini daemon."""

    # Logging options
    log_level: Annotated[LogLevel, tyro.conf.arg(help="Set the logging level.")] = (
        LogLevel.INFO
    )
    log_file: Annotated[
        Optional[str], tyro.conf.arg(help="Path to a file to write logs to.")
    ] = None

    # Daemon mode options
    wireless_version: Annotated[
        bool, tyro.conf.arg(help="Use the wireless version of Reachy Mini.")
    ] = False
    desktop_app_daemon: Annotated[
        bool, tyro.conf.arg(help="Use the desktop version of Reachy Mini.")
    ] = False

    # Robot identification
    robot_name: Annotated[str, tyro.conf.arg(help="Name of the robot.")] = "reachy_mini"

    # Real robot mode
    serialport: Annotated[
        str,
        tyro.conf.arg(
            aliases=["-p"],
            help="Serial port for real motors (auto to find automatically).",
        ),
    ] = "auto"
    hardware_config_filepath: Annotated[
        Optional[str],
        tyro.conf.arg(help="Path to the hardware configuration YAML file."),
    ] = None

    # Simulation mode
    sim: Annotated[bool, tyro.conf.arg(help="Run in simulation mode using MuJoCo.")] = (
        False
    )
    mockup_sim: Annotated[
        bool, tyro.conf.arg(help="Run in mockup simulation mode (no MuJoCo required).")
    ] = False
    scene: Annotated[str, tyro.conf.arg(help="Name of the scene to load.")] = "empty"
    headless: Annotated[
        bool, tyro.conf.arg(help="Run the daemon in headless mode.")
    ] = False
    use_audio: Annotated[bool, tyro.conf.arg(help="Enable audio.")] = True

    # Kinematics options
    kinematics_engine: Annotated[
        KinematicsEngine,
        tyro.conf.EnumChoicesFromValues,
        tyro.conf.arg(help="Set the kinematics engine."),
    ] = KinematicsEngine.ANALYTICAL
    check_collision: Annotated[
        bool, tyro.conf.arg(help="Enable collision checking.")
    ] = False

    # Daemon lifecycle options
    autostart: Annotated[
        bool, tyro.conf.arg(help="Automatically start the backend on launch.")
    ] = True
    timeout_health_check: Annotated[
        Optional[float],
        tyro.conf.arg(help="Set the health check timeout in seconds."),
    ] = None

    wake_up_on_start: Annotated[
        bool, tyro.conf.arg(help="Wake up the robot on backend start.")
    ] = True
    goto_sleep_on_stop: Annotated[
        bool, tyro.conf.arg(help="Put the robot to sleep on backend stop.")
    ] = True
    preload_datasets: Annotated[
        bool,
        tyro.conf.arg(help="Pre-download recorded move datasets at startup."),
    ] = False
    dataset_update_interval_hours: Annotated[
        float,
        tyro.conf.arg(
            help="Interval in hours for background dataset update checks (0 to disable)."
        ),
    ] = 24.0

    # Server options
    fastapi_host: Annotated[
        str, tyro.conf.arg(help="Host address for FastAPI server.")
    ] = "0.0.0.0"
    fastapi_port: Annotated[int, tyro.conf.arg(help="Port for FastAPI server.")] = 8000
    localhost_only: Annotated[
        Optional[bool],
        tyro.conf.arg(help="Restrict the server to localhost only."),
    ] = None

    def __post_init__(self) -> None:
        """Set dynamic defaults after initialization."""
        if self.hardware_config_filepath is None:
            self.hardware_config_filepath = _default_hardware_config_path()
