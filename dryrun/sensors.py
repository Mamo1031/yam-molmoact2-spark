"""Observation sources: RealSense cameras and YAM follower servers, or synthetic fakes.

The real sources import LeRobot lazily so this module can be unit-tested on a machine
without cameras, CAN adapters or the ``lerobot`` package. State is read from the
follower servers started by ``setup_bi_yam_servers --eval`` (portal RPC) rather than
by opening the CAN bus directly, so no gravity-compensation loop is started here.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import numpy as np

from dryrun import STATE_DIM
from dryrun.backends import Observation
from dryrun.config import CAMERA_ROLES, CameraSpec


class ObservationSource(Protocol):
    def connect(self) -> None: ...
    def read(self) -> Observation: ...
    def close(self) -> None: ...


def arm_obs_to_state7(arm_obs: dict[str, Any]) -> np.ndarray:
    """Build the 7-vector (6 joints + gripper) from an i2rt ``get_observations()`` dict."""
    joints = np.asarray(arm_obs["joint_pos"], dtype=np.float32).reshape(-1)[:6]
    if joints.shape[0] != 6:
        raise ValueError(f"expected 6 joint positions, got {joints.shape[0]}")
    gripper = np.asarray(arm_obs.get("gripper_pos", [0.0]), dtype=np.float32).reshape(-1)[:1]
    return np.concatenate([joints, gripper])


def combine_state(left7: np.ndarray, right7: np.ndarray) -> np.ndarray:
    """Left arm first, then right arm: matches BiYamFollower and the official BimanualRobot."""
    state: np.ndarray = np.concatenate([left7, right7]).astype(np.float32)
    if state.shape != (STATE_DIM,):
        raise ValueError(f"combined state has shape {state.shape}, expected ({STATE_DIM},)")
    return state


class FakeSource:
    """Synthetic images and a slowly drifting state, for smoke tests without hardware."""

    def __init__(self, cameras: dict[str, CameraSpec], seed: int = 0) -> None:
        self.cameras = cameras
        self.rng = np.random.default_rng(seed)
        self._t = 0

    def connect(self) -> None:
        return None

    def read(self) -> Observation:
        self._t += 1
        imgs = {}
        for role in CAMERA_ROLES:
            spec = self.cameras[role]
            imgs[role] = self.rng.integers(
                0, 255, size=(spec.height, spec.width, 3), dtype=np.uint8
            )
        state = np.zeros(STATE_DIM, dtype=np.float32)
        state[6] = state[13] = 1.0
        state[:6] += 0.01 * self._t
        return Observation(
            top=imgs["top"],
            left=imgs["left"],
            right=imgs["right"],
            state=state,
            timestamp=time.time(),
        )

    def close(self) -> None:
        return None


class LeRobotSource:
    """Real cameras via LeRobot's RealSense wrapper and real state via the YAM follower servers."""

    def __init__(
        self,
        cameras: dict[str, CameraSpec],
        *,
        left_port: int,
        right_port: int,
        host: str = "localhost",
    ) -> None:
        self.camera_specs = cameras
        self.left_port = left_port
        self.right_port = right_port
        self.host = host
        self._cams: dict[str, Any] = {}
        self._left: Any = None
        self._right: Any = None

    def connect(self) -> None:
        from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig
        from lerobot.robots.bi_yam_follower.bi_yam_follower import YamArmClient

        for role in CAMERA_ROLES:
            spec = self.camera_specs[role]
            cfg = RealSenseCameraConfig(
                serial_number_or_name=spec.serial,
                width=spec.width,
                height=spec.height,
                fps=spec.fps,
            )
            cam = RealSenseCamera(cfg)
            cam.connect()
            self._cams[role] = cam
        self._left = YamArmClient(port=self.left_port, host=self.host)
        self._right = YamArmClient(port=self.right_port, host=self.host)
        self._left.connect()
        self._right.connect()

    def read(self) -> Observation:
        imgs = {role: np.asarray(cam.read()) for role, cam in self._cams.items()}
        left7 = arm_obs_to_state7(self._left.get_observations())
        right7 = arm_obs_to_state7(self._right.get_observations())
        return Observation(
            top=imgs["top"],
            left=imgs["left"],
            right=imgs["right"],
            state=combine_state(left7, right7),
            timestamp=time.time(),
        )

    def close(self) -> None:
        for cam in self._cams.values():
            try:
                cam.disconnect()
            except Exception:
                pass
        for arm in (self._left, self._right):
            if arm is not None:
                arm.disconnect()
