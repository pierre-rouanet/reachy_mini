"""Streaming session management.

A StreamingSession manages one client connection, handling:
- Message receive loop
- State streaming at configured frequency
- Goto tracking and completion events
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Coroutine

from reachy_mini.daemon.streaming.messages import (
    ErrorEvent,
    GotoDoneEvent,
    GotoStartedEvent,
    OutboundMessage,
    StateEvent,
    SubscribeCommand,
    parse_inbound_message,
)
from reachy_mini.daemon.streaming.transport import StreamingTransport
from reachy_mini.motion import MoveId

if TYPE_CHECKING:
    from reachy_mini.daemon.streaming.handler import ProtocolHandler


logger = logging.getLogger(__name__)

# Maximum streaming frequency (Hz)
MAX_STREAMING_FREQUENCY = 100.0


class StreamingSession:
    """Manages one streaming client connection.

    The session handles:
    - Receiving and dispatching commands to the protocol handler
    - State streaming at the configured frequency
    - Tracking goto operations and sending completion events

    A session is tied to a single transport (WebSocket or WebRTC data channel).
    """

    def __init__(
        self,
        transport: StreamingTransport,
        handler: ProtocolHandler,
    ) -> None:
        """Initialize a streaming session.

        Args:
            transport: The transport for sending/receiving messages.
            handler: The protocol handler for processing commands.

        """
        self._transport = transport
        self._handler = handler

        # State streaming
        self._subscribe_config: SubscribeCommand | None = None
        self._state_task: asyncio.Task[None] | None = None
        self._state_stop_event = asyncio.Event()

        # Goto tracking
        self._pending_gotos: dict[MoveId, asyncio.Task[None]] = {}

        # Session state
        self._running = False
        self._closed = False
        self._close_event = asyncio.Event()

    @property
    def transport(self) -> StreamingTransport:
        """Get the underlying transport."""
        return self._transport

    @property
    def handler(self) -> ProtocolHandler:
        """Get the protocol handler."""
        return self._handler

    async def run(self) -> None:
        """Run the session until the connection closes.

        This sets up the message callback and waits for the connection to close.
        """
        if self._running:
            raise RuntimeError("Session is already running")

        self._running = True
        self._transport.on_message(self._on_message)
        self._transport.on_close(self._on_close)

        try:
            # Wait until transport signals close
            await self._close_event.wait()
        finally:
            await self._cleanup()

    async def send_event(self, event: OutboundMessage) -> None:
        """Send an event to the client.

        Args:
            event: The event to send.

        """
        if self._closed:
            return

        try:
            await self._transport.send(event.model_dump_json())
        except Exception as e:
            logger.debug(f"Failed to send event: {e}")

    async def start_state_stream(self, config: SubscribeCommand) -> None:
        """Start or reconfigure state streaming.

        Args:
            config: The subscribe configuration.

        """
        # Stop existing stream if any
        await self._stop_state_stream()

        self._subscribe_config = config
        self._state_stop_event.clear()
        self._state_task = asyncio.create_task(self._state_stream_loop())

    async def start_goto(
        self,
        coro: Coroutine[Any, Any, None],
        move_id: MoveId | None,
    ) -> None:
        """Start a goto operation.

        For async goto (with move_id): sends GotoStartedEvent immediately,
        then GotoDoneEvent when complete.

        For blocking goto (move_id=None): waits for completion, then sends
        GotoDoneEvent.

        Args:
            coro: The goto coroutine to execute.
            move_id: Optional client-provided move ID for async mode.

        """
        motion = self._handler.motion_manager

        # Create the move task (may raise DuplicateMoveIdError)
        actual_id, task = motion.create_move_task(coro, move_id=move_id)

        if move_id is not None:
            # Async mode: send started event, track completion
            await self.send_event(GotoStartedEvent(id=actual_id))
            self._pending_gotos[actual_id] = task
            asyncio.create_task(self._track_goto_completion(actual_id, task))
        else:
            # Blocking mode: wait for completion
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            await self.send_event(GotoDoneEvent(status=motion.task_status(task)))

    async def cancel_goto(self, move_id: MoveId) -> bool:
        """Cancel a goto operation.

        Args:
            move_id: The move ID to cancel.

        Returns:
            True if the move was found and cancelled, False otherwise.

        """
        motion = self._handler.motion_manager

        if motion.is_move_in_progress(move_id):
            await motion.stop_move_task(move_id)
            return True
        return False

    async def _on_message(self, raw: str) -> None:
        """Handle incoming message from transport."""
        try:
            cmd = parse_inbound_message(raw)
        except Exception as e:
            await self.send_event(
                ErrorEvent(message=f"Invalid command: {e}", code="INVALID_COMMAND")
            )
            return

        try:
            await self._handler.handle_command(cmd, self)
        except Exception as e:
            logger.exception(f"Error handling command: {e}")
            await self.send_event(
                ErrorEvent(message=f"Internal error: {e}", code="INTERNAL_ERROR")
            )

    async def _on_close(self) -> None:
        """Handle transport close."""
        self._closed = True
        self._close_event.set()

    async def _state_stream_loop(self) -> None:
        """Stream state events at configured frequency."""
        if self._subscribe_config is None:
            return

        frequency = min(self._subscribe_config.frequency, MAX_STREAMING_FREQUENCY)
        period = 1.0 / frequency

        while not self._state_stop_event.is_set():
            try:
                state = await self._handler.build_state(
                    self._subscribe_config.fields,
                    self._subscribe_config.sensors,
                )
                await self.send_event(StateEvent(state=state))
                await asyncio.sleep(period)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"State stream error: {e}")
                break

    async def _track_goto_completion(
        self, move_id: MoveId, task: asyncio.Task[None]
    ) -> None:
        """Track a goto task and send completion event."""
        motion = self._handler.motion_manager
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            await self.send_event(
                GotoDoneEvent(id=move_id, status=motion.task_status(task))
            )
            self._pending_gotos.pop(move_id, None)

    async def _stop_state_stream(self) -> None:
        """Stop the state streaming task if running."""
        if self._state_task is not None:
            self._state_stop_event.set()
            self._state_task.cancel()
            try:
                await self._state_task
            except asyncio.CancelledError:
                pass
            self._state_task = None

    async def _cleanup(self) -> None:
        """Clean up session resources."""
        self._closed = True
        self._running = False

        # Stop state streaming
        await self._stop_state_stream()

        # Cancel pending goto tracking (not the gotos themselves)
        for task in list(self._pending_gotos.values()):
            task.cancel()
        self._pending_gotos.clear()

        # Close transport
        try:
            await self._transport.close()
        except Exception:
            pass
