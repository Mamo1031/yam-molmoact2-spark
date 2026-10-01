import numpy as np

from dryrun import STATE_DIM
from dryrun.checks import JointLimits, validate_action_chunk


def _state() -> np.ndarray:
    s = np.zeros(STATE_DIM, dtype=np.float32)
    s[6] = s[13] = 1.0
    return s


def test_clean_chunk_is_ok() -> None:
    state = _state()
    actions = np.repeat(state[None, :], 30, axis=0)
    report = validate_action_chunk(actions, state)
    assert report.ok
    assert report.shape == (30, STATE_DIM)
    assert report.nan_count == 0 and report.inf_count == 0


def test_nan_and_inf_are_reported() -> None:
    state = _state()
    actions = np.repeat(state[None, :], 5, axis=0)
    actions[1, 2] = np.nan
    actions[3, 9] = np.inf
    report = validate_action_chunk(actions, state)
    assert not report.ok
    assert report.nan_count == 1 and report.inf_count == 1
    assert any("NaN" in i for i in report.issues) and any("Inf" in i for i in report.issues)


def test_wrong_dim_and_ndim() -> None:
    state = _state()
    bad = validate_action_chunk(np.zeros((30, 7)), state)
    assert any("dim 7" in i for i in bad.issues)
    worse = validate_action_chunk(np.zeros(14), state)
    assert any("2-D" in i for i in worse.issues)


def test_first_step_jump_names_the_joint() -> None:
    state = _state()
    actions = np.repeat(state[None, :], 3, axis=0)
    actions[0, 8] = 2.0  # right_joint_1
    report = validate_action_chunk(actions, state, max_first_step_delta=0.5)
    assert not report.ok
    assert "right_joint_1" in report.issues[0]
    assert report.to_row()["argmax_first_step_delta"] == "right_joint_1"


def test_per_step_jump_detected() -> None:
    state = _state()
    actions = np.repeat(state[None, :], 4, axis=0)
    actions[2, 0] = 0.3
    report = validate_action_chunk(actions, state, max_step_delta=0.2)
    assert any("per-step jump" in i for i in report.issues)


def test_joint_limits() -> None:
    state = _state()
    actions = np.repeat(state[None, :], 2, axis=0)
    actions[1, 6] = 1.5  # left_gripper above 1.0
    limits = JointLimits.from_mapping({"left_gripper": (0.0, 1.0)})
    report = validate_action_chunk(actions, state, limits=limits)
    assert any("left_gripper" in i for i in report.issues)
    assert report.limit_violations[6]
    assert int(report.limit_violations.sum()) == 1
