"""FastAPI common request dependencies."""

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, WebSocket

from reachy_mini.media.media_manager import MediaManager
from reachy_mini.motion.manager import MotionManager
from reachy_mini.motor_controller.abstract import MotorController

from ...apps.manager import AppManager
from ..daemon import Daemon

if TYPE_CHECKING:
    from reachy_mini.sensors.imu import IMUSensor


def get_daemon(request: Request) -> Daemon:
    """Get the daemon as request dependency."""
    assert isinstance(request.app.state.daemon, Daemon)
    return request.app.state.daemon


def get_motor_controller(request: Request) -> MotorController:
    """Get the motor controller as request dependency."""
    motor_controller = request.app.state.daemon.motor_controller

    if motor_controller is None or not motor_controller.ready.is_set():
        raise HTTPException(status_code=503, detail="Motor controller not running")

    assert isinstance(motor_controller, MotorController)
    return motor_controller


def get_motion_manager(request: Request) -> MotionManager:
    """Get the motion manager as request dependency."""
    daemon = get_daemon(request)
    if daemon.motor_controller is None or not daemon.motor_controller.ready.is_set():
        raise HTTPException(status_code=503, detail="Motor controller not running")
    return daemon.motion_manager


def get_audio(request: Request) -> MediaManager | None:
    """Get the audio manager as request dependency (may be None if audio disabled)."""
    return get_daemon(request).audio


def get_imu(request: Request) -> "IMUSensor | None":
    """Get the IMU sensor as request dependency (may be None if not available)."""
    return get_daemon(request).imu


def get_app_manager(request: Request) -> "AppManager":
    """Get the app manager as request dependency."""
    assert isinstance(request.app.state.app_manager, AppManager)
    return request.app.state.app_manager


def ws_get_motor_controller(websocket: WebSocket) -> MotorController:
    """Get the motor controller as websocket dependency."""
    motor_controller = websocket.app.state.daemon.motor_controller

    if motor_controller is None or not motor_controller.ready.is_set():
        raise HTTPException(status_code=503, detail="Motor controller not running")

    assert isinstance(motor_controller, MotorController)
    return motor_controller
