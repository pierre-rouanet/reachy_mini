"""Move task tracking and management.

This module provides centralized tracking of async move operations (goto, play, etc.).
It is used by both the HTTP API and streaming protocols to manage move lifecycle.
"""

import asyncio
from enum import Enum
from typing import Any, Coroutine
from uuid import uuid4

# Type alias for move identifiers
MoveId = str


class MoveStatus(str, Enum):
    """Status of a movement operation."""

    InProgress = "in_progress"
    Completed = "completed"
    Failed = "failed"
    Cancelled = "cancelled"
    NotFound = "not_found"


class DuplicateMoveIdError(Exception):
    """Raised when a move ID is already in use."""

    def __init__(self, move_id: MoveId) -> None:
        """Initialize with the duplicate move ID."""
        self.move_id = move_id
        super().__init__(f"Move ID '{move_id}' is already in use")


class MoveTracker:
    """Tracks async move operations (goto, play, etc.).

    This class manages the lifecycle of move tasks:
    - Creating and tracking new moves
    - Querying move status
    - Cancelling in-progress moves
    - Caching completed move statuses for polling

    Typically owned by the Daemon and shared with ProtocolHandler and HTTP routes.
    """

    def __init__(self, max_completed_cache: int = 100) -> None:
        """Initialize the move tracker.

        Args:
            max_completed_cache: Maximum number of completed moves to cache.

        """
        self._tasks: dict[MoveId, asyncio.Task[None]] = {}
        self._completed: dict[MoveId, MoveStatus] = {}
        self._max_completed_cache = max_completed_cache

    def get_running_moves(self) -> list[MoveId]:
        """Get list of currently running move IDs."""
        return list(self._tasks.keys())

    def is_move_in_progress(self, move_id: MoveId) -> bool:
        """Check if a move ID is currently in progress."""
        return move_id in self._tasks

    def get_move_status(self, move_id: MoveId) -> MoveStatus:
        """Get the status of a move.

        Args:
            move_id: The move ID to check.

        Returns:
            MoveStatus indicating current state.

        """
        if move_id in self._tasks:
            return MoveStatus.InProgress
        if move_id in self._completed:
            return self._completed[move_id]
        return MoveStatus.NotFound

    def get_move_task(self, move_id: MoveId) -> asyncio.Task[None] | None:
        """Get the task for a move ID, if it exists."""
        return self._tasks.get(move_id)

    def get_completed_status(self, move_id: MoveId) -> MoveStatus | None:
        """Get the cached completion status for a move.

        Args:
            move_id: The move ID to check.

        Returns:
            The cached MoveStatus, or None if not in cache.

        """
        return self._completed.get(move_id)

    def create_move_task(
        self,
        coro: Coroutine[Any, Any, None],
        move_id: MoveId | None = None,
    ) -> MoveId:
        """Create and track a new move task.

        Args:
            coro: The coroutine to run as the move task.
            move_id: Optional client-provided move ID. If None, a UUID is generated.

        Returns:
            The move ID (generated or provided).

        Raises:
            DuplicateMoveIdError: If the provided move_id is already in use.

        """
        if move_id is None:
            move_id = str(uuid4())
        elif self.is_move_in_progress(move_id):
            raise DuplicateMoveIdError(move_id)

        # Capture move_id in closure
        captured_move_id = move_id

        async def wrap_coro() -> None:
            status = MoveStatus.Completed
            try:
                await coro
            except asyncio.CancelledError:
                status = MoveStatus.Cancelled
            except Exception:
                status = MoveStatus.Failed
            finally:
                self._tasks.pop(captured_move_id, None)
                self._cache_completed(captured_move_id, status)

        task = asyncio.create_task(wrap_coro())
        self._tasks[move_id] = task

        return move_id

    async def stop_move_task(self, move_id: MoveId) -> MoveStatus:
        """Stop a running move task by cancelling it.

        Args:
            move_id: The move ID to stop.

        Returns:
            MoveStatus.Cancelled if stopped, MoveStatus.NotFound if not found.

        """
        if move_id not in self._tasks:
            return MoveStatus.NotFound

        task = self._tasks.pop(move_id, None)
        if task is not None and task.cancel():
            try:
                await task
            except asyncio.CancelledError:
                pass

        return MoveStatus.Cancelled

    def _cache_completed(self, move_id: MoveId, status: MoveStatus) -> None:
        """Cache a completed move status, evicting oldest if at capacity."""
        if len(self._completed) >= self._max_completed_cache:
            # Remove oldest entry (first key)
            oldest = next(iter(self._completed))
            self._completed.pop(oldest)
        self._completed[move_id] = status
