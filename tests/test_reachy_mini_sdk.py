"""Tests for the synchronous ReachyMini SDK wrapper.

These tests exercise the public API of the ReachyMini class,
which wraps the async StreamClient in a synchronous interface.
"""

import asyncio
import threading
import warnings

import numpy as np
import pytest

from reachy_mini import ReachyMini
from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon

_TEST_CONFIG = DaemonArgs(
    sim=True,
    headless=True,
    wake_up_on_start=False,
    use_audio=False,
    goto_sleep_on_stop=False,
)


def _run_in_daemon(fn):
    """Run a sync function inside a daemon context using executor pattern."""

    @pytest.mark.asyncio
    async def wrapper() -> None:
        async with Daemon(_TEST_CONFIG):
            done = threading.Event()
            error = None

            def sync_client() -> None:
                nonlocal error
                try:
                    with ReachyMini(media_backend="no_media") as reachy:
                        fn(reachy)
                except Exception as e:
                    error = e
                finally:
                    done.set()

            task = asyncio.get_event_loop().run_in_executor(None, sync_client)
            if not await asyncio.to_thread(done.wait, timeout=15):
                await task
                raise AssertionError("sync_client did not complete within 15s")
            await task
            if error is not None:
                raise error

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper


# --- set_target ---


@_run_in_daemon
def test_sdk_set_target_head(reachy: ReachyMini) -> None:
    """set_target with head, antennas, and body_yaw."""
    reachy.set_target(head=np.eye(4))
    reachy.set_target(antennas=[0.1, -0.1])
    reachy.set_target(body_yaw=0.05)
    reachy.set_target(head=np.eye(4), antennas=[0.0, 0.0], body_yaw=0.0)


@_run_in_daemon
def test_sdk_set_target_validation(reachy: ReachyMini) -> None:
    """set_target raises ValueError for invalid inputs."""
    with pytest.raises(ValueError, match="At least one"):
        reachy.set_target()

    with pytest.raises(ValueError, match="4x4"):
        reachy.set_target(head=np.eye(3))

    with pytest.raises(ValueError, match="two elements"):
        reachy.set_target(antennas=[1.0, 2.0, 3.0])

    with pytest.raises(ValueError, match="body_yaw must be a float"):
        reachy.set_target(body_yaw="bad")  # type: ignore[arg-type]


# --- goto_target ---


@_run_in_daemon
def test_sdk_goto_target(reachy: ReachyMini) -> None:
    """goto_target with head and body_yaw."""
    reachy.goto_target(head=np.eye(4), duration=0.3)
    reachy.goto_target(head=np.eye(4), body_yaw=0.0, duration=0.3)
    reachy.goto_target(body_yaw=0.05, duration=0.2)


@_run_in_daemon
def test_sdk_goto_target_validation(reachy: ReachyMini) -> None:
    """goto_target raises ValueError for invalid inputs."""
    with pytest.raises(ValueError, match="At least one"):
        reachy.goto_target()

    with pytest.raises(ValueError, match="Duration must be positive"):
        reachy.goto_target(head=np.eye(4), duration=0.0)

    with pytest.raises(ValueError, match="Duration must be positive"):
        reachy.goto_target(head=np.eye(4), duration=-1.0)


# --- body_yaw ---


@_run_in_daemon
def test_sdk_body_yaw(reachy: ReachyMini) -> None:
    """set_target_body_yaw, get_current_body_yaw, set_automatic_body_yaw."""
    reachy.set_target_body_yaw(0.1)
    yaw = reachy.get_current_body_yaw()
    assert isinstance(yaw, float)

    reachy.set_automatic_body_yaw(False)
    reachy.set_automatic_body_yaw(True)


# --- head_joints ---


@_run_in_daemon
def test_sdk_head_joints(reachy: ReachyMini) -> None:
    """set_target_head_joints and get_current_head_joints."""
    joints = reachy.get_current_head_joints()
    assert len(joints) == 6

    # Small offset from current
    target = [j + 0.001 for j in joints]
    reachy.set_target_head_joints(target)


@_run_in_daemon
def test_sdk_head_joints_validation(reachy: ReachyMini) -> None:
    """set_target_head_joints raises ValueError for wrong length."""
    with pytest.raises(ValueError, match="6 elements"):
        reachy.set_target_head_joints([0.0, 0.0])

    with pytest.raises(ValueError, match="6 elements"):
        reachy.set_target_head_joints([0.0] * 7)


# --- joint positions ---


@_run_in_daemon
def test_sdk_get_joint_positions(reachy: ReachyMini) -> None:
    """get_current_joint_positions and get_present_antenna_joint_positions."""
    head_joints, antenna_joints = reachy.get_current_joint_positions()
    assert len(head_joints) == 6
    assert len(antenna_joints) == 2

    antennas = reachy.get_present_antenna_joint_positions()
    assert len(antennas) == 2


# --- recording ---


@_run_in_daemon
def test_sdk_recording(reachy: ReachyMini) -> None:
    """start_recording, _set_record_data, stop_recording cycle."""
    # Stop before start returns None
    assert reachy.stop_recording() is None

    reachy.start_recording()
    assert reachy.is_recording is True

    reachy._set_record_data({"t": 0.0, "head": [0.0] * 6})
    reachy._set_record_data({"t": 0.1, "head": [0.1] * 6})

    data = reachy.stop_recording()
    assert reachy.is_recording is False
    assert data is not None
    assert len(data) == 2

    # _set_record_data with bad type
    with pytest.raises(ValueError, match="Record must be a dictionary"):
        reachy._set_record_data("not a dict")  # type: ignore[arg-type]


# --- motors ---


@_run_in_daemon
def test_sdk_enable_disable_motors(reachy: ReachyMini) -> None:
    """enable_motors, disable_motors, enable_gravity_compensation."""
    reachy.enable_motors()
    reachy.disable_motors()
    reachy.enable_gravity_compensation()
    reachy.disable_gravity_compensation()
    reachy.enable_motors()


# --- deprecated params ---


@pytest.mark.asyncio
async def test_sdk_deprecated_params() -> None:
    """Deprecated constructor params emit DeprecationWarning."""
    async with Daemon(_TEST_CONFIG):
        done = threading.Event()

        def sync_client() -> None:
            # robot_name emits warning
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with ReachyMini(media_backend="no_media", robot_name="foo") as _:
                    pass
            assert any(issubclass(x.category, DeprecationWarning) for x in w)
            assert any("robot_name" in str(x.message) for x in w)

            # connection_mode emits warning and resolves host
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with ReachyMini(
                    media_backend="no_media", connection_mode="localhost_only"
                ) as _:
                    pass
            assert any("connection_mode" in str(x.message) for x in w)

            # localhost_only emits warning
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with ReachyMini(media_backend="no_media", localhost_only=True) as _:
                    pass
            assert any("localhost_only" in str(x.message) for x in w)

            done.set()

        task = asyncio.get_event_loop().run_in_executor(None, sync_client)
        if not await asyncio.to_thread(done.wait, timeout=15):
            await task
            raise AssertionError("sync_client did not complete within 15s")
        await task


# --- look_at_world ---


@_run_in_daemon
def test_sdk_look_at_world(reachy: ReachyMini) -> None:
    """look_at_world with perform_movement=False returns correct pose matrix."""
    # Look straight ahead (1, 0, 0) — should be close to identity rotation
    pose = reachy.look_at_world(x=1.0, y=0.0, z=0.0, perform_movement=False)
    assert pose.shape == (4, 4)
    np.testing.assert_allclose(pose[:3, :3], np.eye(3), atol=1e-6)
    assert pose[3, 3] == 1.0

    # Look to the left (1, 1, 0) — rotation around Z axis
    pose = reachy.look_at_world(x=1.0, y=1.0, z=0.0, perform_movement=False)
    assert pose.shape == (4, 4)
    # The head should be rotated roughly 45 degrees around z
    assert pose[0, 1] != 0.0  # off-diagonal element should be non-zero

    # Look up (1, 0, 1)
    pose = reachy.look_at_world(x=1.0, y=0.0, z=1.0, perform_movement=False)
    assert pose.shape == (4, 4)

    # Negative duration raises
    with pytest.raises(ValueError, match="negative"):
        reachy.look_at_world(x=1.0, y=0.0, z=0.0, duration=-1.0)
