"""Pytest configuration and shared fixtures."""

import socket


def is_port_in_use(port: int, host: str = "localhost") -> bool:
    """Check if a port is already in use.

    Utility function for tests that need to check port availability.
    Note: The Daemon itself will fail fast with a clear error if the port
    is already in use, so this is mainly for diagnostic purposes.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True
