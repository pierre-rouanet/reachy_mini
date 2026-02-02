import asyncio

import aiohttp
import numpy as np
import pytest

from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon, DaemonState
from reachy_mini.reachy_mini import ReachyMini


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
        assert daemon._status.error is not None
        # Error could be "No such file or directory" or "No Reachy Mini serial port found"
        assert len(daemon._status.error) > 0

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
async def test_daemon_multiple_start_stop() -> None:
    for _ in range(3):
        async with Daemon(_TEST_CONFIG):
            pass


@pytest.mark.asyncio
async def test_daemon_client_disconnection() -> None:
    async with Daemon(_TEST_CONFIG) as daemon:
        client_connected = asyncio.Event()

        async def simple_client() -> None:
            with ReachyMini(media_backend="no_media") as mini:
                status = mini.client.get_status()
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

                # Make sure the keep-alive check runs at least once
                reachy.client._check_alive_evt.clear()
                reachy.client._check_alive_evt.wait(timeout=100.0)

                with pytest.raises(ConnectionError, match="Lost connection with the server."):
                    reachy.set_target(head=np.eye(4))

        async def will_stop_soon() -> None:
            await client_connected.wait()
            await daemon.stop()
            daemon_stopped.set()

        await asyncio.gather(client_bg(), will_stop_soon())
