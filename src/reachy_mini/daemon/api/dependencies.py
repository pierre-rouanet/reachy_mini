"""FastAPI common request dependencies."""

from fastapi import HTTPException, Request, WebSocket

from reachy_mini.media.media_manager import MediaManager
from reachy_mini.motion import MoveTracker
from reachy_mini.motion.manager import MotionManager
from reachy_mini.motor_controller.abstract import MotorController

from ...apps.manager import AppManager
from ..daemon import Daemon


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


def get_move_tracker(request: Request) -> MoveTracker:
    """Get the move tracker as request dependency."""
    return get_daemon(request).move_tracker


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
