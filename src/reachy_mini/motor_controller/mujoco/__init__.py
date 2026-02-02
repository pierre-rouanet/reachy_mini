"""MuJoCo Motor Controller for Reachy Mini."""

try:
    import mujoco  # noqa: F401

    from reachy_mini.motor_controller.mujoco.controller import MujocoController

except ImportError:

    class MujocoControllerMockup:
        """Mockup class to avoid import errors when MuJoCo is not installed."""

        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            """Raise ImportError when trying to instantiate the class."""
            raise ImportError(
                "MuJoCo is not installed. MuJoCo controller is not available."
                " To use MuJoCo controller, please install the 'mujoco' extra dependencies"
                " with 'pip install reachy_mini[mujoco]'."
            )

    MujocoController = MujocoControllerMockup  # type: ignore[assignment, misc]

__all__ = ["MujocoController"]
