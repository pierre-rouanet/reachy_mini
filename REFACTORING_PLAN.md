# Refactoring Plan

This document tracks the ongoing refactoring effort to make the Reachy Mini codebase cleaner, simpler, and easier to test and maintain.

## Goals

1. **Clean up the motor controller abstraction** - Currently tangled with Zenoh. Only truly controller-specific logic should remain in the concrete implementations (robot/mujoco/mockup).

2. **Clean up and extend FastAPI HTTP/WS interface** - Make it the primary interface with:
   - Better data model definitions
   - Missing routes added
   - Interface abstraction for clean daemon integration
   - Support for new interface types (e.g., WebRTC via GStreamer)

3. **Simplify daemon/motor controller/API interaction** - The daemon should be the main abstraction that integrates motor control and interfaces. (Details to be refined.)

4. **Preserve SDK client compatibility** - The `ReachyMini` class APIs in `reachy_mini.py` must remain stable. Motor controller implementations can change freely as long as exposed interfaces are preserved.

## Constraints

- **Testing/Linting**: Use existing CI, examples, and tests as validation. They may break during refactoring but should always be the target to restore.
- **Ask questions** when uncertain about design decisions.

## Priority Order

1. ~~**Motor controller abstraction cleanup**~~ ✓ Done
2. ~~**Daemon cleanup**~~ ✓ Done (interfaces removed, to be re-added with abstraction)
3. ~~**Daemon architecture refactoring**~~ ✓ Done (MotorManager, ApiManager, WebRTCManager split)
4. **Shared data models** (next focus) - ApiManager & WebRTCManager share motor + media data models
5. FastAPI interface cleanup & data models
6. WebRTC motor data streaming

## Status

| Area | Status | Notes |
|------|--------|-------|
| Motor controller abstraction cleanup | Done | Zenoh removed, template method pattern, unified status |
| Daemon cleanup | Done | Removed ZenohServer, WebSocket, media streaming from daemon |
| IO module cleanup | Done | Removed deprecated websocket files (audio_ws, video_ws, ws_controller) |
| Daemon architecture | Done | Split into MotorManager + ApiManager + WebRTCManager + MotionManager |
| Shared data models | Not started | ApiManager & WebRTCManager share motor + media models |
| FastAPI interface cleanup | Not started | |
| Data model definitions | Not started | |
| WebRTC motor data streaming | Not started | Stream motor state/commands via WebRTC |
| SDK compatibility verification | Blocked | ReachyMini client needs ZenohServer - will need new interface |

## Current Architecture

```
Daemon (orchestrator)
├── MotorManager (motor control lifecycle)
│   └── MotorController (MuJoCo/Robot/Mockup)
├── ApiManager (FastAPI HTTP/WS server)
├── WebRTCManager (real-time streaming: video, audio, motor data)
├── MotionManager (motion with synchronized audio)
├── _audio_manager (local sound playback via MediaManager)
└── AppManager (user app lifecycle)
```

**Key insight**: WebRTC carries both media streams AND motor data, making it a real-time transport layer rather than purely a media component. ApiManager and WebRTCManager should share the same motor data models to ensure consistency.

## Progress Log

### Step 1: Remove Zenoh from backend (DONE)
Goal: Clean up the backend abstraction by removing all Zenoh coupling.

Changes made:
- `src/reachy_mini/daemon/backend/abstract.py`:
  - Removed `zenoh` import
  - Removed publisher attributes (`joint_positions_publisher`, `pose_publisher`, `recording_publisher`, `imu_publisher`)
  - Removed publisher setter methods
  - Removed WebRTC coupling (`setup_webrtc_interface`, `_handle_webrtc_message`, `_send_message_to_webrtc`)
  - Removed recording functionality (moved to client-side): `is_recording`, `recorded_data`, `_rec_lock`, `append_record()`, `start_recording()`, `stop_recording()`
- `src/reachy_mini/daemon/backend/robot/backend.py`:
  - Removed `zenoh` and `json` imports
  - Removed `imu_publisher` attribute
  - Simplified `_update()` to just read state and update kinematics (no publishing)
- `src/reachy_mini/daemon/backend/mujoco/backend.py`:
  - Removed `json` import
  - Removed publishing code from `run()` loop
- `src/reachy_mini/daemon/backend/mockup_sim/backend.py`:
  - Removed `json` import
  - Removed publishing code from `run()` loop
- `src/reachy_mini/io/zenoh_server.py`:
  - Removed calls to `backend.set_*_publisher()`
  - Removed recording command handlers
  - Added TODO for implementing polling loop
- `src/reachy_mini/daemon/daemon.py`:
  - Removed `backend.setup_webrtc_interface()` call (TODO added)

**Note**: ZenohServer currently won't publish state data - needs polling loop implementation.

### Step 2: Backend Template Method Pattern (DONE)
Goal: Reduce code duplication across backend implementations using Template Method Pattern.

Changes made:
- `src/reachy_mini/daemon/backend/abstract.py`:
  - Made `Backend` a proper ABC (inherits from `ABC`)
  - Added `BackendStatus` base dataclass for status objects
  - Implemented Template Method Pattern in `run()`:
    - Unified control loop logic (timing, state reading, kinematics update)
    - `_read_joint_positions()` - abstract method for reading positions
    - `_apply_targets()` - abstract method for applying targets
    - `_on_start()` - hook for initialization
    - `_on_update()` - hook for per-iteration tasks
    - `_on_stop()` - hook for cleanup
  - Added abstract methods: `get_motor_control_mode()`, `set_motor_control_mode()`, `set_motor_torque_ids()`

- `src/reachy_mini/daemon/backend/mockup_sim/backend.py`:
  - Reduced from ~154 to ~85 lines
  - Now only implements abstract methods (no `run()` override)

- `src/reachy_mini/daemon/backend/mujoco/backend.py`:
  - Reduced from ~370 to ~310 lines
  - Uses `_on_start()` for viewer/thread init, `_on_update()` for physics stepping, `_on_stop()` for cleanup

- `src/reachy_mini/daemon/backend/robot/backend.py`:
  - Reduced from ~655 to ~420 lines
  - Uses `_on_start()` for kinematics init, `_on_update()` for hardware error checking

Benefits:
- Control loop logic is now centralized in the abstract class
- Concrete backends only implement what's unique to them
- Easier to add new backend types
- Better separation of concerns

### Step 3: Unified BackendStatus with Common Stats (DONE)
Goal: All backends use the same `BackendStatus` class with common stats collection.

Changes made:
- `src/reachy_mini/daemon/backend/abstract.py`:
  - Extended `BackendStatus` with common fields: `ready`, `last_alive`, `control_loop_stats`
  - Added stats tracking infrastructure to `Backend.__init__`: `_stats_timestamps`, `_stats_error_count`, `_stats_record_period`, `_stats_record_t0`
  - Added `_collect_control_loop_stats()` method for computing stats (mean frequency, max interval, error count)
  - Updated `run()` to track timestamps and collect stats periodically
  - Made `get_status()` non-abstract with default implementation

- `src/reachy_mini/daemon/backend/mockup_sim/backend.py`:
  - Removed `MockupSimBackendStatus` class (uses common `BackendStatus`)
  - Removed `get_status()` override (uses base implementation)
  - Uses `self._status.motor_control_mode` instead of separate field

- `src/reachy_mini/daemon/backend/mujoco/backend.py`:
  - Removed `MujocoBackendStatus` class (uses common `BackendStatus`)
  - Removed `get_status()` override (uses base implementation)
  - Uses `self._status.motor_control_mode` instead of returning constant

- `src/reachy_mini/daemon/backend/robot/backend.py`:
  - Removed `RobotBackendStatus` class (uses common `BackendStatus`)
  - Removed `get_status()` override (uses base implementation)
  - Removed custom stats tracking (now in base class)
  - Overrides `_collect_control_loop_stats()` to add motor controller stats

- Updated `__init__.py` files:
  - Removed status class exports from all backend packages

Benefits:
- All backends share the same status data model
- Stats collection is centralized and consistent
- Reduced code duplication (~50 lines removed)
- Easier for interfaces to consume backend status

### Step 4: Abstracted Timing with `_wait_for_tick()` (DONE)
Goal: Prepare for Rust-controlled timing by abstracting the control loop timing mechanism.

**Problem**: Python's `time.sleep()` and GIL make 50Hz timing unreliable for real robot control.

**Solution**: Add `_wait_for_tick()` hook that can be overridden for different timing strategies:
- **SimBackends** (default): Python `time.sleep()` - acceptable for simulation
- **RobotBackend** (future): Block on Rust channel - precise timing controlled by Rust

Changes made:
- `src/reachy_mini/daemon/backend/abstract.py`:
  - Added `_tick_period` and `_last_tick_time` attributes
  - Added `_wait_for_tick()` method at start of each loop iteration
  - Default implementation sleeps to maintain frequency (min 1ms to release GIL)
  - Removed sleep logic from end of `run()` loop

**Future work** (requires Rust changes to `reachy-mini-motor-controller`):
- Add `wait_next_cycle()` method to Rust motor controller
- Add target queuing mechanism in Rust
- Override `_wait_for_tick()` in `RobotBackend` to block on Rust channel

Architecture when Rust timing is implemented:
```
Rust:   |--read--write--sleep--|--read--write--sleep--|--read--...
               │                      │
               ▼ signal               ▼ signal
Python:        |===FK/IK===|          |===FK/IK===|
               ▲           │          ▲           │
               │           ▼ queue    │           ▼ queue
          wait_for_tick  targets   wait_for_tick  targets
```

### Step 5: Remove Zenoh and WebSocket from Daemon (DONE)
Goal: Clean up the daemon by removing all interface code (to be added back with proper abstraction later).

Changes made:
- `src/reachy_mini/daemon/daemon.py`:
  - Removed ZenohServer initialization and lifecycle management
  - Removed AsyncWebSocketController for robot control
  - Removed media streaming code (frames, audio publishing)
  - Removed `_publish_status()` method
  - Removed `websocket_uri` and `stream_media` parameters from all methods
  - Changed `self.backend` type to abstract `Backend | None`
  - Changed `_setup_backend()` return type to abstract `Backend`
  - Added TODOs for adding interfaces back and DaemonStatus publishing

- `src/reachy_mini/daemon/backend/mujoco/backend.py`:
  - Removed `websocket_uri` parameter
  - Removed `_streaming_loop()` method and WebSocket video streaming
  - Removed cv2 and AsyncWebSocketFrameSender imports

- `src/reachy_mini/daemon/app/main.py`:
  - Removed `websocket_uri` and `stream_media` from Args class
  - Removed websocket-related CLI arguments

- `src/reachy_mini/daemon/app/routers/daemon.py`:
  - Removed `websocket_uri` and `stream_media` from daemon.start() call

**Impact**: ~174 lines removed from daemon code. Tests that rely on ReachyMini client connecting via Zenoh will fail until interfaces are re-added.

### Step 6: Remove Deprecated WebSocket IO Files (DONE)
Goal: Remove websocket streaming code that was moved to a separate app (PR #781).

Files deleted:
- `src/reachy_mini/io/audio_ws.py` - AsyncWebSocketAudioStreamer
- `src/reachy_mini/io/video_ws.py` - AsyncWebSocketFrameSender
- `src/reachy_mini/io/ws_controller.py` - AsyncWebSocketController

Updated:
- `src/reachy_mini/io/__init__.py` - Removed exports of deleted classes

**Note**: Kept `zenoh_client.py` and `zenoh_server.py` as `ZenohClient` is still used by `ReachyMini` SDK client.

### Step 7: Daemon Architecture Refactoring (DONE)
Goal: Split the Daemon class into proper hierarchical structure with clear separation of concerns.

**Architecture (updated):**
```
Daemon (orchestrator)
├── MotorManager (motor control lifecycle)
│   └── MotorController (MuJoCo/Robot/Mockup)
├── ApiManager (FastAPI HTTP/WS server)
├── WebRTCManager (real-time streaming)
├── MotionManager (motion with synchronized audio)
├── _audio_manager (local sound playback)
└── AppManager (user app lifecycle)
```

**Files created:**
- `src/reachy_mini/motor_controller/manager.py` - MotorManager (manages motor controller lifecycle)
- `src/reachy_mini/daemon/api_manager.py` - ApiManager (FastAPI server)
- `src/reachy_mini/daemon/webrtc_manager.py` - WebRTCManager (real-time streaming)
- `src/reachy_mini/motion/manager.py` - MotionManager (motion + audio sync)

**Naming changes:**
- `Backend` → `MotorController`
- `BackendManager` → `MotorManager`
- `backend/` → `motor_controller/`
- `InterfaceManager` → Split into `ApiManager` + `WebRTCManager`
- `_media_manager` → `_audio_manager` (clarifies it's for audio playback)

**Key Design Decisions:**
- FastAPI always runs (daemon = HTTP server)
- Motor controller can start/stop/crash without affecting FastAPI server
- No FastAPI lifespan - motor controller lifecycle managed via API endpoints
- Daemon status reflects motor controller state (ready/error/etc.)
- WebRTC is separate from ApiManager (handles media + motor data streaming)
- ApiManager and WebRTCManager should share motor data models

**Benefits:**
- Clear separation of concerns
- Daemon is the single point of control
- Motor controller failures don't crash the entire daemon
- Easier to add/remove interfaces without touching motor control logic
- WebRTC can evolve independently for motor data streaming

### Step 8: Folder Reorganization and Daemon Improvements (DONE)
Goal: Cleaner folder structure and better daemon usability.

**Final folder structure:**
```
daemon/
├── main.py              # CLI entry point
├── args.py              # DaemonArgs dataclass
├── daemon.py            # Daemon orchestrator (async context manager)
├── api_manager.py       # FastAPI HTTP/WS server
├── webrtc_manager.py    # Real-time streaming (video, audio, motor data)
├── utils.py
├── api/                 # HTTP API layer
│   ├── routers/
│   ├── dashboard/
│   ├── dependencies.py
│   └── ...
motor_controller/
├── manager.py           # MotorManager (lifecycle management)
├── abstract.py          # MotorController base class
├── mujoco/
├── robot/
└── mockup_sim/
motion/
├── manager.py           # MotionManager (motion + audio sync)
├── move.py
├── goto.py
└── recorded_move.py
```

**Daemon async context manager support:**
```python
async with Daemon(DaemonArgs(sim=True, headless=True)) as daemon:
    # daemon is running
# automatically stopped, even on exceptions
```

**Signal handling improvements:**
- Single Ctrl-C cleanly shuts down the daemon
- Proper SIGINT/SIGTERM handlers in `run4ever()`
- Robot properly goes to sleep on shutdown

### Step 9: Clean Up Component Dependencies (DONE)
Goal: Ensure each manager only deals with its scope, no leftover entanglements.

**Issues fixed:**
- `AppManager` was importing concrete `RobotController` - now uses abstract `MotorControlMode`
- `AppManager` was calling `motor_controller.goto_target()` (non-existent) - now uses `motion_manager.goto_target()`
- `AppManager` was using `isinstance(RobotController)` check - now uses `set_motor_control_mode(MotorControlMode.Enabled)`

**Resilience test added:**
- `test_daemon_faulty_audio_backend_still_running` - verifies daemon continues in RUNNING state when audio fails to initialize (audio is non-critical)

---

## Test Status

After refactoring: **Daemon tests: 4 passed, 2 failed (expected)**

```bash
uv run pytest tests/test_daemon.py -v
# test_daemon_start_stop PASSED
# test_daemon_faulty_motor_controller_fastapi_still_running PASSED
# test_daemon_faulty_audio_backend_still_running PASSED
# test_daemon_multiple_start_stop PASSED
# test_daemon_client_disconnection FAILED  # Requires ZenohServer (removed)
# test_daemon_early_stop FAILED            # Requires ZenohServer (removed)
```

| Category | Count | Reason |
|----------|-------|--------|
| MuJoCo not installed | 4 | Optional dependency |
| ZenohServer removed | 2 (daemon) + others | Expected - Zenoh interfaces removed in Step 5 |
| Placo kinematics | 1 | Optional dependency |
| Audio/Video backends | 8 | Sounddevice deprecated, WebRTC setup |

**Note**: Tests using `ReachyMini` client (`test_daemon_client_disconnection`, `test_daemon_early_stop`) fail because they connect via Zenoh (port 7447), which was intentionally removed. These will pass once interfaces are re-added with proper abstraction.

---

## Next Steps

### Shared Data Models
ApiManager (FastAPI) and WebRTCManager both need to expose data. They should share the same data models to ensure consistency:

```
daemon/
├── models/
│   ├── motor_state.py    # Joint positions, poses, control mode
│   ├── motor_command.py  # Target positions, control commands
│   ├── media.py          # Audio/video frame metadata, streaming config
│   └── ...
├── api_manager.py        # Uses shared models for HTTP/WS
└── webrtc_manager.py     # Uses shared models for real-time streaming
```

**Motor data models:**
- Joint positions (current/target)
- Head pose (4x4 matrix or position + orientation)
- Antenna positions
- Control mode (enabled/disabled/gravity compensation)
- Status/errors

**Media data models:**
- Video frame metadata (resolution, format, timestamp)
- Audio sample metadata (sample rate, channels, format)
- Streaming configuration

This will enable:
- Consistent data format across HTTP API and WebRTC
- Single source of truth for data schemas
- Easier client implementation (same models for both transports)
- Clear contract between daemon internals and external interfaces

---

## Open Questions

_(Questions and decisions to discuss will be tracked here)_

## Design Decisions

- **Manager naming**: `ApiManager` for HTTP/WS, `WebRTCManager` for real-time streaming, `MotorManager` for motor control lifecycle
- **WebRTC scope**: Handles media (video, audio) AND motor data streaming
- **Folder structure**: `api/` for HTTP routers, `daemon/` for orchestration and managers, `motor_controller/` for control logic
- **Shared models**: ApiManager & WebRTCManager will share motor + media data models

## Potential Future Improvements

Identified during refactoring (not yet implemented):

1. **Type Safety**: 21 `type: ignore` in `abstract.py` for Placo kinematics - could add proper stubs
2. **Global State**: Module-level dicts in `move.py`, `bg_job_register.py` - could use dependency injection
3. **WebSocket Patterns**: Duplicated disconnect handling in 4+ routers - could create shared utilities
4. **Motor Controller Factory**: If-elif chain in `manager.py` - could use registry pattern
5. **Hardcoded Paths**: `/home/pollen/...` in `cache.py` - should use environment variables
