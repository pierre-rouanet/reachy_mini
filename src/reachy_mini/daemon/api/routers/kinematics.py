"""Kinematics router for handling kinematics-related requests.

This module defines the API endpoints for interacting with the kinematics
subsystem of the robot. It provides endpoints for retrieving URDF representation,
and other kinematics-related information.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from reachy_mini.motor_controller.abstract import MotorController

from ..dependencies import get_motor_controller

router = APIRouter(
    prefix="/kinematics",
)

STL_ASSETS_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "descriptions"
    / "reachy_mini"
    / "urdf"
    / "assets"
)


@router.get("/info")
async def get_kinematics_info(
    motor_controller: MotorController = Depends(get_motor_controller),
) -> dict[str, Any]:
    """Get the current information of the kinematics."""
    return {
        "info": {
            "engine": motor_controller.kinematics_engine,
            "collision check": motor_controller.check_collision,
        }
    }


@router.get("/urdf")
async def get_urdf(motor_controller: MotorController = Depends(get_motor_controller)) -> dict[str, str]:
    """Get the URDF representation of the robot."""
    return {"urdf": motor_controller.get_urdf()}


@router.get("/stl/{filename}")
async def get_stl_file(filename: Path) -> Response:
    """Get the path to an STL asset file."""
    file_path = STL_ASSETS_DIR / filename
    try:
        with open(file_path, "rb") as file:
            content = file.read()
            return Response(content, media_type="model/stl")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"STL file not found {file_path}")
