"""YAML configuration for the dry run (camera serials, arm server ports, policy server, limits)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CAMERA_ROLES = ("top", "left", "right")
"""Order the model expects: [top, left, right] (HF model card)."""


@dataclass
class CameraSpec:
    serial: str
    width: int = 640
    height: int = 360
    fps: int = 30


@dataclass
class DryRunConfig:
    cameras: dict[str, CameraSpec]
    left_arm_port: int = 1235
    right_arm_port: int = 1234
    arm_host: str = "localhost"
    server_url: str = "http://127.0.0.1:8202"
    instruction: str = "Pick up the object and place it on the side."
    normalization_tag: str | None = "yam_dual_molmoact2"
    num_steps: int | None = None
    max_first_step_delta: float = 0.5
    max_step_delta: float = 0.2
    joint_limits: dict[str, tuple[float, float]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> DryRunConfig:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        cams_raw = raw.get("cameras", {})
        missing = [r for r in CAMERA_ROLES if r not in cams_raw]
        if missing:
            raise ValueError(f"cameras.yaml is missing roles: {missing}")
        cameras = {
            role: CameraSpec(
                serial=str(cams_raw[role]["serial"]),
                width=int(cams_raw[role].get("width", 640)),
                height=int(cams_raw[role].get("height", 360)),
                fps=int(cams_raw[role].get("fps", 30)),
            )
            for role in CAMERA_ROLES
        }
        for role, spec in cameras.items():
            if spec.serial.startswith("REPLACE"):
                raise ValueError(f"camera '{role}' still has a placeholder serial: {spec.serial}")
        limits_raw = raw.get("joint_limits", {}) or {}
        limits = {k: (float(v[0]), float(v[1])) for k, v in limits_raw.items()}
        arms = raw.get("arms", {}) or {}
        policy = raw.get("policy", {}) or {}
        checks = raw.get("checks", {}) or {}
        return cls(
            cameras=cameras,
            left_arm_port=int(arms.get("left_port", 1235)),
            right_arm_port=int(arms.get("right_port", 1234)),
            arm_host=str(arms.get("host", "localhost")),
            server_url=str(policy.get("server_url", "http://127.0.0.1:8202")),
            instruction=str(policy.get("instruction", cls.instruction)),
            normalization_tag=policy.get("normalization_tag", "yam_dual_molmoact2"),
            num_steps=policy.get("num_steps"),
            max_first_step_delta=float(checks.get("max_first_step_delta", 0.5)),
            max_step_delta=float(checks.get("max_step_delta", 0.2)),
            joint_limits=limits,
        )
