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
    def read_state_only(self) -> np.ndarray: ...
    def send_targets(self, target14: np.ndarray) -> None: ...
    def collect_send_errors(self) -> list[str]: ...
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

    def __init__(
        self,
        cameras: dict[str, CameraSpec],
        seed: int = 0,
        initial_state: np.ndarray | None = None,
        follow_gain: float = 0.5,
    ) -> None:
        self.cameras = cameras
        self.rng = np.random.default_rng(seed)
        self._t = 0
        self._state = (
            np.asarray(initial_state, dtype=np.float32).copy()
            if initial_state is not None
            else np.zeros(STATE_DIM, dtype=np.float32)
        )
        if initial_state is None:
            self._state[6] = self._state[13] = 1.0
        self._target: np.ndarray | None = None
        self.follow_gain = follow_gain
        self.sent: list[np.ndarray] = []

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
        return Observation(
            top=imgs["top"],
            left=imgs["left"],
            right=imgs["right"],
            state=self.read_state_only(),
            timestamp=time.time(),
        )

    def read_state_only(self) -> np.ndarray:
        """Simulated PD follower: the state moves a fraction of the way toward the last target."""
        if self._target is not None:
            self._state = self._state + self.follow_gain * (self._target - self._state)
        return self._state.astype(np.float32).copy()

    def send_targets(self, target14: np.ndarray) -> None:
        t = np.asarray(target14, dtype=np.float32).reshape(STATE_DIM)
        self._target = t.copy()
        self.sent.append(t.copy())

    def collect_send_errors(self) -> list[str]:
        return []

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
        with_cameras: bool = True,
    ) -> None:
        self.camera_specs = cameras
        # Modes that only move or read the arms must not depend on the cameras: a hung camera
        # driver would otherwise block the return to the start pose.
        self.with_cameras = with_cameras
        self.left_port = left_port
        self.right_port = right_port
        self.host = host
        self._cams: dict[str, Any] = {}
        self._left: Any = None
        self._right: Any = None
        self._pending: list[Any] = []

    def connect(self) -> None:
        from lerobot.robots.bi_yam_follower.bi_yam_follower import YamArmClient

        if self.with_cameras:
            from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig

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
        if not self.with_cameras:
            raise RuntimeError("this source was opened without cameras")
        # Cameras first (slow), arm state last so it is as fresh as possible for the policy.
        imgs = {role: np.asarray(cam.read()) for role, cam in self._cams.items()}
        return Observation(
            top=imgs["top"],
            left=imgs["left"],
            right=imgs["right"],
            state=self.read_state_only(),
            timestamp=time.time(),
        )

    def read_state_only(self) -> np.ndarray:
        left7 = arm_obs_to_state7(self._left.get_observations())
        right7 = arm_obs_to_state7(self._right.get_observations())
        return combine_state(left7, right7)

    def send_targets(self, target14: np.ndarray) -> None:
        """Send absolute joint targets (7 per arm, gripper 0..1) to both follower servers.

        ``YamArmClient.command_joint_pos`` is fire-and-forget; the portal futures are kept so
        transport errors surface through ``collect_send_errors``.
        """
        t = np.asarray(target14, dtype=np.float32).reshape(STATE_DIM)
        for arm, sl in ((self._left, slice(0, 7)), (self._right, slice(7, 14))):
            client = getattr(arm, "_client", None)
            if client is None:
                raise RuntimeError("arm client not connected")
            fut = client.command_joint_pos(np.ascontiguousarray(t[sl]))
            self._pending.append(fut)
        if len(self._pending) > 64:
            self._pending = self._pending[-64:]

    def collect_send_errors(self) -> list[str]:
        """Resolve finished command futures and return any error messages."""
        errors: list[str] = []
        keep: list[Any] = []
        for fut in self._pending:
            done = getattr(fut, "done", None)
            if done is not None and not done():
                keep.append(fut)
                continue
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - report, never raise from the control loop
                errors.append(f"{type(exc).__name__}: {exc}")
        self._pending = keep
        return errors

    def close(self) -> None:
        for cam in self._cams.values():
            try:
                cam.disconnect()
            except Exception:
                pass
        for arm in (self._left, self._right):
            if arm is not None:
                arm.disconnect()
