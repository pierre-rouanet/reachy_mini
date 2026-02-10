"""API manager for Reachy Mini daemon.

This module provides the ApiManager class that handles
the FastAPI HTTP server for REST API and WebSocket endpoints.
"""

import asyncio
import logging
import socket
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reachy_mini.daemon.args import DaemonArgs

if TYPE_CHECKING:
    from reachy_mini.daemon.daemon import Daemon


def is_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding.

    Uses SO_REUSEADDR to match uvicorn's socket options. Without this,
    TIME_WAIT connections from recently closed WebSocket connections
    would cause false negatives on macOS.

    Args:
        host: The host address to check.
        port: The port number to check.

    Returns:
        True if the port is available, False if already in use.

    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


class ApiManager:
    """Manages the FastAPI HTTP server.

    Handles FastAPI HTTP server for REST API and WebSocket endpoints.
    """

    def __init__(
        self,
        daemon: "Daemon",
        log_level: str = "INFO",
        wireless_version: bool = False,
    ) -> None:
        """Initialize the ApiManager.

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
        self._last_host: str = "0.0.0.0"
        self._last_port: int = 8000

    def __del__(self) -> None:
        """Destructor to ensure proper cleanup."""
        self.logger.debug("Cleaning up ApiManager resources...")

    @property
    def fastapi_app(self) -> FastAPI | None:
        """Get the FastAPI application instance."""
        return self._fastapi_app

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
            stream,
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
        router.include_router(stream.router)
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
        """Run the FastAPI server with uvicorn (blocking).

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

        # Check if port is available before attempting to start
        if not is_port_available(args.fastapi_host, args.fastapi_port):
            raise RuntimeError(
                f"Port {args.fastapi_port} is already in use on {args.fastapi_host}. "
                "Another daemon or process may be running on this port."
            )

        # Track host/port for cleanup
        self._last_host = args.fastapi_host
        self._last_port = args.fastapi_port

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

        # Wait for port to be fully released (OS may keep socket in TIME_WAIT)
        port_timeout = 5.0
        port_elapsed = 0.0
        while not is_port_available(self._last_host, self._last_port) and port_elapsed < port_timeout:
            await asyncio.sleep(0.1)
            port_elapsed += 0.1
        if port_elapsed > 0:
            self.logger.debug(f"Waited {port_elapsed:.1f}s for port {self._last_port} to be released")

        self.logger.info("FastAPI server stopped.")
