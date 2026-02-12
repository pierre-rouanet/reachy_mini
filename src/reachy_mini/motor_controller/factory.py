"""Factory for creating motor controller instances.

Selects the appropriate motor controller (MuJoCo, mockup, or real hardware)
based on configuration and handles serial port detection and motor reflashing.
"""

import logging

from reachy_mini.daemon.utils import find_serial_port
from reachy_mini.tools.reflash_motors import reflash_motors

from .abstract import MotorController
from .mockup_sim import MockupController
from .mujoco import MujocoController
from .robot import RobotController

logger = logging.getLogger(__name__)


def create_motor_controller(
    sim: bool,
    mockup_sim: bool,
    serialport: str,
    scene: str,
    check_collision: bool,
    kinematics_engine: str,
    headless: bool,
    log_level: str = "INFO",
    wireless_version: bool = False,
    hardware_config_filepath: str | None = None,
    reflash_motors_on_start: bool = True,
) -> MotorController:
    """Create the appropriate motor controller instance.

    Args:
        sim: If True, create MuJoCo simulation controller.
        mockup_sim: If True, create mockup simulation controller.
        serialport: Serial port for real motors. "auto" to auto-detect.
        scene: Scene to load in simulation.
        check_collision: Enable collision checking.
        kinematics_engine: Kinematics engine to use.
        headless: Run MuJoCo without GUI.
        log_level: Logging level.
        wireless_version: Whether running on wireless Reachy Mini hardware.
        hardware_config_filepath: Path to hardware config YAML.
        reflash_motors_on_start: Reflash motors on startup.

    Returns:
        The created motor controller instance.

    Raises:
        RuntimeError: If serial port auto-detection fails.

    """
    if mockup_sim:
        return MockupController(
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
        )
    elif sim:
        return MujocoController(
            scene=scene,
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            headless=headless,
        )
    else:
        if serialport == "auto":
            ports = find_serial_port(wireless_version=wireless_version)

            if len(ports) == 0:
                raise RuntimeError(
                    "No Reachy Mini serial port found. "
                    "Check USB connection and permissions. "
                    "Or directly specify the serial port using --serialport."
                )
            elif len(ports) > 1:
                raise RuntimeError(
                    f"Multiple Reachy Mini serial ports found {ports}. "
                    "Please specify the serial port using --serialport."
                )

            serialport = ports[0]
            logger.info(f"Found Reachy Mini serial port: {serialport}")

        logger.info(
            f"Creating RobotController: serialport={serialport}, "
            f"check_collision={check_collision}, kinematics_engine={kinematics_engine}"
        )

        if reflash_motors_on_start:
            reflash_motors(serialport, dont_light_up=True)

        return RobotController(
            serialport=serialport,
            log_level=log_level,
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            hardware_config_filepath=hardware_config_filepath,
        )
