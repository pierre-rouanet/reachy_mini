import asyncio
from unittest.mock import patch

import aiohttp
import numpy as np
import pytest

from reachy_mini import ReachyMini
from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon, DaemonState

# Common test config
_TEST_CONFIG = DaemonArgs(sim=True, headless=True, wake_up_on_start=False, use_audio=False, goto_sleep_on_stop=False)


@pytest.mark.asyncio
async def test_daemon_start_stop() -> None:
    async with Daemon(_TEST_CONFIG):
        pass


@pytest.mark.asyncio
async def test_daemon_faulty_motor_controller_fastapi_still_running() -> None:
    """Test that FastAPI runs and returns error status when motor controller fails to start.

    Also verifies that the port is properly released after stopping.
    """
    # Use real robot mode with invalid serial port to cause motor controller failure
    faulty_config = DaemonArgs(
        sim=False,
        mockup_sim=False,
        serialport="/dev/nonexistent_port",
        headless=True,
        wake_up_on_start=False,
        use_audio=False,
        goto_sleep_on_stop=False,
    )
    daemon = Daemon(faulty_config)

    try:
        # Start should complete (FastAPI runs) but motor controller should fail
        state = await daemon.start()

        # Daemon should be in ERROR state due to motor controller failure
        assert state == DaemonState.ERROR
        assert daemon._error is not None
        # Error could be "No such file or directory" or "No Reachy Mini serial port found"
        assert len(daemon._error) > 0

        # FastAPI should still be reachable
        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8000/api/daemon/status") as response:
                assert response.status == 200
                status = await response.json()

                # Verify the status reflects the error
                assert status["state"] == "error"
                assert status["error"] is not None
                assert status["motor_controller_status"] is None  # Motor controller never started

    finally:
        await daemon.stop()

    # Verify the port is properly released by starting a new daemon on the same port
    async with Daemon(_TEST_CONFIG):
        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8000/api/daemon/status") as response:
                assert response.status == 200
                status = await response.json()
                # This daemon should be running normally
                assert status["state"] == "running"


@pytest.mark.asyncio
async def test_daemon_faulty_audio_backend_still_running() -> None:
    """Test that daemon runs normally when audio backend fails to initialize.

    Audio is non-critical - daemon should continue in RUNNING state even if
    audio fails. Motion commands that use audio should gracefully degrade.
    """
    # Config with audio enabled
    config_with_audio = DaemonArgs(
        sim=True,
        headless=True,
        wake_up_on_start=False,
        use_audio=True,  # Enable audio
        goto_sleep_on_stop=False,
    )

    # Mock MediaManager to raise an exception (simulating missing audio device)
    with patch(
        "reachy_mini.daemon.daemon.MediaManager",
        side_effect=RuntimeError("No audio device found: speaker not detected"),
    ):
        daemon = Daemon(config_with_audio)

        try:
            state = await daemon.start()

            # TODO: Maybe this error should still be visible in status?

            # Daemon should still be RUNNING (audio failure is non-critical)
            assert state == DaemonState.RUNNING
            assert daemon._error is None  # No error since audio is optional

            # Audio manager should be None due to initialization failure
            assert daemon._audio_manager is None

            # FastAPI should still be reachable
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8000/api/daemon/status") as response:
                    assert response.status == 200
                    status = await response.json()
                    assert status["state"] == "running"

            # Motor controller should work normally
            assert daemon.motor_controller is not None
            assert daemon.motor_controller.ready.is_set()

            # MotionManager should handle missing audio gracefully
            # (play_sound/stop_sound should be no-ops when audio is None)
            daemon.motion_manager.play_sound("wake_up.wav")  # Should not raise
            daemon.motion_manager.stop_sound()  # Should not raise

        finally:
            await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_multiple_start_stop() -> None:
    for _ in range(3):
        async with Daemon(_TEST_CONFIG):
            pass


@pytest.mark.asyncio
async def test_daemon_client_disconnection() -> None:
    config = DaemonArgs(sim=True, headless=True, wake_up_on_start=True, use_audio=False, goto_sleep_on_stop=False)
    async with Daemon(config) as daemon:
        client_connected = asyncio.Event()

        async def simple_client() -> None:
            with ReachyMini(media_backend="no_media") as mini:
                status = mini.get_status()
                assert status['state'] == "running"
                assert status['simulation_enabled']
                assert status['error'] is None
                assert status['motor_controller_status']['motor_control_mode'] == "enabled"
                assert status['motor_controller_status']['error'] is None
                assert status['wlan_ip'] is None
                client_connected.set()

        async def wait_for_client() -> None:
            await client_connected.wait()
            await daemon.stop()

        await asyncio.gather(simple_client(), wait_for_client())


@pytest.mark.asyncio
async def test_daemon_early_stop() -> None:
    async with Daemon(_TEST_CONFIG) as daemon:
        client_connected = asyncio.Event()
        daemon_stopped = asyncio.Event()

        async def client_bg() -> None:
            with ReachyMini(media_backend="no_media") as reachy:
                client_connected.set()
                await daemon_stopped.wait()

                with pytest.raises(ConnectionError, match="Lost connection with the server."):
                    reachy.set_target(head=np.eye(4))

        async def will_stop_soon() -> None:
            await client_connected.wait()
            await daemon.stop()
            daemon_stopped.set()

        await asyncio.gather(client_bg(), will_stop_soon())


@pytest.mark.asyncio
async def test_daemon_early_stop_get_state() -> None:
    """Test that getting state also fails when daemon stops."""
    async with Daemon(_TEST_CONFIG) as daemon:
        client_connected = asyncio.Event()
        daemon_stopped = asyncio.Event()

        async def client_bg() -> None:
            with ReachyMini(media_backend="no_media") as reachy:
                # Verify we can get head pose while connected
                pose = reachy.get_current_head_pose()
                assert pose is not None

                client_connected.set()
                await daemon_stopped.wait()

                with pytest.raises(ConnectionError, match="Lost connection with the server."):
                    reachy.get_current_head_pose()

        async def will_stop_soon() -> None:
            await client_connected.wait()
            await daemon.stop()
            daemon_stopped.set()

        await asyncio.gather(client_bg(), will_stop_soon())
