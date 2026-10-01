"""Safety filter for executing policy actions on the YAM arms.

Design rules (see the G5 plan and its review):
- Rate limiting is relative to the LAST COMMANDED TARGET, never to the measured position.
  Clipping against the measured position removes the PD stiffness and lets the arm drift.
- Per arm, the joint delta is scaled by ONE common factor so the path direction is kept.
- The tracking error (target minus measured) is a FAULT condition, not something to clip.
- Absolute bounds are the XML joint range shrunk by a margin, intersected with the training
  action range, so the follower server's own limit (which stops its loop) is never reached.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dryrun import STATE_DIM, STATE_NAMES
from dryrun.checks import validate_action_chunk
from dryrun.yam_stats import GRIPPER_RANGE, TRAIN_ACTION_MAX, TRAIN_ACTION_MIN, XML_JOINT_RANGE

LEFT = slice(0, 7)
RIGHT = slice(7, 14)
JOINTS = [slice(0, 6), slice(7, 13)]
GRIPPERS = [6, 13]


@dataclass
class SafetyLimits:
    """All thresholds in state units (radians; gripper 0..1)."""

    max_step_joint: float = 0.01
    max_step_gripper: float = 0.03
    joint_margin: float = 0.2
    use_training_range: bool = True
    tracking_err_max: np.ndarray = field(
        default_factory=lambda: np.array([0.05, 0.05, 0.05, 0.10, 0.10, 0.10, 0.2] * 2)
    )
    chunk_first_step_max: float = 0.1
    chunk_step_max: float = 0.1
    chunk_first_step_max_gripper: float = 0.5
    chunk_step_max_gripper: float = 0.5
    start_pose_deviation_max: float | None = 0.5
    residual_max: float = 0.05

    def __post_init__(self) -> None:
        self.tracking_err_max = np.asarray(self.tracking_err_max, dtype=np.float64)
        if self.tracking_err_max.shape != (STATE_DIM,):
            raise ValueError("tracking_err_max must have 14 entries")
        lower = np.empty(STATE_DIM)
        upper = np.empty(STATE_DIM)
        for arm in JOINTS:
            lower[arm] = XML_JOINT_RANGE[:, 0] + self.joint_margin
            upper[arm] = XML_JOINT_RANGE[:, 1] - self.joint_margin
        for g in GRIPPERS:
            lower[g], upper[g] = GRIPPER_RANGE
        if self.use_training_range:
            lower = np.maximum(lower, TRAIN_ACTION_MIN)
            upper = np.minimum(upper, TRAIN_ACTION_MAX)
        if np.any(lower >= upper):
            raise ValueError("empty absolute range after applying margins")
        self.lower = lower
        self.upper = upper

    @classmethod
    def from_mapping(cls, raw: dict[str, object] | None) -> SafetyLimits:
        raw = dict(raw or {})
        kwargs: dict[str, object] = {}
        for key in (
            "max_step_joint",
            "max_step_gripper",
            "joint_margin",
            "use_training_range",
            "chunk_first_step_max",
            "chunk_step_max",
            "chunk_first_step_max_gripper",
            "chunk_step_max_gripper",
            "residual_max",
        ):
            if key in raw:
                kwargs[key] = raw[key]
        if "start_pose_deviation_max" in raw:
            v = raw["start_pose_deviation_max"]
            kwargs["start_pose_deviation_max"] = None if v is None else float(v)  # type: ignore[arg-type]
        if "tracking_err_max" in raw:
            t = raw["tracking_err_max"]
            if isinstance(t, dict):
                arr = (
                    np.array(
                        [float(t.get("joint", 0.05))] * 3
                        + [float(t.get("wrist", 0.10))] * 3
                        + [float(t.get("gripper", 0.2))]
                    )
                    if "joint" in t
                    else None
                )
                if arr is None:
                    raise ValueError("tracking_err_max mapping needs joint/wrist/gripper")
                kwargs["tracking_err_max"] = np.concatenate([arr, arr])
            else:
                kwargs["tracking_err_max"] = np.asarray(t, dtype=np.float64)
        return cls(**kwargs)  # type: ignore[arg-type]


@dataclass
class ChunkVerdict:
    ok: bool
    reasons: list[str]


def validate_chunk_for_execution(
    chunk: np.ndarray, measured: np.ndarray, limits: SafetyLimits
) -> ChunkVerdict:
    """Reject a chunk that is malformed, jumps away from the measured pose, or leaves the bounds.

    A large first-step jump or a large step-to-step jump usually means a wrong state order
    or an out-of-distribution input; clipping would hide it, so the chunk is refused instead.
    """
    # Shape/NaN/Inf come from the generic check; jump thresholds are applied here per group
    # because grippers (0..1) and joints (rad) need different limits.
    base = validate_action_chunk(chunk, measured, max_first_step_delta=1e9, max_step_delta=1e9)
    reasons = list(base.issues)
    if base.shape == (0,) or len(base.shape) != 2:
        return ChunkVerdict(False, reasons)
    finite = np.nan_to_num(np.asarray(chunk, dtype=np.float64))
    m = np.asarray(measured, dtype=np.float64)
    first = np.abs(finite[0] - m)
    steps = (
        np.max(np.abs(np.diff(finite, axis=0)), axis=0) if len(finite) > 1 else np.zeros(STATE_DIM)
    )
    for name, sl_list, f_max, s_max in (
        ("joint", JOINTS, limits.chunk_first_step_max, limits.chunk_step_max),
        (
            "gripper",
            [slice(g, g + 1) for g in GRIPPERS],
            limits.chunk_first_step_max_gripper,
            limits.chunk_step_max_gripper,
        ),
    ):
        for sl in sl_list:
            idx = np.arange(STATE_DIM)[sl]
            i = idx[int(np.argmax(first[sl]))]
            if first[i] > f_max:
                reasons.append(
                    f"first action jumps {first[i]:.3f} from measured on {STATE_NAMES[i]} "
                    f"({name} threshold {f_max})"
                )
            j = idx[int(np.argmax(steps[sl]))]
            if steps[j] > s_max:
                reasons.append(
                    f"per-step jump {steps[j]:.3f} on {STATE_NAMES[j]} ({name} threshold {s_max})"
                )
    below = finite < limits.lower
    above = finite > limits.upper
    if np.any(below | above):
        dims = np.flatnonzero(np.any(below | above, axis=0))
        names = ", ".join(STATE_NAMES[i] for i in dims)
        reasons.append(f"chunk leaves absolute bounds on {names}")
    return ChunkVerdict(not reasons, reasons)


@dataclass
class RateLimitResult:
    target: np.ndarray
    scale: tuple[float, float]
    clamped: np.ndarray


def rate_limit(
    desired: np.ndarray, last_target: np.ndarray, limits: SafetyLimits
) -> RateLimitResult:
    """Move from ``last_target`` toward ``desired`` by at most the per-tick limits.

    Joint deltas of each arm are scaled by a common factor; grippers are limited separately.
    The result is then clamped to the absolute bounds.
    """
    desired = np.asarray(desired, dtype=np.float64)
    last = np.asarray(last_target, dtype=np.float64)
    target = last.copy()
    scales = []
    for arm in JOINTS:
        delta = desired[arm] - last[arm]
        worst = float(np.max(np.abs(delta))) if delta.size else 0.0
        s = 1.0 if worst <= limits.max_step_joint or worst == 0.0 else limits.max_step_joint / worst
        target[arm] = last[arm] + s * delta
        scales.append(s)
    for g in GRIPPERS:
        d = float(np.clip(desired[g] - last[g], -limits.max_step_gripper, limits.max_step_gripper))
        target[g] = last[g] + d
    clamped_target = np.clip(target, limits.lower, limits.upper)
    clamped = clamped_target != target
    return RateLimitResult(clamped_target, (scales[0], scales[1]), clamped)


@dataclass
class TrackingResult:
    ok: bool
    errors: np.ndarray
    worst_dim: int
    worst_ratio: float

    @property
    def worst_name(self) -> str:
        return STATE_NAMES[self.worst_dim]


def check_tracking(
    measured: np.ndarray, target: np.ndarray, limits: SafetyLimits
) -> TrackingResult:
    """Compare the commanded target with the measured pose against per-dimension thresholds."""
    err = np.abs(np.asarray(target, dtype=np.float64) - np.asarray(measured, dtype=np.float64))
    ratio = err / limits.tracking_err_max
    worst = int(np.argmax(ratio))
    return TrackingResult(bool(np.all(ratio <= 1.0)), err, worst, float(ratio[worst]))


def start_pose_deviation(measured: np.ndarray, start_pose: np.ndarray) -> tuple[float, int]:
    """Largest joint deviation (grippers excluded) from the start pose and its dimension."""
    d = np.abs(np.asarray(measured, dtype=np.float64) - np.asarray(start_pose, dtype=np.float64))
    for g in GRIPPERS:
        d[g] = 0.0
    i = int(np.argmax(d))
    return float(d[i]), i


def within_bounds(pose: np.ndarray, limits: SafetyLimits) -> list[str]:
    """Names of dimensions of ``pose`` outside the absolute bounds (empty when all inside)."""
    p = np.asarray(pose, dtype=np.float64)
    bad = np.flatnonzero((p < limits.lower) | (p > limits.upper))
    return [STATE_NAMES[i] for i in bad]


def apply_residual(
    base: np.ndarray, residual: np.ndarray | None, limits: SafetyLimits
) -> np.ndarray:
    """Add a (future) learned residual to the base action, capped per dimension."""
    if residual is None:
        return np.asarray(base, dtype=np.float64)
    r = np.clip(np.asarray(residual, dtype=np.float64), -limits.residual_max, limits.residual_max)
    return np.asarray(base, dtype=np.float64) + r
