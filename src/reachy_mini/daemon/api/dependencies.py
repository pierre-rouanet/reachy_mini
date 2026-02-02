"""FastAPI common request dependencies."""

from fastapi import HTTPException, Request, WebSocket

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
