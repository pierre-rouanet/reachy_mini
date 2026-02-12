"""BMI088 IMU sensor for wireless Reachy Mini."""

import logging
from typing import Any

from reachy_mini.daemon.models import IMUData

logger = logging.getLogger(__name__)


class IMUSensor:
    """BMI088 IMU sensor.

    Reads accelerometer, gyroscope, quaternion, and temperature data.
    Only available on wireless Reachy Mini hardware.
    """

    def __init__(self, i2c_bus: int = 4) -> None:
        """Initialize the BMI088 IMU sensor.

        Args:
            i2c_bus: I2C bus number. Default is 4.

        Raises:
            ImportError: If the bmi088 package is not installed.
            Exception: If the sensor cannot be initialized.

        """
        from bmi088 import BMI088

        self._bmi088: Any = BMI088(i2c_bus=i2c_bus)
        logger.info("BMI088 IMU initialized successfully")

    def get_data(self, dt: float) -> IMUData | None:
        """Read current IMU data.

        Args:
            dt: Time step in seconds for quaternion integration.

        Returns:
            IMUData with sensor readings, or None if reading fails.

        """
        try:
            accel_x, accel_y, accel_z = self._bmi088.read_accelerometer(m_per_s2=True)
            gyro_x, gyro_y, gyro_z = self._bmi088.read_gyroscope(deg_per_s=False)
            quat = self._bmi088.get_quat(dt)
            temperature = self._bmi088.read_temperature()

            return IMUData(
                accelerometer=(float(accel_x), float(accel_y), float(accel_z)),
                gyroscope=(float(gyro_x), float(gyro_y), float(gyro_z)),
                quaternion=(float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])),
                temperature=float(temperature),
            )
        except Exception as e:
            logger.error(f"Error reading IMU data: {e}")
            return None
