"""Tests for DaemonArgs dataclass and related enums."""

from pathlib import Path

from reachy_mini.daemon.args import DaemonArgs, KinematicsEngine, LogLevel


def test_default_args() -> None:
    """Verify all DaemonArgs defaults are correct."""
    args = DaemonArgs()
    assert args.log_level == LogLevel.INFO
    assert args.log_file is None
    assert args.wireless_version is False
    assert args.desktop_app_daemon is False
    assert args.robot_name == "reachy_mini"
    assert args.serialport == "auto"
    assert args.sim is False
    assert args.mockup_sim is False
    assert args.scene == "empty"
    assert args.headless is False
    assert args.use_audio is True
    assert args.kinematics_engine == KinematicsEngine.ANALYTICAL
    assert args.check_collision is False
    assert args.autostart is True
    assert args.timeout_health_check is None
    assert args.wake_up_on_start is True
    assert args.goto_sleep_on_stop is True
    assert args.preload_datasets is False
    assert args.dataset_update_interval_hours == 24.0
    assert args.fastapi_host == "0.0.0.0"
    assert args.fastapi_port == 8000
    assert args.localhost_only is None


def test_kinematics_engine_values() -> None:
    """KinematicsEngine can be constructed from string values."""
    assert KinematicsEngine("Placo") == KinematicsEngine.PLACO
    assert KinematicsEngine("NN") == KinematicsEngine.NN
    assert KinematicsEngine("AnalyticalKinematics") == KinematicsEngine.ANALYTICAL
    # Values differ from names
    assert KinematicsEngine.PLACO.value == "Placo"
    assert KinematicsEngine.ANALYTICAL.value == "AnalyticalKinematics"


def test_log_level_values() -> None:
    """LogLevel values match standard logging level names."""
    assert LogLevel("DEBUG") == LogLevel.DEBUG
    assert LogLevel("INFO") == LogLevel.INFO
    assert LogLevel("WARNING") == LogLevel.WARNING
    assert LogLevel("ERROR") == LogLevel.ERROR
    assert LogLevel("CRITICAL") == LogLevel.CRITICAL


def test_hardware_config_default() -> None:
    """__post_init__ sets default hardware config path."""
    args = DaemonArgs()
    assert args.hardware_config_filepath is not None
    assert "hardware_config.yaml" in args.hardware_config_filepath
    assert Path(args.hardware_config_filepath).is_file()


def test_custom_args() -> None:
    """Override multiple fields at once."""
    args = DaemonArgs(
        sim=True,
        headless=True,
        log_level=LogLevel.DEBUG,
        kinematics_engine=KinematicsEngine.PLACO,
        fastapi_port=9000,
        wake_up_on_start=False,
    )
    assert args.sim is True
    assert args.headless is True
    assert args.log_level == LogLevel.DEBUG
    assert args.kinematics_engine == KinematicsEngine.PLACO
    assert args.fastapi_port == 9000
    assert args.wake_up_on_start is False
    # Other fields keep defaults
    assert args.use_audio is True
    assert args.robot_name == "reachy_mini"
