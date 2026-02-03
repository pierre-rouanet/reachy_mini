"""Tests for the API client."""

import pytest

from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon
from reachy_mini.sdk_client.api_client import ApiClient

_TEST_CONFIG = DaemonArgs(
    sim=True, headless=True, wake_up_on_start=False, use_audio=False, goto_sleep_on_stop=False
)


@pytest.mark.asyncio
async def test_api_client_connect() -> None:
    """Test API client connection and state retrieval."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)
            assert client.is_connected()

            # Get state
            state = client.get_state()
            assert state is not None
            assert "head_pose" in state

            # Get head pose
            pose = client.get_head_pose()
            assert pose.shape == (4, 4)

            # Get joints
            head_joints, antennas = client.get_joints()
            assert len(head_joints) == 7
            assert len(antennas) == 2

            # Get body yaw
            body_yaw = client.get_body_yaw()
            assert isinstance(body_yaw, float)

        finally:
            client.disconnect()
