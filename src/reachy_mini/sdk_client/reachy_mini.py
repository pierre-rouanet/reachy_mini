"""Reachy Mini class for controlling a simulated or real Reachy Mini robot.

This class provides methods to control the head and antennas of the Reachy Mini robot,
set their target positions, and perform various behaviors such as waking up and going to sleep.

It also includes methods for multimedia interactions like playing sounds and looking at specific points in the image frame or world coordinates.
"""

import asyncio
import logging
import threading
import time
import warnings
from typing import Any, Coroutine, Dict, List, Literal, Optional, TypeVar, Union

import cv2
import numpy as np
import numpy.typing as npt
import websockets.exceptions
from scipy.spatial.transform import Rotation as R

from reachy_mini.daemon.models import MotorControlMode
from reachy_mini.daemon.streaming.messages import DaemonStatusEvent
from reachy_mini.daemon.streaming.transport import ConnectionClosedError
from reachy_mini.daemon.utils import daemon_check, is_local_camera_available
from reachy_mini.media.media_manager import MediaBackend, MediaManager
from reachy_mini.motion.move import Move
from reachy_mini.sdk_client.stream_client import StreamClient
from reachy_mini.utils.interpolation import InterpolationTechnique

T = TypeVar("T")
ConnectionMode = Literal["auto", "localhost_only", "network"]

# Behavior definitions
INIT_HEAD_POSE = np.eye(4)

SLEEP_HEAD_JOINT_POSITIONS = [
    -0.9848156658225817,
    1.2624661884298831,
    -0.24390294527381684,
    0.20555342557667577,
    -1.2363885150358267,
    1.0032234352772091,
]


SLEEP_ANTENNAS_JOINT_POSITIONS = [-3.05, 3.05]
SLEEP_HEAD_POSE = np.array(
    [
        [0.911, 0.004, 0.413, -0.021],
        [-0.004, 1.0, -0.001, 0.001],
        [-0.413, -0.001, 0.911, -0.044],
        [0.0, 0.0, 0.0, 1.0],
    ]
)


class ReachyMini:
    """Reachy Mini class for controlling a simulated or real Reachy Mini robot.

    Args:
        host: The daemon host address. If None, will try localhost then reachy-mini.local.
        spawn_daemon (bool): If True, will spawn a daemon to control the robot, defaults to False.
        use_sim (bool): If True and spawn_daemon is True, will spawn a simulated robot, defaults to True.

    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: int = 8000,
        spawn_daemon: bool = False,
        use_sim: bool = False,
        timeout: float = 5.0,
        log_level: str = "INFO",
        media_backend: str = "default",
        automatic_body_yaw: bool = True,
        # Deprecated params (kept for backward compatibility)
        robot_name: Optional[str] = None,
        connection_mode: Optional[ConnectionMode] = None,
        localhost_only: Optional[bool] = None,
    ) -> None:
        """Initialize the Reachy Mini robot.

        Args:
            host: The daemon host address. If None (default), will try localhost
                first, then reachy-mini.local.
            port: The daemon port, defaults to 8000.
            spawn_daemon (bool): If True, will spawn a daemon to control the robot, defaults to False.
            use_sim (bool): If True and spawn_daemon is True, will spawn a simulated robot, defaults to True.
            timeout (float): Timeout for the client connection, defaults to 5.0 seconds.
            log_level (str): Logging level, defaults to "INFO".
            media_backend (str): Use "no_media" to disable media entirely. Any other value
                triggers auto-detection: Lite uses OpenCV, Wireless uses GStreamer (local)
                or WebRTC (remote) based on environment.
            automatic_body_yaw (bool): If True, the body yaw is automatically computed
                during IK to stay within mechanical limits. Defaults to True.
            robot_name: Deprecated, ignored.
            connection_mode: Deprecated. Use host parameter instead.
            localhost_only: Deprecated. Use host parameter instead.

        Raises:
            ConnectionError: If unable to connect to the daemon.

        """
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        # Handle deprecated params
        if robot_name is not None:
            warnings.warn(
                "robot_name is deprecated and ignored. Use host instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        if connection_mode is not None or localhost_only is not None:
            warnings.warn(
                "connection_mode/localhost_only are deprecated. Use host instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if host is None:
                if connection_mode == "localhost_only" or localhost_only is True:
                    host = "localhost"
                elif connection_mode == "network" or localhost_only is False:
                    host = "reachy-mini.local"

        daemon_check(spawn_daemon, use_sim)

        # Background event loop for async StreamClient
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._stream_client: Optional[StreamClient] = None
        self._stop_event = threading.Event()
        self._daemon_status: Optional[DaemonStatusEvent] = (
            None  # Set by _initialize_client
        )

        self.host, self.port = self._initialize_client(host, port, timeout)
        self.set_automatic_body_yaw(automatic_body_yaw)
        self.is_recording = False

        self.T_head_cam = np.eye(4)
        self.T_head_cam[:3, 3][:] = [0.0437, 0, 0.0512]
        self.T_head_cam[:3, :3] = np.array(
            [
                [0, 0, 1],
                [-1, 0, 0],
                [0, -1, 0],
            ]
        )

        self.media_manager = self._configure_mediamanager(media_backend, log_level)

    def __del__(self) -> None:
        """Destroy the Reachy Mini instance.

        The client is disconnected explicitly to avoid a thread pending issue.

        """
        self._disconnect()

    def __enter__(self) -> "ReachyMini":
        """Context manager entry point for Reachy Mini."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore [no-untyped-def]
        """Context manager exit point for Reachy Mini."""
        self.media_manager.close()
        self._disconnect()

    def _disconnect(self) -> None:
        """Disconnect from the daemon and stop the background event loop."""
        self._stop_event.set()
        if self._stream_client and self._loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._stream_client.disconnect(), self._loop
                )
                future.result(timeout=2.0)
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=2.0)
        self._stream_client = None
        self._loop = None
        self._loop_thread = None

    def _run_async(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run an async coroutine synchronously using the background event loop.

        Args:
            coro: The coroutine to run.

        Returns:
            The result of the coroutine.

        Raises:
            ConnectionError: If not connected or connection is lost.

        """
        if self._loop is None or self._stream_client is None:
            raise ConnectionError("Not connected to daemon")
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=10.0)
        except ConnectionError:
            raise ConnectionError("Lost connection with the server.")
        except (
            OSError,
            websockets.exceptions.ConnectionClosed,
            ConnectionClosedError,
        ) as e:
            raise ConnectionError("Lost connection with the server.") from e

    @property
    def _client(self) -> StreamClient:
        """Get the stream client, raising if not connected."""
        if self._stream_client is None:
            raise ConnectionError("Not connected to daemon")
        return self._stream_client

    @property
    def media(self) -> MediaManager:
        """Expose the MediaManager instance used by ReachyMini."""
        return self.media_manager

    def get_status(self) -> DaemonStatusEvent:
        """Get daemon status.

        Returns:
            DaemonStatusEvent with state, simulation_enabled,
            error, motor_controller_status, wlan_ip, etc.

        """
        if self._daemon_status is None:
            raise RuntimeError("Daemon status not available (not connected)")
        return self._daemon_status

    @property
    def imu(self) -> Dict[str, List[float] | float] | None:
        """Get the current IMU data from the backend.

        Returns:
            dict with the following keys, or None if IMU is not available (Lite version)
            or no data received yet:
            - 'accelerometer': [x, y, z] in m/s^2
            - 'gyroscope': [x, y, z] in rad/s
            - 'quaternion': [w, x, y, z] orientation quaternion
            - 'temperature': float in °C

        Note:
            - Data is cached from the last state update
            - Quaternion is in [w, x, y, z] format

        Example:
            >>> imu_data = reachy.imu
            >>> if imu_data is not None:
            >>>     accel_x, accel_y, accel_z = imu_data['accelerometer']
            >>>     gyro_x, gyro_y, gyro_z = imu_data['gyroscope']
            >>>     quat_w, quat_x, quat_y, quat_z = imu_data['quaternion']
            >>>     temp = imu_data['temperature']

        """
        state = self._run_async(self._client.get_state())
        if state.sensors and "imu" in state.sensors:
            imu = state.sensors["imu"]
            return {
                "accelerometer": list(imu.accelerometer),
                "gyroscope": list(imu.gyroscope),
                "quaternion": list(imu.quaternion),
                "temperature": imu.temperature,
            }
        return None

    def _configure_mediamanager(
        self, media_backend: str, log_level: str
    ) -> MediaManager:
        status = self.get_status()
        is_wireless = status.wireless_version

        # If no_media is requested, skip all media initialization
        if media_backend.lower() == "no_media":
            self.logger.info("No media backend requested.")
            mbackend = MediaBackend.NO_MEDIA
        else:
            if is_wireless:
                if is_local_camera_available():
                    # Local client on CM4: use GStreamer to read from unix socket
                    # This avoids WebRTC encode/decode overhead
                    if "no_video" in media_backend.lower():
                        mbackend = MediaBackend.GSTREAMER_NO_VIDEO
                        self.logger.info(
                            "Auto-detected: Wireless + local camera socket. "
                            "Using GStreamer audio-only backend (no WebRTC overhead)."
                        )
                    else:
                        mbackend = MediaBackend.GSTREAMER
                        self.logger.info(
                            "Auto-detected: Wireless + local camera socket. "
                            "Using GStreamer backend (no WebRTC overhead)."
                        )
                else:
                    # Remote client: use WebRTC for streaming
                    self.logger.info(
                        "Auto-detected: Wireless + remote client. "
                        "Using WebRTC backend for streaming."
                    )
                    mbackend = MediaBackend.WEBRTC
            else:
                # Lite version: use specified backend if compatible
                try:
                    mbackend = MediaBackend(media_backend.lower())
                except ValueError:
                    self.logger.warning(
                        f"Invalid media backend on Lite: {media_backend}, using default backend."
                    )
                    mbackend = (
                        MediaBackend.DEFAULT_NO_VIDEO
                        if "no_video" in media_backend.lower()
                        else MediaBackend.DEFAULT
                    )

        return MediaManager(
            use_sim=bool(status.simulation_enabled),
            backend=mbackend,
            log_level=log_level,
            signalling_host=status.wlan_ip or "localhost",
        )

    def _start_event_loop(self, ready: threading.Event) -> None:
        """Start background event loop in a thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        ready.set()
        self._loop.run_forever()

    def _initialize_client(
        self, host: Optional[str], port: int, timeout: float
    ) -> tuple[str, int]:
        """Create and connect a StreamClient, with auto-discovery if host is None."""
        # Start background event loop
        loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._start_event_loop, args=(loop_ready,), daemon=True
        )
        self._loop_thread.start()
        loop_ready.wait()

        hosts_to_try = (
            [host] if host is not None else ["localhost", "reachy-mini.local"]
        )

        for try_host in hosts_to_try:
            client = StreamClient(host=try_host, port=port)
            try:
                # Connect in the background loop
                future = asyncio.run_coroutine_threadsafe(
                    client.connect(timeout=timeout), self._loop
                )
                future.result(timeout=timeout + 1.0)

                # Subscribe to state streaming with IMU sensor if available
                future = asyncio.run_coroutine_threadsafe(
                    client.subscribe(frequency=50.0, sensors=["imu"]), self._loop
                )
                future.result(timeout=5.0)

                # Get daemon status for media configuration
                status_future = asyncio.run_coroutine_threadsafe(
                    client.get_daemon_status(), self._loop
                )
                self._daemon_status = status_future.result(timeout=5.0)

                self._stream_client = client
                self.logger.info("Connected to daemon at %s:%d", try_host, port)
                return try_host, port

            except Exception as e:
                if host is not None:
                    # Explicit host was provided, fail immediately
                    self._disconnect()
                    raise ConnectionError(
                        f"Could not connect to daemon at {host}:{port}. "
                        f"Is the Reachy Mini daemon running? Error: {e}"
                    ) from e
                self.logger.info("Connection to %s:%d failed: %s", try_host, port, e)
                continue

        self._disconnect()
        raise ConnectionError(
            "Could not connect to daemon. Tried: "
            + ", ".join(f"{h}:{port}" for h in hosts_to_try)
            + ". Make sure a Reachy Mini daemon is running and accessible."
        )

    def set_target(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,  # 4x4 pose matrix
        antennas: Optional[
            Union[npt.NDArray[np.float64], List[float]]
        ] = None,  # [right_angle, left_angle] (in rads)
        body_yaw: Optional[float] = None,  # Body yaw angle in radians
    ) -> None:
        """Set the target pose of the head and/or the target position of the antennas.

        Args:
            head (Optional[np.ndarray]): 4x4 pose matrix representing the head pose.
            antennas (Optional[Union[np.ndarray, List[float]]]): 1D array with two elements representing the angles of the antennas in radians.
            body_yaw (Optional[float]): Body yaw angle in radians.

        Raises:
            ValueError: If neither head nor antennas are provided, or if the shape of head is not (4, 4), or if antennas is not a 1D array with two elements.

        """
        if head is None and antennas is None and body_yaw is None:
            raise ValueError(
                "At least one of head, antennas or body_yaw must be provided."
            )

        if head is not None and not head.shape == (4, 4):
            raise ValueError(f"Head pose must be a 4x4 matrix, got shape {head.shape}.")

        if antennas is not None and not len(antennas) == 2:
            raise ValueError(
                "Antennas must be a list or 1D np array with two elements."
            )

        if body_yaw is not None and not isinstance(body_yaw, (int, float)):
            raise ValueError("body_yaw must be a float.")

        if head is not None:
            self.set_target_head_pose(head)

        if antennas is not None:
            self.set_target_antenna_joint_positions(list(antennas))

        if body_yaw is not None:
            self.set_target_body_yaw(body_yaw)

    def goto_target(
        self,
        head: Optional[npt.NDArray[np.float64]] = None,  # 4x4 pose matrix
        antennas: Optional[
            Union[npt.NDArray[np.float64], List[float]]
        ] = None,  # [right_angle, left_angle] (in rads)
        duration: float = 0.5,  # Duration in seconds for the movement, default is 0.5 seconds.
        method: InterpolationTechnique = InterpolationTechnique.MIN_JERK,  # can be "linear", "minjerk", "ease" or "cartoon", default is "minjerk")
        body_yaw: float | None = None,  # Body yaw angle in radians
    ) -> None:
        """Go to a target head pose and/or antennas position using task space interpolation, in "duration" seconds.

        Args:
            head (Optional[np.ndarray]): 4x4 pose matrix representing the target head pose.
            antennas (Optional[Union[np.ndarray, List[float]]]): 1D array with two elements representing the angles of the antennas in radians.
            duration (float): Duration of the movement in seconds.
            method (InterpolationTechnique): Interpolation method to use ("linear", "minjerk", "ease", "cartoon"). Default is "minjerk".
            body_yaw (float | None): Body yaw angle in radians. Use None to keep the current yaw.

        Raises:
            ValueError: If neither head nor antennas are provided, or if duration is not positive.

        """
        if head is None and antennas is None and body_yaw is None:
            raise ValueError(
                "At least one of head, antennas or body_yaw must be provided."
            )

        if duration <= 0.0:
            raise ValueError(
                "Duration must be positive and non-zero. Use set_target() for immediate position setting."
            )

        # Use StreamClient's blocking goto
        self._run_async(
            self._client.goto(
                head=head,
                antennas=antennas,
                body_rotation=body_yaw,
                duration=duration,
                interpolation=method,
            )
        )

    def wake_up(self) -> None:
        """Wake up the robot - go to the initial head position and play the wake up emote and sound."""
        self.goto_target(INIT_HEAD_POSE, antennas=[0.0, 0.0], duration=2)
        time.sleep(0.1)

        # Toudoum
        self.media.play_sound("wake_up.wav")

        # Roll 20° to the left
        pose = INIT_HEAD_POSE.copy()
        pose[:3, :3] = R.from_euler("xyz", [20, 0, 0], degrees=True).as_matrix()
        self.goto_target(pose, duration=0.2)

        # Go back to the initial position
        self.goto_target(INIT_HEAD_POSE, duration=0.2)

    def goto_sleep(self) -> None:
        """Put the robot to sleep by moving the head and antennas to a predefined sleep position."""
        # Check if we are too far from the initial position
        # Move to the initial position if necessary
        current_positions, _ = self.get_current_joint_positions()
        # init_positions = self.head_kinematics.ik(INIT_HEAD_POSE)
        # Todo : get init position from the daemon?
        init_positions = [
            0.5251518455536499,
            -0.668710345667336,
            0.6067086443974802,
            -0.606711497194891,
            0.6687148024583701,
            -0.5251586523105128,
        ]
        dist = np.linalg.norm(np.array(current_positions) - np.array(init_positions))
        if dist > 0.2:
            self.goto_target(INIT_HEAD_POSE, antennas=[0.0, 0.0], duration=1)
            time.sleep(0.2)

        # Pfiou
        self.media.play_sound("go_sleep.wav")

        # # Move to the sleep position
        self.goto_target(
            SLEEP_HEAD_POSE, antennas=SLEEP_ANTENNAS_JOINT_POSITIONS, duration=2
        )

        time.sleep(2)

    def look_at_image(
        self, u: int, v: int, duration: float = 1.0, perform_movement: bool = True
    ) -> npt.NDArray[np.float64]:
        """Make the robot head look at a point defined by a pixel position (u,v).

        # TODO image of reachy mini coordinate system

        Args:
            u (int): Horizontal coordinate in image frame.
            v (int): Vertical coordinate in image frame.
            duration (float): Duration of the movement in seconds. If 0, the head will snap to the position immediately.
            perform_movement (bool): If True, perform the movement. If False, only calculate and return the pose.

        Returns:
            np.ndarray: The calculated head pose as a 4x4 matrix.

        Raises:
            ValueError: If duration is negative.

        """
        if self.media_manager.camera is None:
            raise RuntimeError("Camera is not initialized.")

        # TODO this is false for the raspicam for now
        if not (0 < u < self.media_manager.camera.resolution[0]):
            raise ValueError(
                f"u must be in [0, {self.media_manager.camera.resolution[0]}], got {u}."
            )
        if not (0 < v < self.media_manager.camera.resolution[1]):
            raise ValueError(
                f"v must be in [0, {self.media_manager.camera.resolution[1]}], got {v}."
            )

        if duration < 0:
            raise ValueError("Duration can't be negative.")

        if self.media.camera is None or self.media.camera.camera_specs is None:
            raise RuntimeError("Camera specs not set.")

        points = np.array([[[u, v]]], dtype=np.float32)
        x_n, y_n = cv2.undistortPoints(
            points,
            self.media.camera.K,  # type: ignore
            self.media.camera.D,
        )[0, 0]

        ray_cam = np.array([x_n, y_n, 1.0])
        ray_cam /= np.linalg.norm(ray_cam)

        T_world_head = self.get_current_head_pose()
        T_world_cam = T_world_head @ self.T_head_cam

        R_wc = T_world_cam[:3, :3]
        t_wc = T_world_cam[:3, 3]

        ray_world = R_wc @ ray_cam

        P_world = t_wc + ray_world

        return self.look_at_world(
            x=P_world[0],
            y=P_world[1],
            z=P_world[2],
            duration=duration,
            perform_movement=perform_movement,
        )

    def look_at_world(
        self,
        x: float,
        y: float,
        z: float,
        duration: float = 1.0,
        perform_movement: bool = True,
    ) -> npt.NDArray[np.float64]:
        """Look at a specific point in 3D space in Reachy Mini's reference frame.

        TODO include image of reachy mini coordinate system

        Args:
            x (float): X coordinate in meters.
            y (float): Y coordinate in meters.
            z (float): Z coordinate in meters.
            duration (float): Duration of the movement in seconds. If 0, the head will snap to the position immediately.
            perform_movement (bool): If True, perform the movement. If False, only calculate and return the pose.

        Returns:
            np.ndarray: The calculated head pose as a 4x4 matrix.

        Raises:
            ValueError: If duration is negative.

        """
        if duration < 0:
            raise ValueError("Duration can't be negative.")

        # Head is at the origin, so vector from head to target position is directly the target position
        # TODO FIX : Actually, the head frame is not the origin frame wrt the kinematics. Close enough for now.
        target_position = np.array([x, y, z])
        target_vector = target_position / np.linalg.norm(
            target_position
        )  # normalize the vector

        # head_pointing straight vector
        straight_head_vector = np.array([1, 0, 0])

        # Calculate the rotation needed to align the head with the target vector
        v1 = straight_head_vector
        v2 = target_vector
        axis = np.cross(v1, v2)
        axis_norm = np.linalg.norm(axis)
        if axis_norm < 1e-8:
            # Vectors are (almost) parallel
            if np.dot(v1, v2) > 0:
                rot_mat = np.eye(3)
            else:
                # Opposite direction: rotate 180° around any perpendicular axis
                perp = np.array([0, 1, 0]) if abs(v1[0]) < 0.9 else np.array([0, 0, 1])
                axis = np.cross(v1, perp)
                axis /= np.linalg.norm(axis)
                rot_mat = R.from_rotvec(np.pi * axis).as_matrix()
        else:
            axis = axis / axis_norm
            angle = np.arccos(np.clip(np.dot(v1, v2), -1.0, 1.0))
            rotation_vector = angle * axis
            rot_mat = R.from_rotvec(rotation_vector).as_matrix()

        target_head_pose = np.eye(4)
        target_head_pose[:3, :3] = rot_mat

        # If perform_movement is True, execute the movement
        if perform_movement:
            # If duration is specified, use the goto_target method to move smoothly
            # Otherwise, set the position immediately
            if duration > 0:
                self.goto_target(target_head_pose, duration=duration)
            else:
                self.set_target(target_head_pose)

        return target_head_pose

    def get_current_joint_positions(self) -> tuple[list[float], list[float]]:
        """Get the current joint positions of the head and antennas.

        Returns:
            tuple: A tuple containing two lists:
                - List of stewart platform joint positions (rad) (length 6).
                - List of antennas joint positions (rad) (length 2).

        Note:
            Body yaw is accessed separately via get_current_body_yaw().

        """
        s = self._run_async(self._client.get_state())
        if s is None:
            raise RuntimeError("Could not get current joint positions from the daemon.")
        if s.head_joints is None:
            raise RuntimeError("Head joints data is None.")
        if s.antennas is None:
            raise RuntimeError("Antennas data is None.")
        return s.head_joints, list(s.antennas)

    def get_present_antenna_joint_positions(self) -> list[float]:
        """Get the present joint positions of the antennas.

        Returns:
            list: A list of antennas joint positions (rad) (length 2).

        """
        return self.get_current_joint_positions()[1]

    def get_current_body_yaw(self) -> float:
        """Get the current body yaw angle.

        Returns:
            float: Body yaw angle in radians.

        """
        state = self._run_async(self._client.get_state())
        if state is None:
            raise RuntimeError("Could not get state from daemon.")
        if state.body_rotation is None:
            raise RuntimeError("Body yaw data is None.")
        return float(state.body_rotation)

    def get_current_head_pose(self) -> npt.NDArray[np.float64]:
        """Get the current head pose as a 4x4 matrix.

        Returns:
            np.ndarray: A 4x4 matrix representing the current head pose.

        """
        state = self._run_async(self._client.get_state())
        if state is None:
            raise RuntimeError("Could not get current head pose from the daemon.")
        head_pose = state.head_pose
        if head_pose is None:
            raise RuntimeError("Head pose data is None.")
        result: npt.NDArray[np.float64] = head_pose.to_numpy()
        return result

    def set_target_head_pose(self, pose: npt.NDArray[np.float64]) -> None:
        """Set the head pose to a specific 4x4 matrix.

        Args:
            pose (np.ndarray): A 4x4 matrix representing the desired head pose.

        Raises:
            ValueError: If the shape of the pose is not (4, 4).

        """
        if pose is None:
            raise ValueError("Pose must be provided as a 4x4 matrix.")
        if pose.shape != (4, 4):
            raise ValueError(f"Head pose should be a 4x4 matrix, got {pose.shape}.")

        self._run_async(self._client.set_target(head=pose))

    def set_target_antenna_joint_positions(self, antennas: List[float]) -> None:
        """Set the target joint positions of the antennas."""
        if len(antennas) != 2:
            raise ValueError("Antennas must have length 2.")
        self._run_async(self._client.set_target(antennas=antennas))

    def set_target_body_yaw(self, body_yaw: float) -> None:
        """Set the target body yaw.

        Args:
            body_yaw (float): The yaw angle of the body in radians.

        """
        self._run_async(self._client.set_target(body_rotation=body_yaw))

    def set_target_head_joints(self, head_joints: List[float]) -> None:
        """Set the target head joint positions (joint-space control).

        This is an alternative to set_target_head_pose() for joint-space control.
        Use this when you need direct control over the stewart platform actuators.

        Args:
            head_joints: 6 stewart platform joint positions in radians.

        Raises:
            ValueError: If head_joints does not have exactly 6 elements.

        """
        if len(head_joints) != 6:
            raise ValueError(
                f"head_joints must have 6 elements, got {len(head_joints)}"
            )
        self._run_async(self._client.set_target(head_joints=head_joints))

    def get_current_head_joints(self) -> List[float]:
        """Get the current head joint positions (stewart platform).

        Returns:
            List of 6 stewart platform joint positions in radians.

        """
        return self.get_current_joint_positions()[0]

    def start_recording(self) -> None:
        """Start recording data (client-side)."""
        self._recorded_data: List[
            Dict[str, float | List[float] | List[List[float]]]
        ] = []
        self.is_recording = True

    def stop_recording(
        self,
    ) -> Optional[List[Dict[str, float | List[float] | List[List[float]]]]]:
        """Stop recording data and return the recorded data (client-side)."""
        self.is_recording = False
        return self._recorded_data if hasattr(self, "_recorded_data") else None

    def _set_record_data(
        self, record: Dict[str, float | List[float] | List[List[float]]]
    ) -> None:
        """Store record data locally (client-side).

        Args:
            record (Dict): The record data to be logged.

        """
        if not isinstance(record, dict):
            raise ValueError("Record must be a dictionary.")

        if self.is_recording and hasattr(self, "_recorded_data"):
            self._recorded_data.append(record)

    def enable_motors(self, ids: List[str] | None = None) -> None:
        """Enable the motors.

        Args:
            ids (List[str] | None): List of motor names to enable. If None, all motors will be enabled.
                Valid names match `src/reachy_mini/assets/config/hardware_config.yaml`:
                `body_rotation`, `stewart_1` … `stewart_6`, `right_antenna`, `left_antenna`.

        """
        self._set_torque(True, ids=ids)

    def disable_motors(self, ids: List[str] | None = None) -> None:
        """Disable the motors.

        Args:
            ids (List[str] | None): List of motor names to disable. If None, all motors will be disabled.
                Valid names match `src/reachy_mini/assets/config/hardware_config.yaml`:
                `body_rotation`, `stewart_1` … `stewart_6`, `right_antenna`, `left_antenna`.

        """
        self._set_torque(False, ids=ids)

    def _set_torque(self, on: bool, ids: List[str] | None = None) -> None:
        # TODO: ids parameter not yet supported via API
        if ids is not None:
            self.logger.warning(
                "Motor IDs parameter not yet supported via API, ignoring."
            )
        mode = MotorControlMode.Enabled if on else MotorControlMode.Disabled
        self._run_async(self._client.set_mode(mode))

    def enable_gravity_compensation(self) -> None:
        """Enable gravity compensation for the head motors."""
        self._run_async(self._client.set_mode(MotorControlMode.GravityCompensation))

    def disable_gravity_compensation(self) -> None:
        """Disable gravity compensation for the head motors."""
        self._run_async(self._client.set_mode(MotorControlMode.Enabled))

    def set_automatic_body_yaw(self, enabled: bool) -> None:
        """Set the automatic body yaw.

        When enabled, the body yaw is automatically computed during IK
        to stay within mechanical limits.

        Args:
            enabled (bool): Whether to enable automatic body yaw.

        """
        self._run_async(self._client.set_automatic_body_rotation(enabled))

    def play_move(
        self,
        move: Move,
        play_frequency: float = 100.0,
        initial_goto_duration: float = 0.0,
        sound: bool = True,
    ) -> None:
        """Asynchronously play a Move.

        Args:
            move (Move): The Move object to be played.
            play_frequency (float): The frequency at which to evaluate the move (in Hz).
            initial_goto_duration (float): Duration for the initial goto to the starting position of the move (in seconds). If 0, no initial goto is performed.
            sound (bool): If True, play the sound associated with the move (if any).

        """
        if initial_goto_duration > 0.0:
            start_head_pose, start_antennas_positions, start_body_yaw = move.evaluate(
                0.0
            )
            self.goto_target(
                head=start_head_pose,
                antennas=start_antennas_positions,
                duration=initial_goto_duration,
                body_yaw=start_body_yaw,
            )

        sleep_period = 1.0 / play_frequency

        if move.sound_path is not None and sound:
            self.media_manager.play_sound(str(move.sound_path))

        t0 = time.time()
        while time.time() - t0 < move.duration:
            t = min(time.time() - t0, move.duration - 1e-2)

            head, antennas, body_yaw = move.evaluate(t)
            if head is not None:
                self.set_target_head_pose(head)
            if body_yaw is not None:
                self.set_target_body_yaw(body_yaw)
            if antennas is not None:
                self.set_target_antenna_joint_positions(list(antennas))

            elapsed = time.time() - t0 - t
            remaining = sleep_period - elapsed
            if remaining > 0:
                time.sleep(remaining)
