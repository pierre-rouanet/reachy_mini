"""Mujoco Backend for Reachy Mini.

This module provides the MujocoBackend class for simulating the Reachy Mini robot using the MuJoCo physics engine.

It includes methods for running the simulation, getting joint positions, and controlling the robot's joints.

"""

import time
from importlib.resources import files
from threading import Thread
from typing import Annotated, Any, Optional

import cv2
import mujoco
import mujoco.viewer
import numpy as np
import numpy.typing as npt

import reachy_mini
from reachy_mini.io.video_ws import AsyncWebSocketFrameSender

from ..abstract import Backend, MotorControlMode
from .utils import (
    get_actuator_names,
    get_joint_addr_from_name,
    get_joint_id_from_name,
)
from .video_udp import UDPJPEGFrameSender

CAMERA_REACHY = "eye_camera"
CAMERA_STUDIO_CLOSE = "studio_close"
CAMERA_SIZES = {CAMERA_REACHY: (1280, 720), CAMERA_STUDIO_CLOSE: (640, 640)}


class MujocoBackend(Backend):
    """Simulated Reachy Mini using MuJoCo."""

    # MuJoCo runs at higher frequency internally, decimated for control loop
    control_frequency: float = 50.0  # Control loop frequency
    _sim_frequency: float = 500.0  # Internal simulation frequency

    def __init__(
        self,
        scene: str = "empty",
        check_collision: bool = False,
        kinematics_engine: str = "AnalyticalKinematics",
        headless: bool = False,
        use_audio: bool = False,
        websocket_uri: Optional[str] = None,
    ) -> None:
        """Initialize the MujocoBackend with a specified scene.

        Args:
            scene (str): The name of the scene to load. Default is "empty".
            check_collision (bool): If True, enable collision checking. Default is False.
            kinematics_engine (str): Kinematics engine to use. Defaults to "AnalyticalKinematics".
            headless (bool): If True, run Mujoco in headless mode (no GUI). Default is False.
            use_audio (bool): If True, use audio. Default is False.
            websocket_uri (Optional[str]): If set, allow streaming of the robot view through a WebSocket connection to the specified uri. Defaults to None.

        """
        super().__init__(
            check_collision=check_collision,
            kinematics_engine=kinematics_engine,
            use_audio=use_audio,
        )

        self.headless = headless
        self.websocket_uri = websocket_uri

        from reachy_mini.reachy_mini import (
            SLEEP_ANTENNAS_JOINT_POSITIONS,
            SLEEP_HEAD_JOINT_POSITIONS,
        )

        # Real robot convention for the order of the antennas joints is [right, left], but in mujoco it's [left, right]
        self._SLEEP_ANTENNAS_JOINT_POSITIONS = [
            SLEEP_ANTENNAS_JOINT_POSITIONS[1],
            SLEEP_ANTENNAS_JOINT_POSITIONS[0],
        ]
        self._SLEEP_HEAD_JOINT_POSITIONS = SLEEP_HEAD_JOINT_POSITIONS

        mjcf_root_path = str(
            files(reachy_mini).joinpath("descriptions/reachy_mini/mjcf/")
        )
        self.model = mujoco.MjModel.from_xml_path(
            f"{mjcf_root_path}/scenes/{scene}.xml"
        )
        self.data = mujoco.MjData(self.model)
        self.model.opt.timestep = 1.0 / self._sim_frequency

        self.decimation = int(self._sim_frequency / self.control_frequency)
        self.rendering_timestep = 0.04  # s, rendering loop # 25Hz
        self.streaming_timestep = 0.04  # s, streaming loop # 25Hz

        self.head_site_id = mujoco.mj_name2id(
            self.model,
            mujoco.mjtObj.mjOBJ_SITE,
            "head",
        )

        self.current_head_pose = np.eye(4)

        self.joint_names = get_actuator_names(self.model)

        self.joint_ids = [
            get_joint_id_from_name(self.model, n) for n in self.joint_names
        ]
        self.joint_qpos_addr = [
            get_joint_addr_from_name(self.model, n) for n in self.joint_names
        ]

        # Disable collisions at the beginning for smoother initialization
        self.col_inds = []
        for i, type in enumerate(self.model.geom_contype):
            if type != 0:
                geom_name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, i)
                # monkey-patch: the geoms in the minimal scene are named (duck_geom, table_top_collision ... ).
                # We don't disable the collision for them so that the objects don't fall to the ground at initialization
                if geom_name is None:
                    self.col_inds.append(i)
                    self.model.geom_contype[i] = 0
                    self.model.geom_conaffinity[i] = 0

        # Viewer and threads (initialized in _on_start)
        self._viewer: Any = None
        self._rendering_thread: Optional[Thread] = None
        self._streaming_thread: Optional[Thread] = None
        self._step_count = 0

    def _get_camera_id(self, camera_name: str) -> Any:
        """Get the id of the virtual camera."""
        return mujoco.mj_name2id(
            self.model,
            mujoco.mjtObj.mjOBJ_CAMERA,
            camera_name,
        )

    def _get_renderer(self, camera_name: str) -> mujoco.Renderer:
        """Get the renderer for the virtual camera."""
        camera_size = CAMERA_SIZES[camera_name]
        return mujoco.Renderer(self.model, height=camera_size[1], width=camera_size[0])

    def _streaming_loop(self, camera_name: str, ws_uri: str) -> None:
        """Streaming loop for the Mujoco simulation over WebSocket."""
        streamer = AsyncWebSocketFrameSender(ws_uri=ws_uri + "/video_stream")
        offscreen_renderer = self._get_renderer(camera_name)
        camera_id = self._get_camera_id(camera_name)

        while not self.should_stop.is_set():
            start_t = time.time()
            offscreen_renderer.update_scene(self.data, camera_id)

            # OPTIMIZATION: Disable expensive rendering effects
            offscreen_renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 0
            offscreen_renderer.scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 0

            im = offscreen_renderer.render()
            im = cv2.cvtColor(im, cv2.COLOR_RGB2BGR)
            streamer.send_frame(im)

            took = time.time() - start_t
            time.sleep(max(0, self.streaming_timestep - took))

    def _rendering_loop(self, camera_name: str, port: int) -> None:
        """Offline Rendering loop for the Mujoco simulation."""
        streamer = UDPJPEGFrameSender(dest_port=port)
        offscreen_renderer = self._get_renderer(camera_name)
        camera_id = self._get_camera_id(camera_name)

        while not self.should_stop.is_set():
            start_t = time.time()
            offscreen_renderer.update_scene(self.data, camera_id)

            im = offscreen_renderer.render()
            streamer.send_frame(im)

            took = time.time() - start_t
            time.sleep(max(0, self.rendering_timestep - took))

    # Template method hooks

    def _on_start(self) -> None:
        """Initialize viewer, threads, and simulation state."""
        # Start WebSocket streaming thread if configured
        if self.websocket_uri:
            self._streaming_thread = Thread(
                target=self._streaming_loop,
                args=(CAMERA_STUDIO_CLOSE, self.websocket_uri),
                daemon=True,
            )
            self._streaming_thread.start()

        # Start viewer if not headless
        if not self.headless:
            self._viewer = mujoco.viewer.launch_passive(
                self.model, self.data, show_left_ui=False, show_right_ui=False
            )
            with self._viewer.lock():
                self._viewer.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
                self._viewer.cam.distance = 0.8
                self._viewer.cam.azimuth = 160
                self._viewer.cam.elevation = -20
                self._viewer.cam.lookat[:] = [0, 0, 0.15]

                mujoco.mj_step(self.model, self.data)
                self._viewer.sync()

        # Set initial positions
        self.data.qpos[self.joint_qpos_addr] = np.array(
            self._SLEEP_HEAD_JOINT_POSITIONS + self._SLEEP_ANTENNAS_JOINT_POSITIONS
        ).reshape(-1, 1)
        self.data.ctrl[:] = np.array(
            self._SLEEP_HEAD_JOINT_POSITIONS + self._SLEEP_ANTENNAS_JOINT_POSITIONS
        )

        # Initialize simulation
        mujoco.mj_forward(self.model, self.data)
        for _ in range(100):
            mujoco.mj_step(self.model, self.data)

        # Enable collisions
        for i in self.col_inds:
            self.model.geom_contype[i] = 1
            self.model.geom_conaffinity[i] = 1

        for _ in range(100):
            mujoco.mj_step(self.model, self.data)

        mujoco.mj_step(self.model, self.data)
        if not self.headless:
            self._viewer.sync()
            self._rendering_thread = Thread(
                target=self._rendering_loop, args=(CAMERA_REACHY, 5005), daemon=True
            )
            self._rendering_thread.start()

        # Initialize kinematics state
        self.head_kinematics.ik(self._get_mj_head_pose(), no_iterations=20)
        head_pos, _ = self._read_joint_positions()
        self.head_kinematics.fk(head_pos, no_iterations=20)

    def _on_update(self) -> None:
        """Step physics simulation and sync viewer."""
        # Apply controls and step simulation
        if self.target_head_joint_positions is not None:
            self.data.ctrl[:7] = self.target_head_joint_positions
        if self.target_antenna_joint_positions is not None:
            self.data.ctrl[-2:] = -self.target_antenna_joint_positions

        # Step physics multiple times (decimation)
        for _ in range(self.decimation):
            mujoco.mj_step(self.model, self.data)

        # Sync viewer
        if not self.headless and self._viewer is not None:
            self._viewer.sync()

    def _on_stop(self) -> None:
        """Close viewer and join threads."""
        if not self.headless and self._viewer is not None:
            self._viewer.close()
            if self._rendering_thread is not None:
                self._rendering_thread.join()
        if self._streaming_thread is not None:
            self._streaming_thread.join()

    # Abstract method implementations

    def _read_joint_positions(
        self,
    ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        """Read joint positions from MuJoCo simulation."""
        head_pos: npt.NDArray[np.float64] = self.data.qpos[
            self.joint_qpos_addr[:7]
        ].flatten()
        antenna_pos: npt.NDArray[np.float64] = -self.data.qpos[
            self.joint_qpos_addr[-2:]
        ].flatten()
        return head_pos, antenna_pos

    def _apply_targets(self) -> None:
        """Targets are applied in _on_update along with physics step."""
        # MuJoCo applies targets through data.ctrl in _on_update
        pass

    def _get_mj_head_pose(self) -> Annotated[npt.NDArray[np.float64], (4, 4)]:
        """Get the current head pose from the Mujoco simulation."""
        pose = np.eye(4)
        pose[:3, :3] = self.data.site_xmat[self.head_site_id].reshape(3, 3)
        pose[:3, 3] = self.data.site_xpos[self.head_site_id]
        pose[2, 3] -= 0.177
        return pose

    def get_motor_control_mode(self) -> MotorControlMode:
        """Get the motor control mode."""
        return self._status.motor_control_mode

    def set_motor_control_mode(self, mode: MotorControlMode) -> None:
        """Set the motor control mode (simulation always reports Enabled)."""
        self._status.motor_control_mode = mode

    def set_motor_torque_ids(self, ids: list[str], on: bool) -> None:
        """Set the motor torque state for specific motor names (no-op in simulation)."""
        pass
