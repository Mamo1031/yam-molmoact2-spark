"""Validation of an action chunk returned by the policy (pure numpy, no hardware)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dryrun import STATE_DIM, STATE_NAMES


@dataclass
class JointLimits:
    """Per-dimension lower/upper bounds in the same units as the state (radians, gripper 0..1)."""

    lower: np.ndarray
    upper: np.ndarray

    @classmethod
    def from_mapping(cls, mapping: dict[str, tuple[float, float]]) -> JointLimits:
        lower = np.full(STATE_DIM, -np.inf)
        upper = np.full(STATE_DIM, np.inf)
        for name, (lo, hi) in mapping.items():
            idx = STATE_NAMES.index(name)
            lower[idx], upper[idx] = lo, hi
        return cls(lower=lower, upper=upper)


@dataclass
class ActionReport:
    """Result of validating one action chunk against the observed state."""

    shape: tuple[int, ...]
    nan_count: int
    inf_count: int
    first_step_delta: np.ndarray
    max_step_delta: np.ndarray
    limit_violations: np.ndarray
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_row(self) -> dict[str, object]:
        """Flatten the report into scalars for CSV logging."""
        return {
            "shape": "x".join(str(s) for s in self.shape),
            "nan_count": self.nan_count,
            "inf_count": self.inf_count,
            "max_first_step_delta": float(np.max(self.first_step_delta)),
            "argmax_first_step_delta": STATE_NAMES[int(np.argmax(self.first_step_delta))],
            "max_step_delta": float(np.max(self.max_step_delta)),
            "argmax_step_delta": STATE_NAMES[int(np.argmax(self.max_step_delta))],
            "limit_violations": int(np.sum(self.limit_violations)),
            "ok": self.ok,
            "issues": "; ".join(self.issues),
        }


def validate_action_chunk(
    actions: np.ndarray,
    state: np.ndarray,
    *,
    limits: JointLimits | None = None,
    max_first_step_delta: float = 0.5,
    max_step_delta: float = 0.2,
    expected_dim: int = STATE_DIM,
) -> ActionReport:
    """Check shape, finiteness, jump from the current state, per-step jumps and joint limits.

    ``actions`` is (N, D). ``state`` is (D,). Thresholds are in state units and only
    produce issues; nothing is clipped or modified.
    """
    actions = np.asarray(actions, dtype=np.float64)
    state = np.asarray(state, dtype=np.float64)
    issues: list[str] = []

    if actions.ndim != 2:
        issues.append(f"actions must be 2-D (N, D), got ndim={actions.ndim}")
        zeros = np.zeros(expected_dim)
        return ActionReport(actions.shape, 0, 0, zeros, zeros, zeros.astype(bool), issues)
    n_steps, dim = actions.shape
    if dim != expected_dim:
        issues.append(f"action dim {dim} != expected {expected_dim}")
    if n_steps == 0:
        issues.append("empty action chunk")
    if state.shape != (dim,):
        issues.append(f"state shape {state.shape} does not match action dim {dim}")

    nan_count = int(np.isnan(actions).sum())
    inf_count = int(np.isinf(actions).sum())
    if nan_count:
        issues.append(f"{nan_count} NaN values")
    if inf_count:
        issues.append(f"{inf_count} Inf values")

    finite = np.nan_to_num(actions, nan=0.0, posinf=0.0, neginf=0.0)
    if n_steps and state.shape == (dim,):
        first_delta = np.abs(finite[0] - state)
    else:
        first_delta = np.zeros(dim)
    step_delta = np.max(np.abs(np.diff(finite, axis=0)), axis=0) if n_steps > 1 else np.zeros(dim)

    if np.any(first_delta > max_first_step_delta):
        worst = int(np.argmax(first_delta))
        issues.append(
            f"first action jumps {first_delta[worst]:.3f} from current state on "
            f"{_name(worst, dim)} (threshold {max_first_step_delta})"
        )
    if np.any(step_delta > max_step_delta):
        worst = int(np.argmax(step_delta))
        issues.append(
            f"per-step jump {step_delta[worst]:.3f} on {_name(worst, dim)} "
            f"(threshold {max_step_delta})"
        )

    violations = np.zeros(dim, dtype=bool)
    if limits is not None and dim == expected_dim and n_steps:
        below = finite < limits.lower
        above = finite > limits.upper
        violations = np.any(below | above, axis=0)
        if np.any(violations):
            names = [STATE_NAMES[i] for i in np.flatnonzero(violations)]
            issues.append(f"joint limit violations on {', '.join(names)}")

    return ActionReport(
        shape=actions.shape,
        nan_count=nan_count,
        inf_count=inf_count,
        first_step_delta=first_delta,
        max_step_delta=step_delta,
        limit_violations=violations,
        issues=issues,
    )


def _name(idx: int, dim: int) -> str:
    return STATE_NAMES[idx] if dim == STATE_DIM else f"dim_{idx}"
