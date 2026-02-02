"""Arguments dataclass for the Reachy Mini daemon."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


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
            Path(__file__).parent.parent.parent
            / "assets"
            / "config"
            / "hardware_config.yaml"
        ).resolve()
    )


@dataclass
class DaemonArgs:
    """Arguments for configuring the Reachy Mini daemon.

    This dataclass defines all configuration options for the daemon.
    It can be used with simple-parsing for automatic CLI generation.
    """

    # Logging options
    log_level: LogLevel = field(
        default=LogLevel.INFO,
        metadata={"help": "Set the logging level."},
    )
    log_file: Optional[str] = field(
        default=None,
        metadata={"help": "Path to a file to write logs to."},
    )

    # Daemon mode options
    wireless_version: bool = field(
        default=False,
        metadata={"help": "Use the wireless version of Reachy Mini."},
    )
    desktop_app_daemon: bool = field(
        default=False,
        metadata={"help": "Use the desktop version of Reachy Mini."},
    )

    # Robot identification
    robot_name: str = field(
        default="reachy_mini",
        metadata={"help": "Name of the robot."},
    )

    # Real robot mode
    serialport: str = field(
        default="auto",
        metadata={
            "help": "Serial port for real motors (auto to find automatically).",
            "alias": "-p",
        },
    )
    hardware_config_filepath: Optional[str] = field(
        default=None,
        metadata={"help": "Path to the hardware configuration YAML file."},
    )

    # Simulation mode
    sim: bool = field(
        default=False,
        metadata={"help": "Run in simulation mode using MuJoCo."},
    )
    mockup_sim: bool = field(
        default=False,
        metadata={"help": "Run in mockup simulation mode (no MuJoCo required)."},
    )
    scene: str = field(
        default="empty",
        metadata={"help": "Name of the scene to load."},
    )
    headless: bool = field(
        default=False,
        metadata={"help": "Run the daemon in headless mode."},
    )
    use_audio: bool = field(
        default=True,
        metadata={"help": "Enable audio."},
    )

    # Kinematics options
    kinematics_engine: KinematicsEngine = field(
        default=KinematicsEngine.ANALYTICAL,
        metadata={"help": "Set the kinematics engine."},
    )
    check_collision: bool = field(
        default=False,
        metadata={"help": "Enable collision checking."},
    )

    # Daemon lifecycle options
    autostart: bool = field(
        default=True,
        metadata={"help": "Automatically start the backend on launch."},
    )
    timeout_health_check: Optional[float] = field(
        default=None,
        metadata={"help": "Set the health check timeout in seconds."},
    )

    wake_up_on_start: bool = field(
        default=True,
        metadata={"help": "Wake up the robot on backend start."},
    )
    goto_sleep_on_stop: bool = field(
        default=True,
        metadata={"help": "Put the robot to sleep on backend stop."},
    )
    preload_datasets: bool = field(
        default=False,
        metadata={"help": "Pre-download recorded move datasets at startup."},
    )
    dataset_update_interval_hours: float = field(
        default=24.0,
        metadata={"help": "Interval in hours for background dataset update checks (0 to disable)."},
    )

    # Server options
    fastapi_host: str = field(
        default="0.0.0.0",
        metadata={"help": "Host address for FastAPI server."},
    )
    fastapi_port: int = field(
        default=8000,
        metadata={"help": "Port for FastAPI server."},
    )
    localhost_only: Optional[bool] = field(
        default=None,
        metadata={"help": "Restrict the server to localhost only."},
    )

    def __post_init__(self) -> None:
        """Set dynamic defaults after initialization."""
        if self.hardware_config_filepath is None:
            self.hardware_config_filepath = _default_hardware_config_path()
