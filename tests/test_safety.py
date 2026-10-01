import numpy as np
import pytest

from dryrun import STATE_DIM
from dryrun.safety import (
    SafetyLimits,
    apply_residual,
    check_tracking,
    rate_limit,
    start_pose_deviation,
    validate_chunk_for_execution,
    within_bounds,
)
from dryrun.yam_stats import DEFAULT_START_POSE, XML_JOINT_RANGE


def _pose() -> np.ndarray:
    return DEFAULT_START_POSE.copy()


def test_bounds_are_inside_xml_and_training_range() -> None:
    lim = SafetyLimits()
    assert np.all(lim.lower[:6] >= XML_JOINT_RANGE[:, 0] + 0.2 - 1e-9)
    assert np.all(lim.upper[:6] <= XML_JOINT_RANGE[:, 1] - 0.2 + 1e-9)
    assert lim.lower[6] == 0.0 and lim.upper[6] == 1.0
    assert not within_bounds(_pose(), lim)
    folded = np.zeros(STATE_DIM)
    assert "left_joint_1" in within_bounds(folded, lim)  # j2 lower bound is 0 + margin


def test_rate_limit_uses_common_scale_and_last_target() -> None:
    lim = SafetyLimits(max_step_joint=0.01)
    last = _pose()
    desired = last.copy()
    desired[0] += 0.05  # left joint 0 wants 5x the limit
    desired[1] += 0.025  # left joint 1 wants 2.5x
    desired[7] += 0.004  # right joint 0 within limit
    res = rate_limit(desired, last, lim)
    assert res.target[0] - last[0] == pytest.approx(0.01)
    assert res.target[1] - last[1] == pytest.approx(0.005)  # same scale 0.2 keeps direction
    assert res.target[7] - last[7] == pytest.approx(0.004)
    assert res.scale[0] == pytest.approx(0.2) and res.scale[1] == pytest.approx(1.0)
    # Measured position is irrelevant: only last_target matters.
    res2 = rate_limit(desired, last, lim)
    assert np.allclose(res.target, res2.target)


def test_rate_limit_gripper_and_clamp() -> None:
    lim = SafetyLimits(max_step_gripper=0.03)
    last = _pose()
    desired = last.copy()
    desired[6] = 0.0  # close left gripper fully
    desired[13] = 5.0  # nonsense above range
    res = rate_limit(desired, last, lim)
    assert res.target[6] == pytest.approx(last[6] - 0.03)
    assert (
        res.target[13] <= 1.0
        and res.clamped[13]
        or res.target[13] == pytest.approx(min(1.0, last[13] + 0.03))
    )
    # Walk toward the upper bound and confirm the clamp holds.
    t = last.copy()
    desired = last.copy()
    desired[2] = 10.0
    for _ in range(500):
        t = rate_limit(desired, t, lim).target
    assert t[2] == pytest.approx(lim.upper[2])


def test_chunk_rejection_on_jump_and_bounds() -> None:
    lim = SafetyLimits()
    measured = _pose()
    good = np.repeat(measured[None, :], 30, axis=0)
    assert validate_chunk_for_execution(good, measured, lim).ok
    jump = good.copy()
    jump[0, 3] += 0.3
    v = validate_chunk_for_execution(jump, measured, lim)
    assert not v.ok and any("jumps" in r for r in v.reasons)
    oob = good.copy()
    oob[10:, 1] = 3.6  # beyond upper bound of left joint 1
    v = validate_chunk_for_execution(oob, measured, lim)
    assert not v.ok and any("absolute bounds" in r for r in v.reasons)
    nan = good.copy()
    nan[5, 0] = np.nan
    assert not validate_chunk_for_execution(nan, measured, lim).ok


def test_tracking_thresholds_per_dimension() -> None:
    lim = SafetyLimits()
    target = _pose()
    measured = target.copy()
    measured[0] += 0.04  # joint 0: limit 0.05 -> ok
    assert check_tracking(measured, target, lim).ok
    measured[0] += 0.02  # 0.06 > 0.05 -> fault
    r = check_tracking(measured, target, lim)
    assert not r.ok and r.worst_name == "left_joint_0"
    measured = target.copy()
    measured[4] += 0.08  # wrist: limit 0.10 -> ok
    assert check_tracking(measured, target, lim).ok


def test_start_pose_deviation_ignores_grippers() -> None:
    start = _pose()
    m = start.copy()
    m[6] = 0.0
    m[9] += 0.3
    d, i = start_pose_deviation(m, start)
    assert d == pytest.approx(0.3) and i == 9


def test_residual_capped() -> None:
    lim = SafetyLimits(residual_max=0.05)
    base = _pose()
    out = apply_residual(base, np.full(STATE_DIM, 1.0), lim)
    assert np.allclose(out - base, 0.05)
    assert np.allclose(apply_residual(base, None, lim), base)


def test_lab_config_disables_the_gripper_guards() -> None:
    # 2026-10-01: hard objects stall the gripper at the object width while the policy keeps
    # commanding "closed"; the yaml turns the gripper tracking and jump checks off (1.0).
    import yaml

    raw = yaml.safe_load(open("configs/dry_run.yaml", encoding="utf-8"))
    lim = SafetyLimits.from_mapping(raw["safety"])
    assert lim.tracking_err_max[6] == lim.tracking_err_max[13] == 1.0
    assert lim.tracking_err_max[0] == 0.08 and lim.tracking_err_max[1] == 0.10
    assert lim.chunk_first_step_max_gripper == lim.chunk_step_max_gripper == 1.0
    measured = DEFAULT_START_POSE.copy()
    measured[6] = 0.9
    chunk = np.repeat(measured[None, :], 30, axis=0)
    chunk[:, 6] = 0.0  # "closed" while the measured gripper sits at the object width
    assert validate_chunk_for_execution(chunk, measured, lim).ok
    assert check_tracking(measured, chunk[0], lim).ok


def test_gripper_jumps_use_their_own_threshold() -> None:
    lim = SafetyLimits()
    measured = _pose()
    chunk = np.repeat(measured[None, :], 30, axis=0)
    chunk[5:, 6] = measured[6] - 0.3  # gripper closes 0.3 in one step: allowed (<0.5)
    assert validate_chunk_for_execution(chunk, measured, lim).ok
    chunk[5:, 6] = measured[6] - 0.6  # 0.6 in one step: rejected
    v = validate_chunk_for_execution(chunk, measured, lim)
    assert not v.ok and "gripper" in v.reasons[0]
    chunk = np.repeat(measured[None, :], 30, axis=0)
    chunk[5:, 2] += 0.15  # joint jump 0.15 rad: rejected
    assert not validate_chunk_for_execution(chunk, measured, lim).ok
