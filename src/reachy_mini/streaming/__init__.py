"""Shared streaming transport abstraction.

This module provides the base transport interface used by both
server-side (daemon) and client-side (SDK) streaming implementations.
"""

from reachy_mini.streaming.transport import Transport

__all__ = ["Transport"]
