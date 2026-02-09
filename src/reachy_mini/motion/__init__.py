"""Motion module for Reachy Mini.

This module contains both utilities to create and play moves, as well as utilities to download datasets of recorded moves.
"""

from .move_tracker import DuplicateMoveIdError, MoveId, MoveStatus, MoveTracker

__all__ = [
    "DuplicateMoveIdError",
    "MoveId",
    "MoveStatus",
    "MoveTracker",
]
