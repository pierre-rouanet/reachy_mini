"""Mockup Simulation Motor Controller for Reachy Mini.

A lightweight simulation motor controller that doesn't require MuJoCo.
Uses only kinematics (no physics simulation).
"""

from reachy_mini.motor_controller.mockup_sim.controller import MockupController

__all__ = ["MockupController"]
