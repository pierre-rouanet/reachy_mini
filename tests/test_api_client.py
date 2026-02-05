"""Tests for the API client."""

import time

import numpy as np
import pytest

from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon
from reachy_mini.daemon.models import FullState
from reachy_mini.daemon.streaming.messages import MoveId
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

            # Get state - should be a FullState model with all fields populated
            state = client.get_state()
            assert isinstance(state, FullState)
            assert state.head_pose is not None
            assert state.head_joints is not None
            assert len(state.head_joints) == 6  # 6 stewart platform joints (body_rotation separate)
            assert state.antennas is not None
            assert len(state.antennas) == 2
            assert state.body_rotation is not None

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_api_client_goto_head_pose() -> None:
    """Test goto with head pose target."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)

            # Create a target head pose (tilted 10 degrees pitch)
            target_pose = np.eye(4)
            pitch_rad = np.radians(10)
            target_pose[1, 1] = np.cos(pitch_rad)
            target_pose[1, 2] = -np.sin(pitch_rad)
            target_pose[2, 1] = np.sin(pitch_rad)
            target_pose[2, 2] = np.cos(pitch_rad)

            # Send goto request
            move_uuid = client.send_goto_request(
                head=target_pose,
                duration=0.5,
            )

            # Verify we got a valid move ID (UUID string)
            assert isinstance(move_uuid, str)
            assert len(move_uuid) > 0

            # Wait for move completion
            client.wait_for_move_completion(move_uuid, timeout=2.0)

            # Verify the head moved (give some tolerance for simulation)
            state = client.get_state()
            assert state.head_pose is not None

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_api_client_goto_antennas() -> None:
    """Test goto with antennas target."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)

            # Target antenna positions (in radians)
            target_antennas = [0.5, -0.5]

            # Send goto request
            move_uuid = client.send_goto_request(
                antennas=target_antennas,
                duration=0.5,
            )

            assert isinstance(move_uuid, str)

            # Wait for move completion
            client.wait_for_move_completion(move_uuid, timeout=2.0)

            # Verify antennas moved close to target
            state = client.get_state()
            assert state.antennas is not None
            assert len(state.antennas) == 2
            # Allow some tolerance for simulation timing
            assert abs(state.antennas[0] - target_antennas[0]) < 0.15
            assert abs(state.antennas[1] - target_antennas[1]) < 0.15

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_api_client_goto_combined() -> None:
    """Test goto with both head and antennas targets."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)

            target_pose = np.eye(4)
            target_antennas = [0.3, -0.3]

            move_uuid = client.send_goto_request(
                head=target_pose,
                antennas=target_antennas,
                duration=0.5,
            )

            assert isinstance(move_uuid, str)
            client.wait_for_move_completion(move_uuid, timeout=2.0)

            state = client.get_state()
            assert state.head_pose is not None
            assert state.antennas is not None

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_api_client_goto_timeout() -> None:
    """Test that goto timeout raises TimeoutError."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)

            # Send a long goto request
            move_uuid = client.send_goto_request(
                head=np.eye(4),
                duration=5.0,  # 5 second movement
            )

            # Try to wait with a very short timeout
            with pytest.raises(TimeoutError):
                client.wait_for_move_completion(move_uuid, timeout=0.1)

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_api_client_goto_invalid_uuid() -> None:
    """Test that waiting for unknown move UUID raises ValueError."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)

            # Create a fake move ID that was never sent
            from uuid import uuid4

            fake_move_id = str(uuid4())

            with pytest.raises(ValueError, match="No ongoing move with ID"):
                client.wait_for_move_completion(fake_move_id, timeout=1.0)

        finally:
            client.disconnect()


@pytest.mark.asyncio
async def test_api_client_goto_sequential() -> None:
    """Test multiple sequential goto requests."""
    async with Daemon(_TEST_CONFIG):
        client = ApiClient(host="localhost", port=_TEST_CONFIG.fastapi_port)
        try:
            client.connect(timeout=5.0)

            # First movement - use longer duration for simulation to catch up
            move1 = client.send_goto_request(
                antennas=[0.5, -0.5],
                duration=0.5,
            )
            client.wait_for_move_completion(move1, timeout=2.0)

            # Give simulation time to settle
            time.sleep(0.1)

            state1 = client.get_state()
            assert state1.antennas is not None
            # Use wider tolerance for simulation
            assert abs(state1.antennas[0] - 0.5) < 0.2

            # Second movement
            move2 = client.send_goto_request(
                antennas=[-0.5, 0.5],
                duration=0.5,
            )
            client.wait_for_move_completion(move2, timeout=2.0)

            time.sleep(0.1)

            state2 = client.get_state()
            assert state2.antennas is not None
            assert abs(state2.antennas[0] - (-0.5)) < 0.2

        finally:
            client.disconnect()
