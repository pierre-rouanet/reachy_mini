"""Reachy Mini SDK."""

from importlib.metadata import version

from reachy_mini.apps.app import ReachyMiniApp  # noqa: F401
from reachy_mini.sdk_client.reachy_mini import ReachyMini  # noqa: F401

__version__ = version("reachy_mini")
