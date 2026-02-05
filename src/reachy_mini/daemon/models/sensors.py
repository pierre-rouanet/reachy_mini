"""Sensor data models.

Extensible sensor system using a base class pattern.
To add a new sensor:
1. Create a subclass of SensorData with sensor_type as a Literal
2. Register a SensorProvider in the daemon

These models are used by both HTTP and Streaming APIs.
"""

from typing import Literal

from pydantic import BaseModel


class SensorData(BaseModel):
    """Base class for all sensor data.

    All sensor types must inherit from this class and define
    a unique sensor_type literal.
    """

    sensor_type: str
    timestamp: float | None = None


class DoAData(SensorData):
    """Direction of Arrival from the microphone array.

    Attributes:
        angle: Angle in radians (0=left, pi/2=front, pi=right)
        speech_detected: Whether speech was detected
    """

    sensor_type: Literal["doa"] = "doa"
    angle: float
    speech_detected: bool


class IMUData(SensorData):
    """Inertial Measurement Unit data.

    Attributes:
        accelerometer: Linear acceleration (x, y, z) in m/s^2
        gyroscope: Angular velocity (x, y, z) in rad/s
        quaternion: Orientation quaternion (w, x, y, z)
        temperature: Sensor temperature in Celsius
    """

    sensor_type: Literal["imu"] = "imu"
    accelerometer: tuple[float, float, float]
    gyroscope: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]
    temperature: float | None = None
