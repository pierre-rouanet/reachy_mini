# Streaming API Reference

The Streaming API provides real-time bidirectional communication with Reachy Mini over WebSocket or WebRTC data channels. Use this for teleoperation, real-time monitoring, or any application requiring low-latency communication.

**Recommended for:** Python SDK, teleoperation apps, real-time control

## Connection

### WebSocket

```
ws://<host>:8000/api/stream/ws
```

### WebRTC Data Channel

Connect via WebRTC signaling, then use the `control` data channel.

## Protocol Overview

All messages are JSON objects with either:
- `cmd` field - Client → Server (commands)
- `event` field - Server → Client (events/responses)

The connection is fully multiplexed:
- State streams continuously from server
- Commands can be sent at any time
- Events (goto completion, errors) arrive asynchronously

```
Client                              Server
   |                                   |
   |-- {"cmd": "subscribe", ...} ---->|
   |<-- {"event": "state", ...} ------|  (continuous)
   |<-- {"event": "state", ...} ------|
   |-- {"cmd": "target", ...} ------->|
   |<-- {"event": "state", ...} ------|
   |-- {"cmd": "goto", ...} --------->|
   |<-- {"event": "goto_done", ...} --|  (after duration)
   |<-- {"event": "state", ...} ------|
```

---

## Commands (Client → Server)

### Subscribe

Configure state streaming. Must be sent before receiving state events.

```json
{
  "cmd": "subscribe",
  "fields": ["head_pose", "antennas", "body_rotation"],
  "sensors": ["doa", "imu"],
  "frequency": 50
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fields` | list[str] \| null | null (all) | Motor state fields to include |
| `sensors` | list[str] \| null | null (none) | Sensor types to include |
| `frequency` | float | 50 | Update frequency in Hz (max 100) |

**Available fields:**
- `control_mode` - Motor control mode
- `head_pose` - Current head pose [x, y, z, roll, pitch, yaw]
- `target_head_pose` - Target head pose
- `head_joints` - Current joint positions
- `target_head_joints` - Target joint positions
- `antennas` - Current antenna positions
- `target_antennas` - Target antenna positions
- `body_rotation` - Current body yaw angle
- `target_body_rotation` - Target body yaw

**Available sensors:**
- `doa` - Direction of arrival (microphone array)
- `imu` - Inertial measurement unit

**Response:** Server begins streaming `state` events.

---

### Target

Set immediate target position. No interpolation - robot moves as fast as possible.

```json
{
  "cmd": "target",
  "target": {
    "head_pose": [0.0, 0.0, 0.4, 0.0, 0.3, 0.0],
    "antennas": [0.1, -0.1],
    "body_rotation": 0.0
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `target` | FullBodyTarget | Target positions (all fields optional) |

The `target` object supports:
- `head_pose` - [x, y, z, roll, pitch, yaw] in task space
- `head_joints` - Joint positions (alternative to head_pose)
- `antennas` - [left, right] antenna angles
- `body_rotation` - Body rotation angle

**Note:** Use `head_pose` OR `head_joints`, not both. If both provided, `head_pose` takes precedence.

**Response:** None (fire-and-forget for performance).

---

### Goto (Blocking)

Interpolated movement. Server responds when movement completes.

```json
{
  "cmd": "goto",
  "request": {
    "head_pose": [0.0, 0.0, 0.4, 0.0, 0.3, 0.0],
    "duration": 2.0,
    "interpolation": "minimum_jerk"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `request` | GotoRequest | Movement parameters |

The `request` object:
- `head_pose` - Target pose (or `head_joints`)
- `antennas` - Target antenna positions
- `body_rotation` - Target body yaw
- `duration` - Movement duration in seconds (required)
- `interpolation` - `"linear"` or `"minimum_jerk"` (default: linear)

**Response:** `goto_done` event after movement completes.

```json
{"event": "goto_done", "status": "completed"}
```

**Note:** State events continue streaming during the movement.

---

### Goto (Async)

Non-blocking goto. Provide an `id` to receive immediate confirmation and completion event later.

```json
{
  "cmd": "goto",
  "request": {
    "head_pose": [0.0, 0.0, 0.4, 0.0, 0.3, 0.0],
    "duration": 2.0
  },
  "id": "move-123"
}
```

**Immediate response:**

```json
{"event": "goto_started", "id": "move-123"}
```

**On completion:**

```json
{"event": "goto_done", "id": "move-123", "status": "completed"}
```

**ID uniqueness:** The `id` must be unique among in-progress moves. Reusing an active ID returns:

```json
{"event": "error", "message": "Move ID 'move-123' is already in use", "code": "DUPLICATE_MOVE_ID"}
```

IDs can be reused after the move completes.

---

### Set Mode

Change motor control mode.

```json
{
  "cmd": "set_mode",
  "mode": "enabled"
}
```

| Mode | Description |
|------|-------------|
| `enabled` | Motors active, following targets |
| `disabled` | Motors off, robot limp |
| `gravity_compensation` | Motors compensate for gravity only |

**Response:**

```json
{"event": "mode_changed", "mode": "enabled"}
```

---

### Cancel

Cancel an async goto movement.

```json
{
  "cmd": "cancel",
  "id": "move-123"
}
```

**Response:**

```json
{"event": "cancelled", "id": "move-123"}
```

The corresponding goto will receive:

```json
{"event": "goto_done", "id": "move-123", "status": "cancelled"}
```

---

### Get Status

Request daemon/motor status (one-time, not streaming).

```json
{
  "cmd": "get_status"
}
```

**Response:**

```json
{
  "event": "status",
  "motor_ready": true,
  "control_mode": "enabled",
  "available_sensors": ["doa", "imu"],
  "automatic_body_rotation": true
}
```

---

### Set Automatic Body Rotation

Enable or disable automatic body rotation during IK.

```json
{
  "cmd": "set_automatic_body_rotation",
  "enabled": true
}
```

**Response:**

```json
{"event": "automatic_body_rotation_changed", "enabled": true}
```

---

### Get Daemon Status

Request full daemon status including state, version, and motor controller status.

```json
{
  "cmd": "get_daemon_status"
}
```

**Response:**

```json
{
  "event": "daemon_status",
  "robot_name": "reachy_mini",
  "state": "running",
  "wireless_version": true,
  "desktop_app_daemon": false,
  "simulation_enabled": false,
  "mockup_sim_enabled": false,
  "motor_controller_status": {
    "motor_control_mode": "enabled",
    "error": null,
    "ready": true,
    "control_loop_stats": {},
    "automatic_body_yaw": true
  },
  "error": null,
  "wlan_ip": "192.168.1.100",
  "version": "1.2.13"
}
```

---

## Events (Server → Client)

### State

Continuous robot state updates (after subscribe).

```json
{
  "event": "state",
  "state": {
    "control_mode": "enabled",
    "head_pose": [0.0, 0.0, 0.38, 0.0, 0.2, 0.1],
    "antennas": [0.05, -0.05],
    "body_rotation": 0.0,
    "timestamp": 1706789012.345,
    "sensors": {
      "doa": {
        "sensor_type": "doa",
        "angle": 1.2,
        "speech_detected": true
      }
    }
  }
}
```

Only requested fields and sensors are included.

---

### Goto Started

Confirmation that an async goto has started.

```json
{
  "event": "goto_started",
  "id": "move-123"
}
```

---

### Goto Done

Movement completed (blocking or async).

```json
{
  "event": "goto_done",
  "status": "completed",
  "id": "move-123"
}
```

| Status | Description |
|--------|-------------|
| `completed` | Movement finished successfully |
| `failed` | Movement failed (motor error, collision, etc.) |
| `cancelled` | Movement was cancelled via cancel command |

For blocking goto, `id` is omitted.

---

### Mode Changed

Motor mode changed successfully.

```json
{
  "event": "mode_changed",
  "mode": "enabled"
}
```

---

### Cancelled

Async goto was cancelled.

```json
{
  "event": "cancelled",
  "id": "move-123"
}
```

---

### Error

An error occurred.

```json
{
  "event": "error",
  "message": "Motor controller not ready",
  "code": "MOTOR_NOT_READY"
}
```

| Code | Description |
|------|-------------|
| `MOTOR_NOT_READY` | Motor controller not initialized |
| `INVALID_COMMAND` | Unknown or malformed command |
| `INVALID_TARGET` | Target out of range or conflicting |
| `GOTO_CONFLICT` | Goto already in progress (for blocking) |
| `DUPLICATE_MOVE_ID` | Async goto with an ID already in use |
| `UNKNOWN_MOVE_ID` | Cancel for unknown move ID |

---

## Complete Examples

### Python: Simple Teleoperation

```python
import asyncio
import json
import websockets

async def teleoperate():
    async with websockets.connect("ws://localhost:8000/api/stream/ws") as ws:
        # Enable motors
        await ws.send(json.dumps({"cmd": "set_mode", "mode": "enabled"}))
        response = json.loads(await ws.recv())
        assert response["event"] == "mode_changed"

        # Subscribe to state
        await ws.send(json.dumps({
            "cmd": "subscribe",
            "fields": ["head_pose"],
            "frequency": 30
        }))

        # Control loop
        async def send_targets():
            while True:
                target = get_joystick_input()  # Your input source
                await ws.send(json.dumps({
                    "cmd": "target",
                    "target": {"head_pose": target}
                }))
                await asyncio.sleep(0.02)  # 50Hz

        async def receive_state():
            while True:
                msg = json.loads(await ws.recv())
                if msg["event"] == "state":
                    update_display(msg["state"])  # Your visualization

        await asyncio.gather(send_targets(), receive_state())

asyncio.run(teleoperate())
```

### Python: Sequential Movements (Blocking Goto)

```python
import asyncio
import json
import websockets

async def dance():
    async with websockets.connect("ws://localhost:8000/api/stream/ws") as ws:
        # Enable motors
        await ws.send(json.dumps({"cmd": "set_mode", "mode": "enabled"}))
        await ws.recv()  # mode_changed

        # Helper for blocking goto
        async def goto(pose, duration):
            await ws.send(json.dumps({
                "cmd": "goto",
                "request": {"head_pose": pose, "duration": duration}
            }))
            # Wait for goto_done (ignore state events)
            while True:
                msg = json.loads(await ws.recv())
                if msg["event"] == "goto_done":
                    return msg["status"]

        # Dance sequence
        await goto([0, 0, 0.4, 0, 0, 0.5], 1.0)   # Look left
        await goto([0, 0, 0.4, 0, 0, -0.5], 1.0)  # Look right
        await goto([0, 0, 0.4, 0, 0.3, 0], 1.0)   # Look down
        await goto([0, 0, 0.4, 0, -0.2, 0], 1.0)  # Look up
        await goto([0, 0, 0.4, 0, 0, 0], 1.0)     # Center

        # Disable motors
        await ws.send(json.dumps({"cmd": "set_mode", "mode": "disabled"}))

asyncio.run(dance())
```

### Python: Concurrent Head + Antenna Animation

```python
import asyncio
import json
import math
import websockets

async def animate():
    async with websockets.connect("ws://localhost:8000/api/stream/ws") as ws:
        # Enable and subscribe
        await ws.send(json.dumps({"cmd": "set_mode", "mode": "enabled"}))
        await ws.recv()

        await ws.send(json.dumps({
            "cmd": "subscribe",
            "fields": ["head_pose", "antennas"],
            "frequency": 30
        }))

        # Start async head movement
        await ws.send(json.dumps({
            "cmd": "goto",
            "request": {"head_pose": [0, 0, 0.4, 0, 0.3, 0], "duration": 3.0},
            "id": "head-move"
        }))

        # Animate antennas during head movement
        start_time = asyncio.get_event_loop().time()
        head_done = False

        while not head_done:
            msg = json.loads(await ws.recv())

            if msg["event"] == "state":
                # Continuous antenna animation
                t = asyncio.get_event_loop().time() - start_time
                angle = math.sin(t * 5) * 0.3
                await ws.send(json.dumps({
                    "cmd": "target",
                    "target": {"antennas": [angle, -angle]}
                }))

            elif msg["event"] == "goto_done" and msg.get("id") == "head-move":
                head_done = True
                print(f"Head movement: {msg['status']}")

asyncio.run(animate())
```

### JavaScript: Browser Client

```javascript
const ws = new WebSocket("ws://localhost:8000/api/stream/ws");

ws.onopen = () => {
    // Enable motors
    ws.send(JSON.stringify({cmd: "set_mode", mode: "enabled"}));
};

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    switch (msg.event) {
        case "mode_changed":
            // Subscribe after motors enabled
            ws.send(JSON.stringify({
                cmd: "subscribe",
                fields: ["head_pose", "antennas"],
                frequency: 30
            }));
            break;

        case "state":
            // Update UI
            document.getElementById("pose").textContent =
                JSON.stringify(msg.state.head_pose);
            break;

        case "goto_done":
            console.log(`Move ${msg.id || ""}:`, msg.status);
            break;

        case "error":
            console.error(msg.message);
            break;
    }
};

// Send target on mouse move
document.addEventListener("mousemove", (e) => {
    const yaw = (e.clientX / window.innerWidth - 0.5) * 1.0;
    const pitch = (e.clientY / window.innerHeight - 0.5) * 0.5;

    ws.send(JSON.stringify({
        cmd: "target",
        target: {head_pose: [0, 0, 0.4, 0, pitch, yaw]}
    }));
});

// Goto on click
document.addEventListener("click", () => {
    ws.send(JSON.stringify({
        cmd: "goto",
        request: {head_pose: [0, 0, 0.4, 0, 0, 0], duration: 1.0},
        id: "click-" + Date.now()
    }));
});
```

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

**Use HTTP when:**
- Simple scripts with occasional commands
- Integration with REST-based systems
- Debugging/testing individual endpoints

**Use Streaming when:**
- Real-time control or monitoring
- High-frequency state updates needed
- Bidirectional communication (targets + state)
- Performance matters (Python SDK)

---

## Message Schema Reference

See [MODELS.md](MODELS.md) for complete documentation of all data models.

**Core models (shared with HTTP API):**
- `FullState` - Robot state
- `FullBodyTarget` - Target positions
- `GotoRequest` - Goto parameters
- `MotorControlMode` - Motor mode enum
- `MotorName` - Motor names
- `AnyPose` - Pose representations

**Streaming-specific wrappers:**
- Commands: `TargetCommand`, `GotoCommand`, `SetModeCommand`, `CancelCommand`, `SubscribeCommand`, `GetStatusCommand`
- Events: `StateEvent`, `GotoStartedEvent`, `GotoDoneEvent`, `ModeChangedEvent`, `CancelledEvent`, `ErrorEvent`, `StatusEvent`

**Export JSON Schema:**
```python
from reachy_mini.daemon.models import FullState, FullBodyTarget, GotoRequest
import json

schemas = {
    "FullState": FullState.model_json_schema(),
    "FullBodyTarget": FullBodyTarget.model_json_schema(),
    "GotoRequest": GotoRequest.model_json_schema(),
}
print(json.dumps(schemas, indent=2))
```
