"""Motion module for Reachy Mini.

This module contains both utilities to create and play moves, as well as utilities to download datasets of recorded moves.
"""

from .manager import DuplicateMoveIdError, MoveId, MoveStatus

__all__ = [
    "DuplicateMoveIdError",
    "MoveId",
    "MoveStatus",
]
