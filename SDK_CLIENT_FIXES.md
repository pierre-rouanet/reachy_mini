# SDK Client Fixes

## Async/Sync Architecture Issue

`ReachyMini` is a sync class wrapping an async `StreamClient` via a background event loop + `_run_async()` bridge. But there's a strange mixup:

- `async_play_move()` is an `async def` on the **sync** class, yet internally calls **sync** methods (`self.goto_target()`, `self.set_target_head_pose()`) which themselves call `_run_async()` to schedule coroutines on the background loop. That's async → sync → async roundtrip.
- `play_move = async_to_sync(async_play_move)` uses `asgiref` to wrap it back to sync, adding a third-party dependency for something `_run_async` already does.
- `wake_up()` and `goto_sleep()` use `time.sleep()` (blocking the caller thread), which is correct for a sync class but means they can't be used from async code.

**Fix**: Remove `async_play_move`. Make `play_move` a regular sync method that calls `_run_async` like everything else. The async play logic belongs on `StreamClient` if needed.

## Fixes (ordered by impact)

### 1. Use `_wait_for_event` helper everywhere in StreamClient
`stream_client.py` already has `_wait_for_event()` at line 188 but 5 methods duplicate the pattern manually: `get_status()`, `set_mode()`, `cancel()`, `set_automatic_body_rotation()`, `get_daemon_status()`.

### 2. Extract `_build_goto_request()` in StreamClient
`goto()` and `goto_async()` build identical `GotoRequest` objects. Extract shared helper.

### 3. Fix `Optional[StreamClient]` typing in ReachyMini
`_stream_client` is always set after `_initialize_client()`. Add a narrowing property that asserts non-None, eliminating 14 `# type: ignore` comments.

### 4. Replace `assert` with proper exceptions in ReachyMini
11 assertions used for runtime validation — should be `ValueError` / `RuntimeError`.

### 5. Fix async/sync mixup in `play_move`
Remove `async_play_move` + `asgiref` dependency. Make `play_move` a regular sync method.

### 6. Replace busy-wait thread sync with `threading.Event`
`_initialize_client` uses `while self._loop is None: time.sleep(0.01)`.

### 7. Remove dead code
- `_last_head_pose` (write-only, never read)
- `hasattr(self, "_recorded_data")` guard (unnecessary)
- Duplicate docstring line in `get_current_head_pose`

### 8. Replace string-based error detection in `_run_async`
Use explicit exception types instead of scanning error messages.

## Verification

```bash
.venv/bin/python -m pytest tests/test_daemon.py tests/test_http_only_client.py tests/test_app.py -x -q --deselect tests/test_app.py::test_app_manager --deselect tests/test_app.py::test_faulty_app
```
