"""Interface manager for Reachy Mini daemon.

This module provides the InterfaceManager class that handles
communication interfaces (FastAPI HTTP server, WebRTC streaming).
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reachy_mini.daemon.args import DaemonArgs

if TYPE_CHECKING:
    from reachy_mini.daemon.daemon import Daemon


class InterfaceManager:
    """Manages daemon communication interfaces.

    Handles FastAPI HTTP server and WebRTC streaming interfaces.
    """

    def __init__(
        self,
        daemon: "Daemon",
        log_level: str = "INFO",
        wireless_version: bool = False,
    ) -> None:
        """Initialize the InterfaceManager.

        Args:
            daemon: The Daemon instance that owns this manager.
            log_level: Logging level.
            wireless_version: Whether running on wireless Reachy Mini hardware.

        """
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        self._daemon = daemon
        self._log_level = log_level
        self._wireless_version = wireless_version

        self._fastapi_app: FastAPI | None = None
        self._uvicorn_server: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None
        self._webrtc: Optional[Any] = None  # GstWebRTC, imported conditionally

        # Initialize WebRTC for wireless version
        if wireless_version:
            try:
                from reachy_mini.media.webrtc_daemon import GstWebRTC

                self._webrtc = GstWebRTC(log_level)
            except Exception as e:
                self.logger.error(f"Failed to initialize WebRTC: {e}")
                self._webrtc = None

    def __del__(self) -> None:
        """Destructor to ensure proper cleanup."""
        self.logger.debug("Cleaning up InterfaceManager resources...")
        if self._webrtc is not None:
            self._webrtc.stop()
            self._webrtc.__del__()
            self._webrtc = None

    @property
    def fastapi_app(self) -> FastAPI | None:
        """Get the FastAPI application instance."""
        return self._fastapi_app

    @property
    def webrtc(self) -> Optional[Any]:
        """Get the WebRTC instance."""
        return self._webrtc

    def create_fastapi_app(
        self,
        args: DaemonArgs,
        health_check_event: asyncio.Event | None = None,
    ) -> FastAPI:
        """Create and configure the FastAPI application.

        Args:
            args: Configuration arguments (DaemonArgs dataclass).
            health_check_event: Optional event for health check endpoint.

        Returns:
            Configured FastAPI application.

        """
        from reachy_mini.daemon.api.routers import (
            apps,
            hf_auth,
            kinematics,
            logs,
            motors,
            move,
            state,
            volume,
        )
        from reachy_mini.daemon.api.routers import daemon as daemon_router

        app = FastAPI()

        # Store references in app state
        app.state.args = args
        app.state.daemon = self._daemon
        app.state.app_manager = self._daemon.app_manager

        # Set up API routes
        router = APIRouter(prefix="/api")
        router.include_router(apps.router)
        router.include_router(daemon_router.router)
        router.include_router(hf_auth.router)
        router.include_router(kinematics.router)
        router.include_router(motors.router)
        router.include_router(move.router)
        router.include_router(state.router)
        router.include_router(volume.router)

        # Wireless-only routes
        if self._wireless_version:
            from reachy_mini.daemon.api.routers import cache, update, wifi_config

            app.include_router(cache.router)
            app.include_router(logs.router)
            app.include_router(update.router)
            app.include_router(wifi_config.router)

        app.include_router(router)

        # Health check endpoint
        if health_check_event is not None:

            @app.post("/health-check")
            async def health_check() -> dict[str, str]:
                """Health check endpoint to reset the health check timer."""
                health_check_event.set()
                return {"status": "ok"}

        # CORS middleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Static files and templates for dashboard
        static_dir = Path(__file__).parent / "api" / "dashboard" / "static"
        templates_dir = Path(__file__).parent / "api" / "dashboard" / "templates"

        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        templates = Jinja2Templates(directory=str(templates_dir))

        @app.get("/")
        async def dashboard(request: Request) -> HTMLResponse:
            """Render the dashboard."""
            return templates.TemplateResponse(
                "index.html", {"request": request, "args": args}
            )

        if self._wireless_version:

            @app.get("/settings")
            async def settings(request: Request) -> HTMLResponse:
                """Render the settings page."""
                return templates.TemplateResponse("settings.html", {"request": request})

            @app.get("/logs")
            async def logs_page(request: Request) -> HTMLResponse:
                """Render the logs page."""
                return templates.TemplateResponse("logs.html", {"request": request})

        self._fastapi_app = app
        return app

    async def run_server(
        self,
        args: DaemonArgs,
        health_check_event: asyncio.Event | None = None,
    ) -> None:
        """Run the FastAPI server with uvicorn.

        Args:
            args: Configuration arguments (DaemonArgs dataclass).
            health_check_event: Optional event for health check endpoint.

        """
        app = self.create_fastapi_app(args, health_check_event)

        config = uvicorn.Config(
            app,
            host=args.fastapi_host,
            port=args.fastapi_port,
            log_config=None,  # Don't override Python logging configuration
        )
        self._uvicorn_server = uvicorn.Server(config)

        health_check_task = None

        async def health_check_timeout(timeout_seconds: float) -> None:
            assert health_check_event is not None
            while True:
                try:
                    await asyncio.wait_for(
                        health_check_event.wait(),
                        timeout=timeout_seconds,
                    )
                    health_check_event.clear()
                except asyncio.TimeoutError:
                    logging.warning("Health check timeout reached, stopping app.")
                    assert self._uvicorn_server is not None
                    self._uvicorn_server.should_exit = True
                    break
                except asyncio.CancelledError:
                    logging.info("Health check task cancelled.")
                    break

        try:
            if args.timeout_health_check is not None:
                health_check_task = asyncio.create_task(
                    health_check_timeout(args.timeout_health_check)
                )
            await self._uvicorn_server.serve()
        except KeyboardInterrupt:
            logging.info("Received Ctrl-C, shutting down gracefully.")
        except Exception as e:
            logging.exception(f"Error during server operation: {e}")
            raise
        finally:
            if health_check_task and not health_check_task.done():
                health_check_task.cancel()
                try:
                    await health_check_task
                except asyncio.CancelledError:
                    pass

    async def start_server(
        self,
        args: DaemonArgs,
        health_check_event: asyncio.Event | None = None,
    ) -> None:
        """Start the FastAPI server in a background thread.

        Args:
            args: Configuration arguments (DaemonArgs dataclass).
            health_check_event: Optional event for health check endpoint.

        """
        if self._server_thread is not None and self._server_thread.is_alive():
            self.logger.warning("Server is already running.")
            return

        app = self.create_fastapi_app(args, health_check_event)

        config = uvicorn.Config(
            app,
            host=args.fastapi_host,
            port=args.fastapi_port,
            log_config=None,  # Don't override Python logging configuration
        )
        server = uvicorn.Server(config)
        self._uvicorn_server = server

        def run_server() -> None:
            asyncio.run(server.serve())

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Wait for server to actually start (bind to port)
        timeout = 5.0
        poll_interval = 0.01
        elapsed = 0.0
        while not server.started and elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if not server.started:
            self.logger.error("FastAPI server failed to start within timeout")
            raise RuntimeError(f"Server failed to start on {args.fastapi_host}:{args.fastapi_port}")

        self.logger.info(f"FastAPI server started on {args.fastapi_host}:{args.fastapi_port}")

    async def stop_server(self) -> None:
        """Stop the FastAPI server."""
        if self._uvicorn_server is not None:
            self.logger.info("Stopping FastAPI server...")
            self._uvicorn_server.should_exit = True

        if self._server_thread is not None and self._server_thread.is_alive():
            # Wait for thread to finish with timeout
            timeout = 5.0
            poll_interval = 0.05
            elapsed = 0.0
            while self._server_thread.is_alive() and elapsed < timeout:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            if self._server_thread.is_alive():
                self.logger.warning("Server thread did not finish in time.")

        self._server_thread = None
        self._uvicorn_server = None
        self.logger.info("FastAPI server stopped.")

    async def start_webrtc(self) -> None:
        """Start WebRTC streaming (if enabled)."""
        if self._webrtc is not None:
            self.logger.info("Starting WebRTC...")
            # Give some time for the backend to release the audio device
            await asyncio.sleep(0.2)
            self._webrtc.start()

    def pause_webrtc(self) -> None:
        """Pause WebRTC streaming (keeps signaling server running)."""
        if self._webrtc is not None:
            self._webrtc.pause()

    def stop_webrtc(self) -> None:
        """Stop WebRTC streaming."""
        if self._webrtc is not None:
            self._webrtc.stop()
