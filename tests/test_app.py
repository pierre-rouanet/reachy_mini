"""Tests for the app manager."""

import asyncio
import time
from pathlib import Path
from threading import Event

import pytest

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini.apps import AppInfo, SourceKind
from reachy_mini.apps.manager import AppState
from reachy_mini.daemon.args import DaemonArgs
from reachy_mini.daemon.daemon import Daemon

# Common test config
_TEST_CONFIG = DaemonArgs(sim=True, headless=True, wake_up_on_start=False, use_audio=False, goto_sleep_on_stop=False)


@pytest.mark.asyncio
async def test_app() -> None:
    """Test basic daemon startup and ReachyMini SDK access."""

    class MockApp(ReachyMiniApp):
        def run(self, reachy_mini: ReachyMini, stop_event: Event) -> None:
            time.sleep(1)  # Simulate some processing time

    async with Daemon(_TEST_CONFIG):
        stop = Event()

        with ReachyMini(media_backend="no_media") as mini:
            app = MockApp()
            app.run(mini, stop)


@pytest.mark.asyncio
async def test_app_manager() -> None:
    """Test app installation, start, stop, and removal."""
    async with Daemon(_TEST_CONFIG) as daemon:
        app_mngr = daemon.app_manager
        before_installed_apps = await app_mngr.list_available_apps(SourceKind.INSTALLED)

        app_info = AppInfo(
            name="ok_app",
            source_kind=SourceKind.LOCAL,
            extra={"path": str(Path(__file__).parent / "ok_app")},
        )
        await app_mngr.install_new_app(app_info, daemon.logger)

        after_installed_apps = await app_mngr.list_available_apps(SourceKind.INSTALLED)

        assert len(after_installed_apps) == len(before_installed_apps) + 1

        status = await app_mngr.start_app("ok_app", media_backend="no_media")
        assert status is not None and status.state in (AppState.STARTING, AppState.RUNNING)
        assert app_mngr.is_app_running()
        status = await app_mngr.current_app_status()
        assert status is not None and status.state in (AppState.STARTING, AppState.RUNNING)

        await app_mngr.stop_current_app()
        assert not app_mngr.is_app_running()
        status = await app_mngr.current_app_status()
        assert status is None

        await app_mngr.remove_app("ok_app", daemon.logger)
        after_uninstalled_apps = await app_mngr.list_available_apps(SourceKind.INSTALLED)

        assert len(after_uninstalled_apps) == len(before_installed_apps)


@pytest.mark.asyncio
async def test_faulty_app() -> None:
    """Test that a faulty app reaches ERROR state."""
    async with Daemon(_TEST_CONFIG) as daemon:
        app_mngr = daemon.app_manager

        app_info = AppInfo(
            name="faulty_app",
            source_kind=SourceKind.LOCAL,
            extra={"path": str(Path(__file__).parent / "faulty_app")},
        )
        await app_mngr.install_new_app(app_info, daemon.logger)

        await app_mngr.start_app("faulty_app", media_backend="no_media")

        success = False
        for _ in range(10):
            status = await app_mngr.current_app_status()
            if status is None or status.state in (AppState.STARTING, AppState.RUNNING):
                await asyncio.sleep(1.0)
                continue

            if status is not None and status.state == AppState.ERROR:
                success = True
                break

        await app_mngr.remove_app("faulty_app", daemon.logger)

        assert success, "Faulty app did not reach ERROR state in time"
