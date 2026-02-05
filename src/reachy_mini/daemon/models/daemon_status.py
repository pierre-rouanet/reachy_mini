"""Daemon status model.

This model is shared between HTTP API and streaming protocol.
"""

from pydantic import BaseModel


class DaemonStatus(BaseModel):
    """Daemon status.

    This model represents the complete daemon status and is used by both
    HTTP endpoints and the streaming protocol.

    Attributes:
        robot_name: Name of the robot.
        state: Current daemon state (not_initialized, starting, running, stopping, stopped, error).
        wireless_version: Whether running on wireless hardware.
        desktop_app_daemon: Whether running as desktop app daemon.
        simulation_enabled: Whether simulation is enabled.
        mockup_sim_enabled: Whether mockup simulation is enabled.
        motor_controller_status: Motor controller status as a dict (or None if not available).
        error: Error message if any.
        wlan_ip: WLAN IP address if available.
        version: Daemon version.
    """

    robot_name: str
    state: str
    wireless_version: bool
    desktop_app_daemon: bool
    simulation_enabled: bool | None = None
    mockup_sim_enabled: bool | None = None
    motor_controller_status: dict | None = None
    error: str | None = None
    wlan_ip: str | None = None
    version: str | None = None
