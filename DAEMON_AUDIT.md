# Daemon Audit Report

## Structure & Architecture — Good

The daemon has a clean, well-layered architecture: a `Daemon` orchestrator delegates to managers (`MotorManager`, `HttpServer`, `StreamingManager`, `AppManager`, `MotionManager`). The `models/` package is shared between HTTP and streaming APIs — single source of truth. The streaming layer is properly transport-agnostic via an ABC. Documentation (docstrings) is thorough.

---

## Issues

### HIGH Priority

- [x] **1. Code duplication: `state.py` vs `handler.py` (build_state)**
  Extracted shared `build_state()` into `daemon/state_builder.py`. Both `state.py` and `session.py` now delegate to it. Also closed feature gaps: streaming gained `passive_joints`, HTTP gained `imu` sensor. Removed 10 `# type: ignore[operator]` and an unsafe `assert target_pose is not None`.

- [ ] **2. Duplicated `set_target` logic**
  `api/routers/move.py:138-171` and `streaming/handler.py:147-167` both do the same 6→7 joint array conversion.

- [ ] **3. Bluetooth `_handle_command` can return `None`**
  `bluetooth_service.py:598-618` — the `CMD_` branch falls through without a return, but the function is typed `-> str`.

- [x] **4. Type mismatch in `webrtc_manager.py:166-167`**
  Changed `_sessions` type to `dict[str, concurrent.futures.Future[None]]`, removed `# type: ignore[assignment]`.

### MEDIUM Priority

- [ ] **5. Module-level side effects in `wifi_config.py:280-294`**
  Importing the module triggers WiFi scans and potentially sets up a hotspot. Breaks testability.

- [ ] **6. `assert` used for runtime validation in API-facing code**
  `dependencies.py:15,26,45,56`, `state.py:56,110`, `models/pose.py:29,70`, `utils.py:90`. These are silently disabled with `python -O`.

- [ ] **7. `print()` instead of `logging` in `utils.py`**
  Lines 80, 86, 89, 94, 147, 157, 160. The rest of the daemon uses structured logging.

- [ ] **8. Path traversal risk in `kinematics.py:49-58`**
  The STL endpoint doesn't validate that the resolved path stays within `STL_ASSETS_DIR`.

- [x] **9. `Daemon` reaches into `ApiManager` privates**
  Refactored: `run_forever()` no longer accesses `_uvicorn_server` or `_server_thread`. Uses `http_server.serve()` (blocking) and `http_server.request_shutdown()` (sync). Thread replaced with asyncio task. `ApiManager` renamed to `HttpServer`.

- [x] **10. Router accesses `Daemon` privates**
  Fixed: router uses `daemon.motor_manager` and `daemon.motion_manager` public properties. Router now calls `start_components()`/`stop_components()` instead of `start()`/`stop()`.

### LOW Priority

- [x] **11. Naming issues**
  Renamed `run4ever` → `run_forever` (daemon.py, main.py, REFACTORING_PLAN.md). Fixed typo `simluation_enabled` → `simulation_enabled` (utils.py). Fixed duplicate "step 4" → "step 5" comment (daemon.py).

- [ ] **12. Style inconsistency: `Optional[X]` vs `X | None`**
  Mixed across `args.py`, `daemon.py`, `webrtc_manager.py`, `volume.py`. Also `List[X]` in `utils.py:164`. Project targets Python ≥3.10.

- [x] **13. Dead code**
  Removed `convert_enum_to_dict` from utils.py (+ unused `Enum`/`List` imports, + autodoc reference in docs). Removed redundant `assert job is not None` from bg_job_register.py.

- [ ] **14. Unreliable `__del__` methods**
  `daemon.py:121-123`, `http_server.py:84-86`, `webrtc_manager.py:63-69`. The last one explicitly calls `self._webrtc.__del__()` — an antipattern.

- [ ] **15. Set mutation inside list comprehension in `wifi_config.py:147`**
  `not seen.add(x.ssid)` with `# type: ignore` — fragile antipattern.

- [ ] **16. Module-level mutable `register` in `bg_job_register.py:41`**
  Shared state, no cleanup, breaks test isolation.

- [x] **17. GPIO pin doc mismatch in `shutdown_monitor.py`**
  Fixed docstring: GPIO24 → GPIO23 to match the code.

- [ ] **18. `bluetooth_service.py` overall quality**
  `# mypy: ignore-errors`, no type annotations, `sudo ls` debug code left in (line 583-588), relative paths for commands directory, minimal auth before running `sudo` scripts via BLE.

- [x] **19. `# type: ignore[operator]` x10 in `handler.py:278-313`**
  Fixed as part of #1 — `build_state` moved to `state_builder.py` with proper `_fields = fields or []` narrowing.

---

## Testing Coverage

**Tested**: Daemon lifecycle (7 tests), HTTP API (10 tests), Motion (12 tests).

**Not tested**: `models/` validators, `streaming/handler.py`, `streaming/session.py`, transports, `bg_job_register.py`, `volume.py`, `kinematics.py`, `logs.py`, `wifi_config.py`, `bluetooth_service.py`, `webrtc_manager.py`, `utils.py`.

---

## Security Notes

- CORS `allow_origins=["*"]` in `http_server.py:161-166` — acceptable for local robot but worth noting.
- Hardcoded WiFi hotspot credentials in `wifi_config.py:11-12`.
- BLE command execution with minimal PIN auth in `bluetooth_service.py`.
