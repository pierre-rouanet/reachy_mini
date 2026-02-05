# HTTP API Reference

The HTTP API provides RESTful endpoints for controlling Reachy Mini. Use this for simple integrations, scripting, or when real-time streaming isn't required.

Base URL: `http://<host>:8000/api`

---

## State

### Get Full State

Retrieve the current robot state.

```
GET /state/full
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `with_control_mode` | bool | true | Include motor control mode |
| `with_head_pose` | bool | true | Include current head pose |
| `with_target_head_pose` | bool | false | Include target head pose |
| `with_head_joints` | bool | false | Include head joint positions |
| `with_target_head_joints` | bool | false | Include target joint positions |
| `with_antennas` | bool | true | Include antenna positions |
| `with_target_antennas` | bool | false | Include target antenna positions |
| `with_body_rotation` | bool | true | Include body rotation angle |
| `with_target_body_rotation` | bool | false | Include target body rotation |
| `sensors` | string | null | Comma-separated sensor types, or `all` |
| `use_pose_matrix` | bool | false | Use 4x4 matrix instead of XYZRPY |

**Response:** `FullState`

```json
{
  "control_mode": "enabled",
  "head_pose": [0.0, 0.0, 0.38, 0.0, 0.2, 0.0],
  "antennas": [0.0, 0.0],
  "body_rotation": 0.0,
  "timestamp": 1706789012.345,
  "sensors": {}
}
```

**Examples:**

```bash
# Basic state
curl http://localhost:8000/api/state/full

# With joint positions
curl "http://localhost:8000/api/state/full?with_head_joints=true"

# With specific sensors
curl "http://localhost:8000/api/state/full?sensors=doa,imu"

# With all sensors
curl "http://localhost:8000/api/state/full?sensors=all"
```

**Response with sensors:**

```json
{
  "control_mode": "enabled",
  "head_pose": [0.0, 0.0, 0.38, 0.0, 0.2, 0.0],
  "antennas": [0.0, 0.0],
  "body_rotation": 0.0,
  "timestamp": 1706789012.345,
  "sensors": {
    "doa": {
      "sensor_type": "doa",
      "angle": 1.2,
      "speech_detected": true
    },
    "imu": {
      "sensor_type": "imu",
      "accelerometer": [0.01, 0.02, 9.81],
      "gyroscope": [0.001, 0.002, 0.0],
      "quaternion": [1.0, 0.0, 0.0, 0.0],
      "temperature": 42.5
    }
  }
}
```

---

## Sensors

### List Available Sensors

Get list of available sensor types.

```
GET /state/sensors
```

**Response:**

```json
{
  "available": ["doa", "imu"]
}
```

### Get Specific Sensor

Read data from a specific sensor.

```
GET /state/sensors/{sensor_type}
```

**Path Parameters:**

| Parameter | Description |
|-----------|-------------|
| `sensor_type` | Sensor identifier (e.g., `doa`, `imu`) |

**Response:** `SensorData` (specific type depends on sensor)

```json
{
  "sensor_type": "imu",
  "accelerometer": [0.01, 0.02, 9.81],
  "gyroscope": [0.001, 0.002, 0.0],
  "quaternion": [1.0, 0.0, 0.0, 0.0],
  "temperature": 42.5
}
```

**Example:**

```bash
curl http://localhost:8000/api/state/sensors/doa
```

**Error (sensor not available):**

```json
{
  "detail": "Sensor 'imu' not available"
}
```

---

## Daemon Status

### Get Status

Get overall daemon and motor controller status.

```
GET /daemon/status
```

**Response:**

```json
{
  "motor_ready": true,
  "control_mode": "enabled",
  "available_sensors": ["doa", "imu"]
}
```

---

## Motor Control

### Get Motor Status

```
GET /motors/status
```

**Response:** `MotorStatus`

```json
{
  "control_mode": "enabled",
  "torque_enabled": true
}
```

### Set Motor Mode

```
POST /motors/set_mode/{mode}
```

**Path Parameters:**

| Parameter | Values |
|-----------|--------|
| `mode` | `enabled`, `disabled`, `gravity_compensation` |

**Response:** `ModeChangedEvent`

```json
{
  "event": "mode_changed",
  "mode": "enabled"
}
```

**Example:**

```bash
curl -X POST http://localhost:8000/api/motors/set_mode/enabled
```

---

## Immediate Targets

### Set Target

Set an immediate target position. The robot moves toward this target as fast as possible (no interpolation).

```
POST /move/set_target
```

**Request Body:** `FullBodyTarget`

```json
{
  "head_pose": [0.0, 0.0, 0.4, 0.0, 0.3, 0.0],
  "antennas": [0.1, -0.1],
  "body_rotation": 0.0
}
```

All fields are optional. Only provided fields are updated.

**Response:**

```json
{"ok": true}
```

**Notes:**
- Use `head_pose` (XYZRPY) OR `head_joints`, not both
- If both provided, `head_pose` takes precedence

**Example:**

```bash
curl -X POST http://localhost:8000/api/move/set_target \
  -H "Content-Type: application/json" \
  -d '{"head_pose": [0, 0, 0.4, 0, 0.3, 0]}'
```

---

## Goto (Interpolated Movement)

### Start Goto (Async)

Start an interpolated movement. Returns immediately with a move ID.

```
POST /move/goto
```

**Request Body:** `GotoRequest`

```json
{
  "head_pose": [0.0, 0.0, 0.4, 0.0, 0.3, 0.0],
  "duration": 2.0,
  "interpolation": "linear"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `head_pose` | list[float] | no* | Target pose [x, y, z, roll, pitch, yaw] |
| `head_joints` | list[float] | no* | Target joint positions |
| `antennas` | tuple[float, float] | no | Target antenna positions |
| `body_rotation` | float | no | Target body rotation |
| `duration` | float | yes | Movement duration in seconds |
| `interpolation` | string | no | `"linear"` (default) or `"minimum_jerk"` |

*At least one target (head_pose, head_joints, antennas, or body_rotation) must be provided.

**Response:** `GotoStartedEvent`

```json
{
  "event": "goto_started",
  "id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Start Goto (Blocking)

Wait for the movement to complete before returning.

```
POST /move/goto?wait=true
```

**Request Body:** Same as async

**Response:** `GotoDoneEvent`

```json
{
  "event": "goto_done",
  "status": "completed"
}
```

| Status | Description |
|--------|-------------|
| `completed` | Movement finished successfully |
| `failed` | Movement failed (motor error, etc.) |
| `cancelled` | Movement was cancelled |

**Example (blocking):**

```bash
curl -X POST "http://localhost:8000/api/move/goto?wait=true" \
  -H "Content-Type: application/json" \
  -d '{"head_pose": [0, 0, 0.4, 0, 0.3, 0], "duration": 2.0}'
```

### Get Goto Status

Poll the status of an async goto.

```
GET /move/goto/{move_id}
```

**Response:** `GotoDoneEvent`

```json
{
  "event": "goto_done",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "in_progress"
}
```

| Status | Description |
|--------|-------------|
| `in_progress` | Movement is still executing |
| `completed` | Movement finished successfully |
| `failed` | Movement failed |
| `cancelled` | Movement was cancelled |
| `not_found` | Unknown move ID |

### Cancel Goto

Cancel a running goto movement.

```
POST /move/goto/{move_id}/cancel
```

**Response:** `GotoDoneEvent`

```json
{
  "event": "goto_done",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled"
}
```

---

## Complete Examples

### Simple Script: Look Around

```python
import requests

BASE = "http://localhost:8000/api"

# Enable motors
requests.post(f"{BASE}/motors/set_mode/enabled")

# Look left (blocking)
requests.post(
    f"{BASE}/move/goto?wait=true",
    json={"head_pose": [0, 0, 0.4, 0, 0, 0.5], "duration": 1.0}
)

# Look right (blocking)
requests.post(
    f"{BASE}/move/goto?wait=true",
    json={"head_pose": [0, 0, 0.4, 0, 0, -0.5], "duration": 1.0}
)

# Look center (blocking)
requests.post(
    f"{BASE}/move/goto?wait=true",
    json={"head_pose": [0, 0, 0.4, 0, 0, 0], "duration": 1.0}
)

# Disable motors
requests.post(f"{BASE}/motors/set_mode/disabled")
```

### Async Goto with Polling

```python
import requests
import time

BASE = "http://localhost:8000/api"

# Start movement
response = requests.post(
    f"{BASE}/move/goto",
    json={"head_pose": [0, 0, 0.4, 0, 0.3, 0], "duration": 3.0}
)
move_id = response.json()["id"]

# Poll until complete
while True:
    status = requests.get(f"{BASE}/move/goto/{move_id}").json()
    print(f"Status: {status['status']}")

    if status["status"] in ("completed", "failed", "cancelled"):
        break

    time.sleep(0.1)
```

### Concurrent Movements with Cancel

```python
import requests
import time

BASE = "http://localhost:8000/api"

# Start a long movement
response = requests.post(
    f"{BASE}/move/goto",
    json={"head_pose": [0, 0, 0.4, 0, 0.5, 0], "duration": 5.0}
)
move_id = response.json()["id"]

# Wait a bit then cancel
time.sleep(1.0)
requests.post(f"{BASE}/move/goto/{move_id}/cancel")
print("Movement cancelled")
```

### Reading Sensors

```python
import requests

BASE = "http://localhost:8000/api"

# List available sensors
sensors = requests.get(f"{BASE}/state/sensors").json()
print(f"Available: {sensors['available']}")

# Get specific sensor
if "imu" in sensors["available"]:
    imu = requests.get(f"{BASE}/state/sensors/imu").json()
    print(f"Acceleration: {imu['accelerometer']}")
    print(f"Temperature: {imu['temperature']}")

# Get state with all sensors
state = requests.get(f"{BASE}/state/full?sensors=all").json()
for name, data in state["sensors"].items():
    print(f"{name}: {data}")
```

---

## Error Handling

All endpoints return standard HTTP status codes:

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request (invalid parameters) |
| 404 | Resource not found (unknown move ID, unknown sensor) |
| 409 | Conflict (e.g., goto while motion already running) |
| 500 | Internal server error |

Error response format:

```json
{
  "detail": "Motor controller not ready"
}
```

---

## Data Models Reference

See [MODELS.md](MODELS.md) for complete documentation of all data models.

**Quick reference:**
- `FullState` - Complete robot state
- `FullBodyTarget` - Immediate target command
- `GotoRequest` - Interpolated movement request
- `MotorControlMode` - Motor mode enum (`enabled`, `disabled`, `gravity_compensation`)
- `MotorName` - Motor names (`body_rotation`, `stewart_1`-`stewart_6`, antennas)
- `AnyPose` - Pose as XYZRPY or 4x4 matrix

These models are shared between HTTP and Streaming APIs.

---

## Comparison: HTTP vs Streaming

| Operation | HTTP | Streaming |
|-----------|------|-----------|
| Get state once | `GET /state/full` | `subscribe` + receive one `state` |
| Continuous state | Poll (inefficient) | `subscribe` at desired frequency |
| Set target | `POST /move/set_target` | `{"cmd": "target", ...}` |
| Goto blocking | `POST /move/goto?wait=true` | `{"cmd": "goto", ...}` (no id) |
| Goto async | `POST /move/goto` + poll status | `{"cmd": "goto", ..., "id": ...}` |
| Cancel goto | `POST /move/goto/{id}/cancel` | `{"cmd": "cancel", "id": ...}` |
| Set motor mode | `POST /motors/set_mode/{mode}` | `{"cmd": "set_mode", ...}` |
| Get sensors | `GET /state/sensors/{type}` | Include in `subscribe` |

**Use HTTP when:**
- Simple scripts with occasional commands
- Integration with REST-based systems
- Debugging/testing individual endpoints

**Use Streaming when:**
- Real-time control or monitoring
- High-frequency state updates needed
- Bidirectional communication (targets + state)
- Performance matters (Python SDK)

See [STREAMING_API.md](STREAMING_API.md) for the streaming protocol reference.
