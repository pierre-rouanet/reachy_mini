# Data Models Reference

This document describes all data models used by the Reachy Mini API. These models are shared between the HTTP API and Streaming API (WebSocket/WebRTC).

**Source:** `src/reachy_mini/daemon/models/` and `src/reachy_mini/daemon/streaming/messages.py`

---

## Coordinate Frames and Units

### Reference Frame

Reachy Mini uses a **right-handed coordinate system** with the origin at the base of the robot:

```
        +Z (up)
         |
         |
         |_______ +Y (left, from robot's perspective)
        /
       /
      +X (forward, direction robot faces)
```

**Key points:**
- **Origin:** Center of the robot base, at the bottom of the body rotation axis
- **+X:** Forward (direction the robot faces when body_rotation = 0)
- **+Y:** Left (from the robot's perspective)
- **+Z:** Up (vertical)

### Units

| Quantity | Unit | Notes |
|----------|------|-------|
| Position (x, y, z) | **meters** | Typical head z range: 0.0 to 0.05 m |
| Angles (roll, pitch, yaw) | **radians** | Use `math.radians()` to convert from degrees |
| Joint positions | **radians** | Stewart platform and antenna angles |
| Body rotation | **radians** | Positive = counter-clockwise when viewed from above |
| Duration | **seconds** | Goto movement duration |
| Timestamp | **seconds** | Unix timestamp (seconds since epoch) |
| Temperature | **°C** | IMU sensor temperature |
| Acceleration | **m/s²** | IMU accelerometer |
| Angular velocity | **rad/s** | IMU gyroscope |

### Euler Angle Convention

Poses use **XYZ Euler angles** (also known as Tait-Bryan angles):

| Angle | Axis | Positive Direction | Typical Range |
|-------|------|-------------------|---------------|
| `roll` | X | Right side down | ±0.3 rad (±17°) |
| `pitch` | Y | Looking up | ±0.4 rad (±23°) |
| `yaw` | Z | Turning left | ±0.8 rad (±46°) |

**Rotation order:** Roll → Pitch → Yaw (applied in that sequence)

### Body Rotation

The `body_rotation` field controls the rotation of the entire head assembly around the vertical axis:

- **0 rad:** Robot facing forward
- **Positive:** Counter-clockwise rotation (turning left when viewed from above)
- **Negative:** Clockwise rotation (turning right)
- **Range:** Approximately ±1.0 rad (±57°)

**Note:** `body_rotation` is separate from `head_joints`. When using task-space control (`head_pose`), the IK solver can automatically adjust body rotation to reach targets outside the direct workspace.

### Antenna Positions

Antenna positions are specified as `[right_antenna, left_antenna]`:

- **0 rad:** Antennas pointing straight up
- **Positive (right):** Antenna tilts forward
- **Negative (right):** Antenna tilts backward
- **Positive (left):** Antenna tilts backward
- **Negative (left):** Antenna tilts forward
- **Range:** Approximately ±3.14 rad (full rotation)

---

## Overview

The API uses [Pydantic](https://docs.pydantic.dev/) models for validation and serialization. All models serialize to/from JSON.

**Model categories:**

| Category | Purpose |
|----------|---------|
| **Core** | Robot state and commands (`FullState`, `FullBodyTarget`, `GotoRequest`) |
| **Motor** | Motor control (`MotorControlMode`, `MotorName`) |
| **Pose** | 3D pose representations (`XYZRPYPose`, `Matrix4x4Pose`) |
| **Sensor** | Sensor data (`DoAData`, `IMUData`) |
| **Streaming** | Protocol messages for WebSocket/WebRTC |

---

## Core Models

### FullState

Complete robot state. Used by `GET /state/full` and streaming state events.

All fields are optional to support selective inclusion via query parameters.

```json
{
  "control_mode": "enabled",
  "head_pose": {"x": 0.0, "y": 0.0, "z": 0.02, "roll": 0.0, "pitch": 0.2, "yaw": 0.0},
  "target_head_pose": null,
  "head_joints": [0.52, -0.67, 0.61, -0.61, 0.67, -0.53],
  "target_head_joints": null,
  "body_rotation": 0.0,
  "target_body_rotation": null,
  "antennas": [0.0, 0.0],
  "target_antennas": null,
  "passive_joints": null,
  "timestamp": 1706789012.345,
  "sensors": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `control_mode` | `MotorControlMode` | Current motor mode |
| `head_pose` | `AnyPose` | Current head pose (task space) |
| `target_head_pose` | `AnyPose` | Target head pose |
| `head_joints` | `list[float]` | 6 stewart platform joint positions (radians) |
| `target_head_joints` | `list[float]` | Target stewart joint positions |
| `body_rotation` | `float` | Body rotation angle (radians) |
| `target_body_rotation` | `float` | Target body rotation |
| `antennas` | `tuple[float, float]` | Antenna positions `[right, left]` (radians) |
| `target_antennas` | `tuple[float, float]` | Target antenna positions |
| `passive_joints` | `list[float]` | Passive joint positions (read-only, Placo only) |
| `timestamp` | `float` | Unix timestamp (seconds since epoch) |
| `sensors` | `dict[str, SensorData]` | Sensor readings by type |

**Note:** `head_joints` contains only the 6 stewart platform motors. `body_rotation` is always separate.

---

### FullBodyTarget

Target positions for immediate or interpolated movement. Used by `POST /move/set_target` and streaming target commands.

All fields are optional - only provided fields are updated.

```json
{
  "head_pose": {"x": 0.0, "y": 0.0, "z": 0.02, "roll": 0.0, "pitch": 0.2, "yaw": 0.0},
  "antennas": [0.1, -0.1],
  "body_rotation": 0.0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `head_pose` | `AnyPose` | Target head pose (task space) |
| `head_joints` | `list[float]` | Target stewart joint positions (6 values, radians) |
| `antennas` | `tuple[float, float]` | Target antenna positions `[right, left]` (radians) |
| `body_rotation` | `float` | Target body rotation (radians) |

**Note:** Use `head_pose` OR `head_joints`, not both. If both are provided, `head_pose` takes precedence.

---

### GotoRequest

Interpolated movement request. Used by `POST /move/goto` and streaming goto commands.

```json
{
  "head_pose": {"x": 0.0, "y": 0.0, "z": 0.02, "roll": 0.0, "pitch": 0.3, "yaw": 0.0},
  "duration": 2.0,
  "interpolation": "minjerk"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `head_pose` | `AnyPose` | no* | Target head pose (task space) |
| `head_joints` | `list[float]` | no* | Target stewart joint positions (6 values) |
| `antennas` | `tuple[float, float]` | no | Target antenna positions |
| `body_rotation` | `float` | no | Target body rotation |
| `duration` | `float` | **yes** | Movement duration in seconds |
| `interpolation` | `InterpolationTechnique` | no | Interpolation method (default: `minjerk`) |

*At least one target (`head_pose`, `head_joints`, `antennas`, or `body_rotation`) must be provided.

**Interpolation techniques:**

| Value | Description |
|-------|-------------|
| `linear` | Constant velocity |
| `minjerk` | Minimum jerk (smooth start/stop) - **default** |
| `ease` | Ease in/out |
| `cartoon` | Exaggerated cartoon-style motion |

---

## Motor Models

### MotorControlMode

Enum for motor control modes.

| Value | Applies to | Description |
|-------|------------|-------------|
| `enabled` | All motors | Torque ON, position control active |
| `disabled` | All motors | Torque OFF, robot is compliant |
| `gravity_compensation` | Stewart only | Torque ON, current control for gravity compensation |

**Note:** `gravity_compensation` only applies to the 6 stewart platform motors as a unit (they work together for parallel kinematics). It cannot be applied to individual motors, antennas, or body_rotation.

```json
"enabled"
```

---

### MotorName

Enum for motor names. The robot has 9 motors:

| Value | Description |
|-------|-------------|
| `body_rotation` | Rotates the entire head assembly |
| `stewart_1` | Stewart platform actuator 1 |
| `stewart_2` | Stewart platform actuator 2 |
| `stewart_3` | Stewart platform actuator 3 |
| `stewart_4` | Stewart platform actuator 4 |
| `stewart_5` | Stewart platform actuator 5 |
| `stewart_6` | Stewart platform actuator 6 |
| `right_antenna` | Right antenna motor |
| `left_antenna` | Left antenna motor |

**Motor groups:**

- **Stewart platform:** `stewart_1` through `stewart_6` (6 linear actuators controlling head pose)
- **Body:** `body_rotation` (rotates the head assembly)
- **Antennas:** `right_antenna`, `left_antenna`

---

### MotorControlCommand

Command to change motor control mode.

```json
{
  "mode": "enabled",
  "motor_names": ["stewart_1", "stewart_2"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `mode` | `MotorControlMode` | Target motor mode |
| `motor_names` | `list[MotorName]` | Optional: specific motors to target (not yet implemented) |

**Validation:**
- `gravity_compensation` mode cannot specify `motor_names` - it always applies globally to the stewart platform only
- `enabled`/`disabled` can optionally target specific motors via `motor_names`

---

## Pose Models

Poses can be represented in two formats. The API accepts either format and converts internally.

### XYZRPYPose (Default)

Position and Euler angles representation.

```json
{
  "x": 0.0,
  "y": 0.0,
  "z": 0.02,
  "roll": 0.0,
  "pitch": 0.2,
  "yaw": 0.0
}
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `x` | `float` | meters | X position |
| `y` | `float` | meters | Y position |
| `z` | `float` | meters | Z position (typically 0.0 to 0.05) |
| `roll` | `float` | radians | Rotation around X axis |
| `pitch` | `float` | radians | Rotation around Y axis (tilt up/down) |
| `yaw` | `float` | radians | Rotation around Z axis (turn left/right) |

**Typical ranges:**

- `z`: 0.0 to 0.05 m (head height adjustment)
- `roll`: ±0.3 rad (head tilt side-to-side)
- `pitch`: ±0.4 rad (look up/down)
- `yaw`: ±0.8 rad (turn left/right)

---

### Matrix4x4Pose

4x4 transformation matrix representation. Use `?use_pose_matrix=true` to request this format.

```json
{
  "m": [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.02,
    0.0, 0.0, 0.0, 1.0
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `m` | `tuple[16 floats]` | Row-major 4x4 matrix (rotation + translation) |

Matrix layout:
```
| R R R Tx |
| R R R Ty |
| R R R Tz |
| 0 0 0 1  |
```

---

### AnyPose

Union type that accepts either `XYZRPYPose` or `Matrix4x4Pose`.

```python
AnyPose = XYZRPYPose | Matrix4x4Pose
```

The API automatically detects which format is provided based on the fields present.

---

## Sensor Models

Sensors use a base class pattern for extensibility. Each sensor type has a `sensor_type` discriminator field.

### SensorData (Base)

Base class for all sensor data.

| Field | Type | Description |
|-------|------|-------------|
| `sensor_type` | `str` | Sensor type identifier |
| `timestamp` | `float` | Optional timestamp |

---

### DoAData

Direction of Arrival from the microphone array.

```json
{
  "sensor_type": "doa",
  "angle": 1.57,
  "speech_detected": true,
  "timestamp": 1706789012.345
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sensor_type` | `"doa"` | Always `"doa"` |
| `angle` | `float` | Angle in radians (0=left, π/2=front, π=right) |
| `speech_detected` | `bool` | Whether speech was detected |
| `timestamp` | `float` | Optional timestamp |

---

### IMUData

Inertial Measurement Unit data.

```json
{
  "sensor_type": "imu",
  "accelerometer": [0.01, 0.02, 9.81],
  "gyroscope": [0.001, 0.002, 0.0],
  "quaternion": [1.0, 0.0, 0.0, 0.0],
  "temperature": 42.5
}
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `sensor_type` | `"imu"` | - | Always `"imu"` |
| `accelerometer` | `tuple[float, float, float]` | m/s² | Linear acceleration (x, y, z) |
| `gyroscope` | `tuple[float, float, float]` | rad/s | Angular velocity (x, y, z) |
| `quaternion` | `tuple[float, float, float, float]` | - | Orientation (w, x, y, z) |
| `temperature` | `float` | °C | Sensor temperature |

---

## Streaming Protocol Models

These models wrap the core models for the streaming protocol. Commands go client→server, events go server→client.

### Commands (Client → Server)

All commands have a `cmd` field that identifies the command type.

#### TargetCommand

Set immediate target position (fire-and-forget).

```json
{
  "cmd": "target",
  "target": {"head_pose": {"x": 0, "y": 0, "z": 0.02, "pitch": 0.2}}
}
```

#### GotoCommand

Start interpolated movement.

```json
{
  "cmd": "goto",
  "request": {"head_pose": {...}, "duration": 2.0},
  "id": "move-123"
}
```

- With `id`: async mode (returns `goto_started` immediately, then `goto_done`)
- Without `id`: blocking mode (server generates ID internally, returns `goto_done` when complete)

**ID uniqueness:** The `id` must be unique among in-progress moves. Reusing an ID that's currently in progress returns an `ErrorEvent` with code `DUPLICATE_MOVE_ID`. IDs can be reused after the move completes.

#### SetModeCommand

Change motor control mode.

```json
{
  "cmd": "set_mode",
  "mode": "enabled"
}
```

#### CancelCommand

Cancel an async goto.

```json
{
  "cmd": "cancel",
  "id": "move-123"
}
```

#### SubscribeCommand

Configure state streaming.

```json
{
  "cmd": "subscribe",
  "fields": ["head_pose", "antennas"],
  "sensors": ["doa"],
  "frequency": 50
}
```

#### GetStatusCommand

Request daemon status.

```json
{
  "cmd": "get_status"
}
```

---

### Events (Server → Client)

All events have an `event` field that identifies the event type.

#### StateEvent

Continuous state updates (after subscribe).

```json
{
  "event": "state",
  "state": {...}
}
```

#### GotoStartedEvent

Async goto has started.

```json
{
  "event": "goto_started",
  "id": "move-123"
}
```

#### GotoDoneEvent

Goto completed.

```json
{
  "event": "goto_done",
  "status": "completed",
  "id": "move-123"
}
```

#### ModeChangedEvent

Motor mode changed.

```json
{
  "event": "mode_changed",
  "mode": "enabled"
}
```

#### CancelledEvent

Goto was cancelled.

```json
{
  "event": "cancelled",
  "id": "move-123"
}
```

#### ErrorEvent

An error occurred.

```json
{
  "event": "error",
  "message": "Motor controller not ready",
  "code": "MOTOR_NOT_READY"
}
```

#### StatusEvent

Daemon status response.

```json
{
  "event": "status",
  "motor_ready": true,
  "control_mode": "enabled",
  "available_sensors": ["doa", "imu"]
}
```

---

### MoveStatus

Enum for movement status in `GotoDoneEvent`.

| Value | Description |
|-------|-------------|
| `in_progress` | Movement is executing |
| `completed` | Movement finished successfully |
| `failed` | Movement failed (motor error, etc.) |
| `cancelled` | Movement was cancelled |
| `not_found` | Unknown move ID |

---

## Type Aliases

| Alias | Type | Description |
|-------|------|-------------|
| `MoveId` | `str` | UUID string identifying a movement |
| `AnyPose` | `XYZRPYPose \| Matrix4x4Pose` | Either pose format |

---

## Adding New Sensors

To add a new sensor type:

1. Create a subclass of `SensorData` in `daemon/models/sensors.py`:

```python
class MyNewSensor(SensorData):
    sensor_type: Literal["my_sensor"] = "my_sensor"
    value: float
    # ... other fields
```

2. Register a sensor provider in the daemon (implementation pending)

3. Request the sensor in API calls:
   - HTTP: `?sensors=my_sensor`
   - Streaming: `{"cmd": "subscribe", "sensors": ["my_sensor"]}`

---

## JSON Schema Export

Export JSON schemas for use in other implementations:

```python
from reachy_mini.daemon.models import FullState, FullBodyTarget, GotoRequest

# Get JSON schema for any model
schema = FullState.model_json_schema()

# Export to file
import json
with open("schemas.json", "w") as f:
    json.dump({
        "FullState": FullState.model_json_schema(),
        "FullBodyTarget": FullBodyTarget.model_json_schema(),
        "GotoRequest": GotoRequest.model_json_schema(),
    }, f, indent=2)
```
