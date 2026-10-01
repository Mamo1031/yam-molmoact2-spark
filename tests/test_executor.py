"""Executor behaviour with a simulated PD follower (FakeSource) and a scripted policy."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dryrun.backends import Observation, PolicyResult
from dryrun.config import CameraSpec
from dryrun.executor import ExecutorConfig, GuardedExecutor, KeyListener
from dryrun.motor_monitor import MotorLimits, MotorMonitor
from dryrun.safety import SafetyLimits
from dryrun.sensors import FakeSource
from dryrun.yam_stats import DEFAULT_START_POSE

CAMS = {r: CameraSpec(serial=r, width=32, height=16) for r in ("top", "left", "right")}


class ScriptedPolicy:
    """Returns chunks that move joint 0 of each arm by a fixed offset from the observed state."""

    def __init__(self, offset: float = 0.2, nan: bool = False) -> None:
        self.offset = offset
        self.nan = nan
        self.calls = 0

    def act(self, obs: Observation, instruction: str) -> PolicyResult:
        self.calls += 1
        a = np.repeat(obs.state[None, :].astype(np.float64), 30, axis=0)
        a[:, 0] += np.linspace(0, self.offset, 30)
        a[:, 7] += np.linspace(0, self.offset, 30)
        if self.nan:
            a[3, 2] = np.nan
        return PolicyResult(actions=a.astype(np.float32), server_dt_ms=1.0, round_trip_ms=1.0)


def _make(
    tmp_path: Path,
    policy: ScriptedPolicy | None = None,
    motors: MotorMonitor | None = None,
    **cfg_kwargs: Any,
) -> tuple[GuardedExecutor, FakeSource, KeyListener]:
    src = FakeSource(CAMS, initial_state=DEFAULT_START_POSE, follow_gain=0.9)
    keys = KeyListener(stream=io.StringIO(""))
    cfg = ExecutorConfig(hz=200.0, tick_budget_s=1.0, hold_wait_s=0.05, **cfg_kwargs)
    limits = SafetyLimits(max_step_joint=0.01)
    ex = GuardedExecutor(
        src,
        policy,
        limits,
        cfg,
        DEFAULT_START_POSE,
        tmp_path,
        keys=keys,
        motors=motors,
        motor_limits=MotorLimits(feedback_timeout_s=0.05, temp_warn_c=60, temp_stop_c=70),
        log=lambda s: None,
    )
    return ex, src, keys


class MotorClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _motor_frame(motor_id: int, state: int = 1, rotor: int = 30) -> tuple[int, bytes]:
    return 0x10 + motor_id, bytes([(state << 4) | motor_id, 0, 0, 0, 0, 0, 30, rotor])


def _motors(*, right_alive: bool = True) -> tuple[MotorMonitor, MotorClock]:
    """A monitor fed by hand: frames stay fresh until the test moves the clock."""
    clock = MotorClock()
    mon = MotorMonitor({"left": "can_follower_l", "right": "can_follower_r"}, clock=clock)
    mon.watch_injected()
    for arm in ("left", "right") if right_alive else ("left",):
        for motor_id in range(1, 8):
            mon.feed(arm, *_motor_frame(motor_id))
    return mon, clock


class MotorEventPolicy(ScriptedPolicy):
    """Triggers a motor event when the second chunk is requested (the first one has run)."""

    def __init__(self, event: Any) -> None:
        super().__init__(offset=0.05)
        self.event = event

    def act(self, obs: Observation, instruction: str) -> PolicyResult:
        if self.calls == 1:
            self.event()
        return super().act(obs, instruction)


def test_first_command_equals_measured(tmp_path: Path) -> None:
    ex, src, _ = _make(tmp_path)
    m = ex.arm_and_first_command()
    assert np.allclose(src.sent[-1], m)


def test_refuses_far_from_start_pose(tmp_path: Path) -> None:
    src = FakeSource(CAMS, initial_state=DEFAULT_START_POSE + 0.3)
    ex = GuardedExecutor(
        src,
        None,
        SafetyLimits(),
        ExecutorConfig(hz=200.0),
        DEFAULT_START_POSE,
        tmp_path,
        keys=KeyListener(io.StringIO("")),
        log=lambda s: None,
    )
    with pytest.raises(RuntimeError, match="refusing to start"):
        ex.arm_and_first_command()


def test_hold_test_reports_small_error(tmp_path: Path) -> None:
    ex, src, _ = _make(tmp_path)
    rep = ex.hold_test(0.2)
    ex.close(rep)
    assert rep.ok and rep.ticks > 10
    err_max = rep.extra["steady_state_err_max"]
    assert isinstance(err_max, list) and max(err_max) < 1e-3  # fake follower converges
    assert (tmp_path / "ticks.csv").exists() and (tmp_path / "summary.json").exists()


def test_run_rate_limits_and_masks_arm(tmp_path: Path) -> None:
    pol = ScriptedPolicy(offset=0.2)
    ex, src, _ = _make(tmp_path, policy=pol, arms="left", steps_per_chunk=10, max_chunks=2)
    ex.policy = pol
    rep = ex.run()
    ex.close(rep)
    assert rep.ok, rep.fault
    assert rep.chunks_executed == 2 and pol.calls == 2
    sent = np.array(src.sent)
    steps = np.abs(np.diff(sent, axis=0))
    assert steps[:, :6].max() <= 0.01 + 1e-9  # rate limit respected
    assert np.allclose(sent[:, 7], sent[0, 7])  # right arm masked: never moves
    assert sent[-1, 0] > sent[0, 0]  # left joint 0 followed the policy


def test_run_shadow_sends_nothing(tmp_path: Path) -> None:
    pol = ScriptedPolicy()
    ex, src, _ = _make(tmp_path, policy=pol, shadow=True, steps_per_chunk=5, max_chunks=1)
    ex.policy = pol
    rep = ex.run()
    ex.close(rep)
    assert rep.ok and rep.chunks_executed == 1 and src.sent == []


def test_nan_chunk_is_rejected_then_fault(tmp_path: Path) -> None:
    pol = ScriptedPolicy(nan=True)
    ex, src, _ = _make(
        tmp_path, policy=pol, steps_per_chunk=5, max_chunks=1, max_chunk_rejections=2
    )
    ex.policy = pol
    rep = ex.run()
    ex.close(rep)
    assert not rep.ok and "rejected" in rep.fault
    assert ex.held and pol.calls == 2


def test_tracking_fault_holds_at_measured(tmp_path: Path) -> None:
    pol = ScriptedPolicy(offset=0.2)
    ex, src, _ = _make(tmp_path, policy=pol, steps_per_chunk=10, max_chunks=3)
    ex.policy = pol
    src.follow_gain = 0.0  # the "arm" stops following -> tracking error grows
    rep = ex.run()
    ex.close(rep)
    assert not rep.ok and "tracking error" in rep.fault
    # the last command released force: target == measured at fault time
    assert np.allclose(src.sent[-1], src.read_state_only(), atol=1e-6)


class GripperClosingPolicy(ScriptedPolicy):
    """Closes both grippers over the chunk and leaves every arm joint where it is."""

    def act(self, obs: Observation, instruction: str) -> PolicyResult:
        self.calls += 1
        a = np.repeat(obs.state[None, :].astype(np.float64), 30, axis=0)
        for g in (6, 13):
            a[:, g] = np.linspace(obs.state[g], 0.0, 30)
        return PolicyResult(actions=a.astype(np.float32), server_dt_ms=1.0, round_trip_ms=1.0)


def test_gripper_stalled_on_an_object_does_not_fault(tmp_path: Path) -> None:
    # 2026-10-01: grasping a hard object. The gripper target keeps closing while the measured
    # value stays at the object width and the arm does not move: neither the (disabled) gripper
    # tracking check nor the liveness check may stop the run.
    pol = GripperClosingPolicy()
    ex, src, _ = _make(tmp_path, policy=pol, steps_per_chunk=20, max_chunks=20)
    ex.limits = SafetyLimits(
        max_step_joint=0.01,
        tracking_err_max=np.array([0.08, 0.10, 0.10, 0.10, 0.10, 0.10, 1.0] * 2),
    )
    ex.policy = pol
    src.follow_gain = 0.0  # the gripper is blocked by the object; the arm is already still
    rep = ex.run()
    ex.close(rep)
    assert rep.ok, rep.fault
    assert rep.chunks_executed == 20 and rep.ticks > 300  # longer than the 1 s liveness window
    sent = np.array(src.sent)
    # each chunk re-plans from the stalled measured value, so the target only gets ~0.5 below it
    assert sent[:, 6].min() < 0.3 and sent[:, 13].min() < 0.3  # the close commands went out


def test_frozen_state_with_moving_joints_faults(tmp_path: Path) -> None:
    pol = ScriptedPolicy(offset=0.2)
    ex, src, _ = _make(tmp_path, policy=pol, steps_per_chunk=20, max_chunks=30)
    # joint tracking is switched off here so that only the liveness check can fire
    ex.limits = SafetyLimits(max_step_joint=0.01, tracking_err_max=np.array([10.0] * 14))
    ex.policy = pol
    src.follow_gain = 0.0
    rep = ex.run()
    ex.close(rep)
    assert not rep.ok and rep.fault.startswith("state frozen while targets moved")


class GraspAndLiftPolicy(ScriptedPolicy):
    """Closes both grippers and moves joint 0 of each arm (the arm stalls in the test)."""

    def act(self, obs: Observation, instruction: str) -> PolicyResult:
        self.calls += 1
        a = np.repeat(obs.state[None, :].astype(np.float64), 30, axis=0)
        a[:, 0] += np.linspace(0, self.offset, 30)
        a[:, 7] += np.linspace(0, self.offset, 30)
        for g in (6, 13):
            a[:, g] = np.linspace(obs.state[g], 0.0, 30)
        return PolicyResult(actions=a.astype(np.float32), server_dt_ms=1.0, round_trip_ms=1.0)


def test_arm_fault_keeps_the_grip(tmp_path: Path) -> None:
    pol = GraspAndLiftPolicy(offset=0.3)
    ex, src, _ = _make(tmp_path, policy=pol, steps_per_chunk=20, max_chunks=5)
    ex.limits = SafetyLimits(
        max_step_joint=0.01,
        tracking_err_max=np.array([0.08, 0.10, 0.10, 0.10, 0.10, 0.10, 1.0] * 2),
    )
    ex.policy = pol
    src.follow_gain = 0.0  # the object blocks the gripper and the arm stops following
    rep = ex.run()
    ex.close(rep)
    assert not rep.ok and "tracking error" in rep.fault
    measured = src.read_state_only()
    hold = src.sent[-1]
    assert np.allclose(hold[:6], measured[:6]) and np.allclose(hold[7:13], measured[7:13])
    assert hold[6] < measured[6] - 0.1 and hold[13] < measured[13] - 0.1  # grip not released
    assert hold[6] == src.sent[-2][6]  # the gripper kept the last commanded target


def test_enter_releases_everything_and_o_opens_while_held(tmp_path: Path) -> None:
    pol = GraspAndLiftPolicy(offset=0.3)
    ex, src, keys = _make(tmp_path, policy=pol, steps_per_chunk=20, max_chunks=5)
    ex.policy = pol
    keys.push("")
    keys.push("o")
    rep = ex.run()
    ex.close(rep)
    assert ex.fault_reason == "operator hold (Enter)"
    first_hold = src.sent[1]  # the first command is the measured pose, the hold is next
    assert np.allclose(first_hold, src.sent[0])  # Enter: the whole measured vector, grip included
    assert src.sent[-1][6] > 0.999 and src.sent[-1][13] > 0.999  # 'o' was handled while held


def test_return_to_start_keeps_the_grip(tmp_path: Path) -> None:
    ex, src, _ = _make(tmp_path)
    ex.limits = SafetyLimits(
        max_step_joint=0.01,
        tracking_err_max=np.array([0.08, 0.10, 0.10, 0.10, 0.10, 0.10, 1.0] * 2),
    )
    m = ex.arm_and_first_command()
    held = m.copy()
    held[6] = held[13] = 0.3  # a closed target on an object the FakeSource does not model
    src.follow_gain = 0.0
    ex._send(held)
    rep = ex.return_to_start()
    assert rep.ok, rep.fault
    sent = np.array(src.sent[2:])
    assert sent.shape[0] == 0 or (np.all(sent[:, 6] == 0.3) and np.all(sent[:, 13] == 0.3))


def test_operator_quit_key(tmp_path: Path) -> None:
    pol = ScriptedPolicy()
    ex, src, keys = _make(tmp_path, policy=pol, steps_per_chunk=30, max_chunks=100)
    ex.policy = pol
    keys.push("q")
    rep = ex.run()
    ex.close(rep)
    assert rep.fault == "operator quit" and rep.chunks_executed == 0


def test_direction_test_reports_signs(tmp_path: Path) -> None:
    ex, src, _ = _make(tmp_path)
    rep = ex.direction_test("left", delta=0.03, hold_s=0.05)
    ex.close(rep)
    assert rep.ok, rep.fault
    assert len(rep.extra) == 6
    for v in rep.extra.values():
        assert isinstance(v, dict) and v["sign_ok"]


def test_skip_pose_checks_requires_shadow() -> None:
    with pytest.raises(ValueError):
        ExecutorConfig(skip_pose_checks=True, shadow=False)
    ExecutorConfig(skip_pose_checks=True, shadow=True)


def test_calibrate_table_reports_positions(tmp_path: Path) -> None:
    from dryrun.kinematics import ArmKinematics

    src = FakeSource(CAMS, initial_state=DEFAULT_START_POSE)
    ex = GuardedExecutor(
        src,
        None,
        SafetyLimits(),
        ExecutorConfig(),
        DEFAULT_START_POSE,
        tmp_path,
        kinematics=ArmKinematics(),
        keys=KeyListener(io.StringIO("")),
        log=lambda s: None,
    )
    vals = ex.calibrate_table()
    assert set(vals) == {f"{a}_grasp_{c}" for a in ("left", "right") for c in "xyz"}
    assert src.sent == []


def test_shadow_ignores_tracking(tmp_path: Path) -> None:
    pol = ScriptedPolicy(offset=0.5)
    ex, src, _ = _make(tmp_path, policy=pol, shadow=True, steps_per_chunk=20, max_chunks=2)
    src.follow_gain = 0.0
    rep = ex.run()
    ex.close(rep)
    assert rep.ok and rep.chunks_executed == 2 and src.sent == []


def test_refuses_to_start_on_silent_bus(tmp_path: Path) -> None:
    # 2026-09-30: the right server's motor thread died; its RPC state stayed frozen but valid.
    mon, _ = _motors(right_alive=False)
    ex, src, _ = _make(tmp_path, motors=mon)
    with pytest.raises(RuntimeError, match="no motor feedback on can_follower_r"):
        ex.arm_and_first_command()
    assert src.sent == []


def test_bus_going_silent_faults_with_the_real_cause(tmp_path: Path) -> None:
    mon, clock = _motors()

    def right_bus_dies() -> None:
        clock.t += 1.0
        for motor_id in range(1, 8):
            mon.feed("left", *_motor_frame(motor_id))
        src.follow_gain = 0.0  # the dead server also freezes the state it reports

    pol = MotorEventPolicy(right_bus_dies)
    ex, src, _ = _make(tmp_path, policy=pol, motors=mon, steps_per_chunk=5, max_chunks=3)
    rep = ex.run()
    ex.close(rep)
    assert not rep.ok and rep.fault.startswith("no motor feedback on can_follower_r")
    assert "tracking error" not in rep.fault
    assert rep.chunks_executed == 1 and ex.held


def test_motor_error_state_faults(tmp_path: Path) -> None:
    mon, _ = _motors()
    pol = MotorEventPolicy(lambda: mon.feed("right", *_motor_frame(2, state=0xC, rotor=120)))
    ex, src, _ = _make(tmp_path, policy=pol, motors=mon, steps_per_chunk=5, max_chunks=3)
    rep = ex.run()
    ex.close(rep)
    assert not rep.ok and "right motor 2 (right_joint_1)" in rep.fault
    assert "rotor over temperature" in rep.fault


def test_hot_motor_stops_the_run_but_not_the_return(tmp_path: Path) -> None:
    mon, _ = _motors()
    pol = MotorEventPolicy(lambda: mon.feed("right", *_motor_frame(2, rotor=72)))
    ex, src, _ = _make(tmp_path, policy=pol, motors=mon, steps_per_chunk=5, max_chunks=3)
    rep = ex.run()
    assert not rep.ok and "motor too hot" in rep.fault and "right motor 2" in rep.fault
    back = ex.return_to_start()
    assert back.ok, back.fault
    assert ex.stop_when_hot  # only lifted for the operator action
    ex.close(rep)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["motor_temp_max"]["right"]["rotor"][1] == 72.0
    assert (tmp_path / "temps.csv").read_text().startswith("t,left_m1_rotor,left_m1_mos")


def test_hot_motor_refuses_a_new_run(tmp_path: Path) -> None:
    mon, _ = _motors()
    mon.feed("left", *_motor_frame(2, rotor=75))
    ex, src, _ = _make(tmp_path, motors=mon)
    with pytest.raises(RuntimeError, match="refusing to start: motor too hot"):
        ex.arm_and_first_command()
    ex.stop_when_hot = False  # what return-to-start / open-grippers do
    ex.arm_and_first_command()


def test_return_to_start_logs_the_fault_tick(tmp_path: Path) -> None:
    mon, clock = _motors()
    ex, src, _ = _make(tmp_path, motors=mon)
    ex.arm_and_first_command()
    clock.t += 1.0  # both buses silent from here on
    rep = ex.return_to_start()
    ex.close(rep)
    assert not rep.ok and "no motor feedback" in rep.fault
    rows = (tmp_path / "ticks.csv").read_text().splitlines()
    assert len(rows) == 2 and "no motor feedback on can_follower_l" in rows[1]


def test_unwatched_bus_refuses_to_start(tmp_path: Path) -> None:
    mon, _ = _motors()
    mon.unavailable = {"right": "can_follower_r: [Errno 19] No such device"}
    ex, src, _ = _make(tmp_path, motors=mon)
    with pytest.raises(RuntimeError, match="right arm is not monitored"):
        ex.arm_and_first_command()
    assert src.sent == []


def test_shadow_is_not_stopped_by_heat(tmp_path: Path) -> None:
    mon, _ = _motors()
    mon.feed("right", *_motor_frame(2, rotor=80))
    pol = ScriptedPolicy()
    ex, src, _ = _make(
        tmp_path, policy=pol, motors=mon, shadow=True, steps_per_chunk=5, max_chunks=1
    )
    rep = ex.run()
    ex.close(rep)
    assert rep.ok and rep.chunks_executed == 1 and src.sent == []


def test_calibrate_table_refuses_a_frozen_state(tmp_path: Path) -> None:
    from dryrun.kinematics import ArmKinematics

    mon, _ = _motors(right_alive=False)
    ex, src, _ = _make(tmp_path, motors=mon)
    ex.kin = ArmKinematics()
    with pytest.raises(RuntimeError, match="no motor feedback on can_follower_r"):
        ex.calibrate_table()


class LoweringPolicy(ScriptedPolicy):
    """Leans both shoulders forward so the fingertips head below the table plane."""

    def act(self, obs: Observation, instruction: str) -> PolicyResult:
        self.calls += 1
        a = np.repeat(obs.state[None, :].astype(np.float64), 30, axis=0)
        for shoulder in (1, 8):
            a[:, shoulder] += np.linspace(0, 0.25, 30)
        a[:, 0] += np.linspace(0, 0.1, 30)
        return PolicyResult(actions=a.astype(np.float32), server_dt_ms=1.0, round_trip_ms=1.0)


def test_target_below_the_table_is_lifted_not_faulted(tmp_path: Path) -> None:
    from dryrun.kinematics import ArmKinematics

    kin = ArmKinematics()
    table = min(
        kin.grasp_height(DEFAULT_START_POSE[:6]), kin.grasp_height(DEFAULT_START_POSE[7:13])
    )
    table -= 0.02  # the policy reaches the plane within the first chunk
    pol = LoweringPolicy()
    ex, src, _ = _make(
        tmp_path,
        policy=pol,
        steps_per_chunk=20,
        max_chunks=4,
        table_z_in_base_frame=table,
        min_grasp_clearance_m=0.0,
    )
    ex.kin = kin
    rep = ex.run()
    ex.close(rep)
    assert rep.ok, rep.fault
    assert rep.chunks_executed == 4
    sent = np.array(src.sent)
    heights = np.array([[kin.grasp_height(t[:6]), kin.grasp_height(t[7:13])] for t in sent])
    assert heights.min() >= table - 1e-4  # never commanded below the plane
    assert heights.min() <= table + 2e-3  # and it did get down to the plane
    assert ex.floor_clamped_ticks["left"] > 0 and ex.floor_clamped_ticks["right"] > 0
    assert sent[-1, 0] > sent[0, 0] + 0.05  # sideways motion continued while clamped
    steps = np.abs(np.diff(sent[:, :6], axis=0))
    assert steps.max() <= 0.012  # the lift stays within about one rate-limited step
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["floor_clamped_ticks"]["right"] == ex.floor_clamped_ticks["right"]


def test_lift_to_is_minimal_and_idempotent() -> None:
    from dryrun.kinematics import ArmKinematics

    kin = ArmKinematics()
    q = DEFAULT_START_POSE[:6].astype(np.float64)
    z = kin.grasp_height(q)
    same = kin.lift_to(q, z - 0.01)
    assert same is not None and np.array_equal(same, q)  # already above: unchanged
    lifted = kin.lift_to(q, z + 0.01)
    assert lifted is not None
    assert z + 0.01 <= kin.grasp_height(lifted) <= z + 0.011
    assert np.abs(lifted - q).max() < 0.05
