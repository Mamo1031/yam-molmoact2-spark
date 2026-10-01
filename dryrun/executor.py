"""Guarded executor: drives the YAM follower servers from policy action chunks (Gate G5).

Every target that reaches the arms goes through the same path:
``desired -> (+residual) -> rate_limit (relative to last target, common scale per arm)
-> absolute clamp -> keep the fingertips above the table plane -> send``.
Watchdogs run every tick, also while inference is in flight (inference runs in a worker
thread). On any fault the executor sends ``target = measured`` once to release the stored
PD force, then holds that fixed target and waits for the operator.

The follower servers answer with a frozen state once their motor thread has died, so the
motors themselves are watched through ``MotorMonitor`` (listen-only CAN): a silent bus or a
motor in an error state is a fault, and a hot motor stops the policy before it trips.
"""

from __future__ import annotations

import csv
import json
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import numpy as np

from dryrun import STATE_NAMES
from dryrun.backends import Observation, Policy, PolicyResult
from dryrun.kinematics import ArmKinematics
from dryrun.motor_monitor import MOTORS_PER_ARM, MotorFeedback, MotorLimits, MotorMonitor
from dryrun.safety import (
    GRIPPERS,
    JOINTS,
    SafetyLimits,
    apply_residual,
    check_tracking,
    rate_limit,
    start_pose_deviation,
    validate_chunk_for_execution,
    within_bounds,
)
from dryrun.sensors import ObservationSource

ResidualFn = Callable[[Observation, np.ndarray], np.ndarray]
ARM_SLICES = {"left": slice(0, 7), "right": slice(7, 14)}
JOINT_DIMS = np.r_[JOINTS[0], JOINTS[1]]
"""Indices of the twelve arm joints (grippers excluded)."""


@dataclass
class ExecutorConfig:
    hz: float = 30.0
    steps_per_chunk: int = 20
    max_chunks: int = 5
    arms: str = "both"
    shadow: bool = False
    instruction: str = "pick up the object and place it on the side"
    tick_budget_s: float = 0.1
    inference_timeout_s: float = 2.0
    max_chunk_rejections: int = 3
    liveness_window_s: float = 1.0
    liveness_min_motion: float = 0.02
    first_command_max_motion: float = 0.02
    start_pose_tolerance: float = 0.15
    table_z_in_base_frame: float | None = None
    min_grasp_clearance_m: float = 0.03
    can_interfaces: tuple[str, ...] = ("can_follower_l", "can_follower_r")
    hold_wait_s: float = 600.0
    skip_pose_checks: bool = False

    def __post_init__(self) -> None:
        if self.arms not in ("left", "right", "both"):
            raise ValueError("arms must be left, right or both")
        if self.skip_pose_checks and not self.shadow:
            raise ValueError("skip_pose_checks is only allowed in shadow mode")
        if not 1 <= self.steps_per_chunk <= 30:
            raise ValueError("steps_per_chunk must be within 1..30")


class KeyListener:
    """Reads operator commands from stdin in a background thread (Enter=hold, q, o, r, c)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._stream = stream if stream is not None else sys.stdin
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        try:
            for line in self._stream:
                self._q.put(line.strip().lower())
        except Exception:
            return

    def poll(self) -> str | None:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def push(self, key: str) -> None:
        self._q.put(key)


class CanErrorMonitor:
    """Watches SocketCAN error counters through sysfs (no-op where the files do not exist)."""

    def __init__(self, interfaces: tuple[str, ...]) -> None:
        self._files = {
            i: Path(f"/sys/class/net/{i}/statistics/{k}")
            for i in interfaces
            for k in ("rx_errors", "tx_errors")
            if Path(f"/sys/class/net/{i}/statistics/{k}").exists()
        }
        self._baseline = self.read()

    def read(self) -> dict[str, int]:
        out = {}
        for key, path in self._files.items():
            try:
                out[key] = int(path.read_text().strip())
            except Exception:
                pass
        return out

    def increased(self) -> str | None:
        now = self.read()
        for key, val in now.items():
            if val > self._baseline.get(key, 0):
                return (
                    f"CAN error counter increased on {key}: {self._baseline.get(key, 0)} -> {val}"
                )
        return None


class TickLogger:
    """One CSV row per tick plus a JSONL line per policy chunk."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        # Line-buffered: the rows before a hard process exit are the ones that matter.
        self._csv = open(out_dir / "ticks.csv", "w", newline="", encoding="utf-8", buffering=1)
        fields = [
            "t",
            "phase",
            "chunk",
            "step",
            "tick_ms",
            "infer_ms",
            "scale_l",
            "scale_r",
            "fault",
        ]
        fields += [f"base_{n}" for n in STATE_NAMES]
        fields += [f"target_{n}" for n in STATE_NAMES]
        fields += [f"meas_{n}" for n in STATE_NAMES]
        self._w = csv.DictWriter(self._csv, fieldnames=fields)
        self._w.writeheader()
        self._jsonl = open(out_dir / "chunks.jsonl", "w", encoding="utf-8")
        self._temps: TextIO | None = None
        self.rows = 0

    def tick(
        self,
        *,
        t: float,
        phase: str,
        chunk: int,
        step: int,
        tick_ms: float,
        infer_ms: float | None,
        scale: tuple[float, float],
        fault: str,
        base: np.ndarray | None,
        target: np.ndarray | None,
        measured: np.ndarray,
    ) -> None:
        row: dict[str, object] = {
            "t": round(t, 4),
            "phase": phase,
            "chunk": chunk,
            "step": step,
            "tick_ms": round(tick_ms, 2),
            "infer_ms": None if infer_ms is None else round(infer_ms, 1),
            "scale_l": round(scale[0], 3),
            "scale_r": round(scale[1], 3),
            "fault": fault,
        }
        for i, n in enumerate(STATE_NAMES):
            row[f"base_{n}"] = None if base is None else float(base[i])
            row[f"target_{n}"] = None if target is None else float(target[i])
            row[f"meas_{n}"] = float(measured[i])
        self._w.writerow(row)
        self.rows += 1

    def temps(self, t: float, snapshot: dict[str, dict[int, MotorFeedback]]) -> None:
        """One row of rotor/MOS temperatures and state codes per call (about once a second)."""
        ids = range(1, MOTORS_PER_ARM + 1)
        if self._temps is None:
            self._temps = open(self.out_dir / "temps.csv", "w", newline="", encoding="utf-8")
            header = ["t"] + [
                f"{arm}_m{i}_{k}"
                for arm in snapshot
                for i in ids
                for k in ("rotor", "mos", "state")
            ]
            self._temps.write(",".join(header) + "\n")
        cells = [f"{t:.2f}"]
        for motors in snapshot.values():
            for i in ids:
                fb = motors.get(i)
                cells += (
                    ["", "", ""]
                    if fb is None
                    else [f"{fb.temp_rotor:.0f}", f"{fb.temp_mos:.0f}", str(fb.state)]
                )
        self._temps.write(",".join(cells) + "\n")
        self._temps.flush()

    def chunk(self, index: int, obs: Observation, result: PolicyResult, verdict: list[str]) -> None:
        # Image writing is slow (~100 ms for three PNGs); do it off the control thread.
        threading.Thread(
            target=self._save_images,
            args=(index, obs.top.copy(), obs.left.copy(), obs.right.copy()),
            daemon=True,
        ).start()
        self._jsonl.write(
            json.dumps(
                {
                    "chunk": index,
                    "timestamp": obs.timestamp,
                    "state": obs.state.tolist(),
                    "actions": result.actions.tolist(),
                    "server_dt_ms": result.server_dt_ms,
                    "round_trip_ms": result.round_trip_ms,
                    "rejected": verdict,
                }
            )
            + "\n"
        )
        self._jsonl.flush()

    def _save_images(
        self, index: int, top: np.ndarray, left: np.ndarray, right: np.ndarray
    ) -> None:
        try:
            from PIL import Image

            img_dir = self.out_dir / "images"
            img_dir.mkdir(exist_ok=True)
            for role, img in (("top", top), ("left", left), ("right", right)):
                Image.fromarray(np.asarray(img, dtype=np.uint8)).save(
                    img_dir / f"chunk{index:03d}_{role}.png"
                )
        except Exception:  # noqa: BLE001 - diagnostics only
            pass

    def close(self, summary: dict[str, object]) -> None:
        self._csv.close()
        self._jsonl.close()
        if self._temps is not None:
            self._temps.close()
        with open(self.out_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)


@dataclass
class ExecutorReport:
    mode: str
    ok: bool
    fault: str = ""
    chunks_executed: int = 0
    ticks: int = 0
    late_ticks: int = 0
    max_tick_ms: float = 0.0
    tracking_max_ratio: float = 0.0
    extra: dict[str, object] = field(default_factory=dict)


class GuardedExecutor:
    def __init__(
        self,
        source: ObservationSource,
        policy: Policy | None,
        limits: SafetyLimits,
        cfg: ExecutorConfig,
        start_pose: np.ndarray,
        out_dir: Path,
        *,
        kinematics: ArmKinematics | None = None,
        keys: KeyListener | None = None,
        residual_fn: ResidualFn | None = None,
        motors: MotorMonitor | None = None,
        motor_limits: MotorLimits | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.source = source
        self.policy = policy
        self.limits = limits
        self.cfg = cfg
        self.start_pose = np.asarray(start_pose, dtype=np.float64)
        self.kin = kinematics
        self.keys = keys or KeyListener()
        self.residual_fn = residual_fn
        self.motors = motors
        self.motor_limits = motor_limits or MotorLimits()
        # Operator actions (return to start, open grippers) stay available on a hot arm, and a
        # shadow run sends nothing, so heat only stops what adds load.
        self.stop_when_hot = not cfg.shadow
        self.log = log
        self.logger = TickLogger(out_dir)
        self.can_monitor = CanErrorMonitor(cfg.can_interfaces)
        self.dt = 1.0 / cfg.hz
        self.last_target: np.ndarray | None = None
        self.held = False
        self.fault_reason = ""
        self._t0 = time.perf_counter()
        self._hist: deque[tuple[float, np.ndarray, np.ndarray]] = deque()
        self._fk_warned = False
        self._track_ratio = 0.0
        self.floor_clamped_ticks = {"left": 0, "right": 0}
        self._temps_logged_at = -1e9
        self._hot_warned_at = -1e9
        if self.kin is None or cfg.table_z_in_base_frame is None:
            self.log("[guard] WARNING: forward-kinematics height check DISABLED (table_z not set)")

    # ---- primitives -------------------------------------------------------------------

    def _now(self) -> float:
        return time.perf_counter() - self._t0

    def _send(self, target: np.ndarray) -> None:
        if not self.cfg.shadow:
            self.source.send_targets(target)
        self.last_target = np.asarray(target, dtype=np.float64).copy()

    def _fault(self, reason: str, measured: np.ndarray, *, release_grippers: bool = False) -> None:
        if self.held:
            return
        self.held = True
        self.fault_reason = reason
        self.log(f"[guard] FAULT -> HOLD: {reason}")
        # Release the stored PD force of the arm joints once, then hold the measured pose. The
        # grippers keep their last target so that a fault does not drop a held object
        # (2026-10-01); only the operator's Enter releases everything.
        hold = np.asarray(measured, dtype=np.float64).copy()
        if not release_grippers and self.last_target is not None:
            hold[GRIPPERS] = self.last_target[GRIPPERS]
        self._send(hold)

    def _motor_check(self) -> str | None:
        """Fault reason from the motor feedback (dead bus, motor error, too hot), or None."""
        if self.motors is None:
            return None
        for arm, why in self.motors.unavailable.items():
            # An arm nobody listens to must not pass as healthy.
            return f"motor feedback of the {arm} arm is not monitored ({why})"
        health = self.motors.check(self.motor_limits)
        now = self._now()
        if now - self._temps_logged_at >= 1.0:
            self._temps_logged_at = now
            self.logger.temps(now, self.motors.snapshot())
        if health.fault:
            return health.fault
        if health.warnings and now - self._hot_warned_at >= 10.0:
            self._hot_warned_at = now
            self.log(f"[guard] WARNING: hot motors: {'; '.join(health.warnings)}")
        if health.too_hot and self.stop_when_hot:
            return (
                f"motor too hot: {health.too_hot}; return to start now (r + Enter while holding, "
                "or the return-to-start mode), then rest the arms"
            )
        return None

    def _motor_fault_only(self) -> str | None:
        """Faults that make the RPC state stale (heat ignored), for modes that send nothing."""
        if self.motors is None:
            return None
        for arm, why in self.motors.unavailable.items():
            return f"motor feedback of the {arm} arm is not monitored ({why})"
        self.motors.wait_for_feedback(self.motor_limits.feedback_timeout_s)
        return self.motors.check(self.motor_limits).fault

    def _keep_above_table(self, target: np.ndarray) -> np.ndarray:
        """Lift a target whose fingertips would dip below the floor back onto it.

        This is a clamp, not a fault: the policy asks for heights slightly below the calibrated
        table plane when it grasps small objects, and stopping there ended every attempt.
        """
        if self.kin is None or self.cfg.table_z_in_base_frame is None:
            return target
        floor = self.cfg.table_z_in_base_frame + self.cfg.min_grasp_clearance_m
        out = target
        for name, arm in zip(("left", "right"), JOINTS, strict=True):
            if self.kin.grasp_height(target[arm]) >= floor:
                continue
            lifted = self.kin.lift_to(target[arm], floor)
            if lifted is not None:
                lifted = np.clip(lifted, self.limits.lower[arm], self.limits.upper[arm])
            if lifted is None or self.kin.grasp_height(lifted) < floor - 1e-4:
                # No small correction inside the joint bounds: this arm keeps its last target.
                lifted = (self.last_target if self.last_target is not None else target)[arm]
            if out is target:
                out = target.copy()
            out[arm] = lifted
            self.floor_clamped_ticks[name] += 1
            if self.floor_clamped_ticks[name] % 30 == 1:
                self.log(
                    f"[guard] {name} target kept at the table plane "
                    f"({self.floor_clamped_ticks[name]} ticks so far)"
                )
        return out

    def _monitor(self, measured: np.ndarray, moving: bool) -> str | None:
        """Return a fault reason or None. Called every tick, including during inference."""
        # Motors first: a dead follower server shows up below only as a tracking error.
        motor_reason = self._motor_check()
        if motor_reason:
            return motor_reason
        if self.last_target is not None and not self.cfg.shadow:
            # In shadow mode nothing was sent, so the arm cannot be expected to follow.
            tr = check_tracking(measured, self.last_target, self.limits)
            self._track_ratio = max(self._track_ratio, tr.worst_ratio)
            if not tr.ok:
                return (
                    f"tracking error {tr.errors[tr.worst_dim]:.3f} rad on {tr.worst_name} "
                    f"(limit {self.limits.tracking_err_max[tr.worst_dim]:.3f})"
                )
        bad = within_bounds(measured, self.limits)
        if bad and not self.cfg.skip_pose_checks:
            return f"measured pose outside bounds on {', '.join(bad)}"
        if self.limits.start_pose_deviation_max is not None and not self.cfg.skip_pose_checks:
            dev, dim = start_pose_deviation(measured, self.start_pose)
            if dev > self.limits.start_pose_deviation_max:
                return f"deviation {dev:.2f} rad from start pose on {STATE_NAMES[dim]}"
        err = self.can_monitor.increased()
        if err:
            return err
        send_errors = self.source.collect_send_errors()
        if send_errors:
            return f"command error: {send_errors[0]}"
        # Liveness: targets moved but the measured state did not change at all.
        t = self._now()
        if self.last_target is not None and not self.cfg.shadow:
            self._hist.append((t, measured.copy(), self.last_target.copy()))
            while self._hist and t - self._hist[0][0] > self.cfg.liveness_window_s:
                self._hist.popleft()
            if (
                moving
                and len(self._hist) >= 5
                and t - self._hist[0][0] >= self.cfg.liveness_window_s * 0.9
            ):
                m0, tg0 = self._hist[0][1], self._hist[0][2]
                # Joints only: a gripper stalled on a hard object keeps its target moving (down,
                # and back up at every re-plan) while its measured value and a still arm do not
                # move at all, and that is not a dead server. Gripper-only motion is covered by
                # the motor monitor.
                cmd_motion = float(np.max(np.abs((self.last_target - tg0)[JOINT_DIMS])))
                meas_motion = float(np.max(np.abs((measured - m0)[JOINT_DIMS])))
                if cmd_motion > self.cfg.liveness_min_motion and meas_motion < 1e-6:
                    return "state frozen while targets moved (server ignoring commands?)"
        return None

    def _sleep_until(self, t_next: float) -> None:
        remaining = t_next - time.perf_counter()
        if remaining > 0:
            time.sleep(remaining)

    def _apply_arm_mask(self, desired: np.ndarray) -> np.ndarray:
        """Only the selected arm(s) follow the policy; the other holds its current target."""
        if self.cfg.arms == "both" or self.last_target is None:
            return desired
        keep = "right" if self.cfg.arms == "left" else "left"
        out = desired.copy()
        out[ARM_SLICES[keep]] = self.last_target[ARM_SLICES[keep]]
        return out

    # ---- start-up -----------------------------------------------------------------

    def arm_and_first_command(self) -> np.ndarray:
        """Check the pose, then read again and send the first command (= measured) in one go."""
        if self.motors is not None:
            # The RPC state below is only trustworthy while the motors are being driven.
            self.motors.wait_for_feedback(self.motor_limits.feedback_timeout_s)
            motor_reason = self._motor_check()
            if motor_reason:
                raise RuntimeError(f"refusing to start: {motor_reason}")
        m1 = self.source.read_state_only().astype(np.float64)
        bad = within_bounds(m1, self.limits)
        if bad and not self.cfg.skip_pose_checks:
            raise RuntimeError(
                f"refusing to start: measured pose outside bounds on {', '.join(bad)}"
            )
        dev, dim = start_pose_deviation(m1, self.start_pose)
        if dev > self.cfg.start_pose_tolerance and not self.cfg.skip_pose_checks:
            raise RuntimeError(
                f"refusing to start: {STATE_NAMES[dim]} is {dev:.3f} rad from the start pose "
                f"(tolerance {self.cfg.start_pose_tolerance})"
            )
        m2 = self.source.read_state_only().astype(np.float64)
        moved = float(np.max(np.abs(m2 - m1)))
        if moved > self.cfg.first_command_max_motion:
            raise RuntimeError(f"refusing to start: arm moved {moved:.3f} rad between checks")
        self._send(m2)
        self.log(f"[guard] first command sent (= measured). shadow={self.cfg.shadow}")
        return m2

    def calibrate_table(self) -> dict[str, float]:
        """Report the grasp-site position of both arms at the current pose (nothing is sent).

        Put the closed gripper fingertips on the table by hand, then run this: the reported
        z is the table plane in the base frame (``execution.table_z_in_base_frame``).
        """
        if self.kin is None:
            raise RuntimeError("kinematics unavailable")
        dead = self._motor_fault_only()
        if dead:
            raise RuntimeError(f"the reported pose cannot be trusted: {dead}")
        m = self.source.read_state_only().astype(np.float64)
        out: dict[str, float] = {}
        for name, arm in zip(("left", "right"), JOINTS, strict=True):
            pos = self.kin.grasp_position(m[arm])
            out[f"{name}_grasp_x"] = float(pos[0])
            out[f"{name}_grasp_y"] = float(pos[1])
            out[f"{name}_grasp_z"] = float(pos[2])
        return out

    def pose_guide(self, seconds: float = 180.0, settle_s: float = 2.0) -> bool:
        """Print the per-joint deviation from the start pose until the arms are within tolerance.

        Nothing is sent. Returns True when both arms stayed within tolerance for ``settle_s``.
        """
        tol = self.cfg.start_pose_tolerance
        ok_since: float | None = None
        t_end = time.perf_counter() + seconds
        while time.perf_counter() < t_end:
            m = self.source.read_state_only().astype(np.float64)
            lines = []
            worst = 0.0
            for name, arm in zip(("LEFT ", "RIGHT"), JOINTS, strict=True):
                cells = []
                for j, dim in enumerate(range(arm.start, arm.stop)):
                    d = m[dim] - self.start_pose[dim]
                    worst = max(worst, abs(d))
                    mark = "ok " if abs(d) <= tol else ("+++" if d > 0 else "---")
                    cells.append(f"j{j + 1}:{d:+.2f}{mark}")
                lines.append(f"{name} " + "  ".join(cells))
            status = (
                "WITHIN TOLERANCE - hold still"
                if worst <= tol
                else f"max dev {worst:.2f} rad (tol {tol})"
            )
            dead = self._motor_fault_only()
            if dead:
                status, worst = f"POSE NOT TRUSTWORTHY: {dead}", float("inf")
            self.log(
                "\033[2J\033[H"
                + "\n".join(lines)
                + f"\n{status}\n"
                + "(+++ = joint above target, --- = below; move it the other way)"
            )
            if worst <= tol:
                ok_since = ok_since or time.perf_counter()
                if time.perf_counter() - ok_since >= settle_s:
                    return True
            else:
                ok_since = None
            time.sleep(0.25)
        return False

    # ---- operator actions ---------------------------------------------------------------

    def _ramp_to(
        self, desired: np.ndarray, *, label: str, timeout_s: float, tol: float = 1e-6
    ) -> ExecutorReport:
        """Move the target toward ``desired`` under the rate limiter with monitoring."""
        rep = ExecutorReport(mode=label, ok=True)
        t_next = time.perf_counter()
        while True:
            t_start = time.perf_counter()
            measured = self.source.read_state_only().astype(np.float64)
            reason = self._monitor(measured, moving=True)
            assert self.last_target is not None
            res = rate_limit(desired, self.last_target, self.limits)
            target = self._keep_above_table(res.target)
            if reason:
                self._fault(reason, measured)
                rep.ok, rep.fault = False, reason
            else:
                self._send(target)
            tick_ms = (time.perf_counter() - t_start) * 1000
            rep.max_tick_ms = max(rep.max_tick_ms, tick_ms)
            self.logger.tick(
                t=self._now(),
                phase=label,
                chunk=-1,
                step=-1,
                tick_ms=tick_ms,
                infer_ms=None,
                scale=res.scale,
                fault=reason or "",
                base=desired,
                target=self.last_target,
                measured=measured,
            )
            if reason:
                break
            rep.ticks += 1
            if np.max(np.abs(target - desired)) < tol:
                break
            if rep.ticks * self.dt > timeout_s:
                rep.ok, rep.fault = False, f"{label}: timeout after {timeout_s}s"
                break
            t_next += self.dt
            self._sleep_until(t_next)
        return rep

    def _settle(self, seconds: float) -> str | None:
        """Keep monitoring (and re-sending the held target) for ``seconds``; return a fault."""
        n = max(1, int(seconds * self.cfg.hz))
        t_next = time.perf_counter()
        for _ in range(n):
            measured = self.source.read_state_only().astype(np.float64)
            reason = self._monitor(measured, moving=False)
            if reason:
                self._fault(reason, measured)
                return reason
            assert self.last_target is not None
            self._send(self.last_target)
            t_next += self.dt
            self._sleep_until(t_next)
        return None

    def open_grippers(self) -> ExecutorReport:
        assert self.last_target is not None
        desired = self.last_target.copy()
        for g in GRIPPERS:
            desired[g] = 1.0
        return self._operator_ramp(desired, label="open_grippers", timeout_s=10.0)

    def return_to_start(self) -> ExecutorReport:
        assert self.last_target is not None
        desired = np.asarray(self.start_pose, dtype=np.float64).copy()
        desired[GRIPPERS] = self.last_target[GRIPPERS]  # a held object stays held; 'o' opens
        return self._operator_ramp(desired, label="return_to_start", timeout_s=60.0)

    def _operator_ramp(
        self, desired: np.ndarray, *, label: str, timeout_s: float
    ) -> ExecutorReport:
        """Ramp on the operator's request. Heat does not block it: it is how the load is reduced."""
        self.held = False
        stop_when_hot, self.stop_when_hot = self.stop_when_hot, False
        try:
            return self._ramp_to(desired, label=label, timeout_s=timeout_s)
        finally:
            self.stop_when_hot = stop_when_hot

    # ---- test modes -----------------------------------------------------------------

    def hold_test(self, seconds: float) -> ExecutorReport:
        """Send one fixed target (a fresh measured snapshot) and record the steady-state error."""
        rep = ExecutorReport(mode="hold", ok=True)
        target = self.arm_and_first_command()
        errs: list[np.ndarray] = []
        t_next = time.perf_counter()
        n = int(seconds * self.cfg.hz)
        for i in range(n):
            t_start = time.perf_counter()
            measured = self.source.read_state_only().astype(np.float64)
            reason = self._monitor(measured, moving=False)
            if reason and reason.startswith("tracking error"):
                reason = (
                    None  # the hold test MEASURES the steady-state error, it does not fault on it
                )
            if reason:
                self._fault(reason, measured)
                rep.ok, rep.fault = False, reason
                break
            self._send(target)
            errs.append(np.abs(target - measured))
            key = self.keys.poll()
            if key in ("q", ""):
                rep.fault = f"operator key {key!r}"
                break
            tick_ms = (time.perf_counter() - t_start) * 1000
            rep.max_tick_ms = max(rep.max_tick_ms, tick_ms)
            self.logger.tick(
                t=self._now(),
                phase="hold",
                chunk=-1,
                step=i,
                tick_ms=tick_ms,
                infer_ms=None,
                scale=(1.0, 1.0),
                fault="",
                base=target,
                target=target,
                measured=measured,
            )
            rep.ticks += 1
            t_next += self.dt
            self._sleep_until(t_next)
        if errs:
            e = np.array(errs)
            rep.extra = {
                "steady_state_err_mean": np.round(e[len(e) // 2 :].mean(0), 4).tolist(),
                "steady_state_err_max": np.round(e[len(e) // 2 :].max(0), 4).tolist(),
            }
        rep.tracking_max_ratio = self._track_ratio
        return rep

    def direction_test(self, arm: str, delta: float = 0.05, hold_s: float = 0.5) -> ExecutorReport:
        """Move each joint of one arm by +delta and back, one at a time, and verify the sign."""
        rep = ExecutorReport(mode=f"direction_test:{arm}", ok=True)
        base = self.arm_and_first_command()
        sl = ARM_SLICES[arm]
        results: dict[str, object] = {}
        for j in range(6):
            dim = sl.start + j
            m0 = self.source.read_state_only().astype(np.float64)
            desired = base.copy()
            desired[dim] = float(
                np.clip(base[dim] + delta, self.limits.lower[dim], self.limits.upper[dim])
            )
            self.log(f"[direction] {STATE_NAMES[dim]}: +{delta:.3f} rad ...")
            r1 = self._ramp_to(desired, label=f"dir+{STATE_NAMES[dim]}", timeout_s=10.0)
            if not r1.ok:
                rep.ok, rep.fault = False, r1.fault
                break
            if self._settle(hold_s):
                rep.ok, rep.fault = False, self.fault_reason
                break
            m1 = self.source.read_state_only().astype(np.float64)
            moved = m1 - m0
            others = np.delete(np.abs(moved), [dim, 6, 13])
            results[STATE_NAMES[dim]] = {
                "commanded": round(desired[dim] - base[dim], 4),
                "measured_delta": round(float(moved[dim]), 4),
                "max_other_joint_delta": round(float(others.max()), 4),
                "sign_ok": bool(
                    np.sign(moved[dim]) == np.sign(desired[dim] - base[dim])
                    and abs(moved[dim]) > abs(delta) * 0.5
                ),
            }
            self.log(f"[direction] {STATE_NAMES[dim]}: {results[STATE_NAMES[dim]]}")
            r2 = self._ramp_to(base, label=f"dir-{STATE_NAMES[dim]}", timeout_s=10.0)
            if not r2.ok:
                rep.ok, rep.fault = False, r2.fault
                break
            if self._settle(hold_s):
                rep.ok, rep.fault = False, self.fault_reason
                break
            if self.keys.poll() in ("q", ""):
                rep.fault = "operator stop"
                break
        rep.extra = results
        if any(not r["sign_ok"] for r in results.values()):  # type: ignore[index]
            rep.ok = False
            rep.fault = rep.fault or "at least one joint did not move as commanded"
        return rep

    # ---- main loop -----------------------------------------------------------------------

    def run(self) -> ExecutorReport:
        """Stop-and-go execution: observe -> infer (worker thread) -> execute N steps -> repeat."""
        if self.policy is None:
            raise ValueError("run() needs a policy")
        rep = ExecutorReport(mode="shadow" if self.cfg.shadow else "run", ok=True)
        self.arm_and_first_command()
        pool = ThreadPoolExecutor(max_workers=1)
        future: Future[PolicyResult] | None = None
        obs: Observation | None = None
        chunk: np.ndarray | None = None
        step = 0
        rejections = 0
        phase = "observe"
        infer_start = 0.0
        last_infer_ms: float | None = None
        held_since: float | None = None
        t_next = time.perf_counter()
        try:
            while True:
                t_start = time.perf_counter()
                measured = self.source.read_state_only().astype(np.float64)
                base: np.ndarray | None = None
                target: np.ndarray | None = None
                scale = (1.0, 1.0)
                fault = ""

                # One poll per tick, dispatched by state (a second poll while held dropped keys).
                key = self.keys.poll()
                if key == "q":
                    if not self.held:
                        rep.fault = "operator quit"
                    break
                if key == "" and not self.held:
                    self._fault("operator hold (Enter)", measured, release_grippers=True)
                elif key == "o" and self.held:
                    self.open_grippers()
                    self.held = True
                elif key == "r" and self.held:
                    self.return_to_start()
                    self.held = True

                if not self.held:
                    reason = self._monitor(measured, moving=(phase == "execute"))
                    if reason:
                        self._fault(reason, measured)
                        rep.ok, rep.fault = False, reason

                if not self.held:
                    if phase == "observe":
                        obs = self.source.read()
                        infer_start = time.perf_counter()
                        future = pool.submit(self.policy.act, obs, self.cfg.instruction)
                        phase = "infer"
                    elif phase == "infer":
                        assert future is not None and obs is not None
                        if future.done():
                            last_infer_ms = (time.perf_counter() - infer_start) * 1000
                            try:
                                result = future.result()
                            except Exception as exc:  # noqa: BLE001
                                self._fault(f"inference failed: {exc}", measured)
                                rep.ok, rep.fault = False, f"inference failed: {exc}"
                            else:
                                verdict = validate_chunk_for_execution(
                                    result.actions, measured, self.limits
                                )
                                if self.cfg.skip_pose_checks and not verdict.ok:
                                    # Shadow from an arbitrary pose: bounds violations are expected.
                                    kept = [
                                        r for r in verdict.reasons if "absolute bounds" not in r
                                    ]
                                    if not kept:
                                        self.log(f"[guard] (shadow) ignoring: {verdict.reasons}")
                                    verdict.ok, verdict.reasons = not kept, kept
                                self.logger.chunk(
                                    rep.chunks_executed,
                                    obs,
                                    result,
                                    [] if verdict.ok else verdict.reasons,
                                )
                                if verdict.ok:
                                    chunk, step, phase = (
                                        result.actions.astype(np.float64),
                                        0,
                                        "execute",
                                    )
                                    self.log(
                                        f"[guard] chunk {rep.chunks_executed} accepted "
                                        f"({last_infer_ms:.0f} ms)"
                                    )
                                else:
                                    rejections += 1
                                    self.log(
                                        f"[guard] chunk rejected ({rejections}): {verdict.reasons}"
                                    )
                                    if rejections >= self.cfg.max_chunk_rejections:
                                        self._fault("too many rejected chunks", measured)
                                        rep.ok, rep.fault = False, "too many rejected chunks"
                                    else:
                                        phase = "observe"
                        elif time.perf_counter() - infer_start > self.cfg.inference_timeout_s:
                            self._fault("inference timeout", measured)
                            rep.ok, rep.fault = False, "inference timeout"
                    if phase == "execute" and not self.held:
                        assert (
                            chunk is not None and self.last_target is not None and obs is not None
                        )
                        base = chunk[step]
                        desired = apply_residual(
                            base,
                            self.residual_fn(obs, base) if self.residual_fn else None,
                            self.limits,
                        )
                        desired = self._apply_arm_mask(desired)
                        res = rate_limit(desired, self.last_target, self.limits)
                        target, scale = self._keep_above_table(res.target), res.scale
                        self._send(target)
                        step += 1
                        if step >= self.cfg.steps_per_chunk:
                            rep.chunks_executed += 1
                            phase = "observe"
                            if rep.chunks_executed >= self.cfg.max_chunks:
                                self.log(f"[guard] done: {rep.chunks_executed} chunks")
                                break

                tick_ms = (time.perf_counter() - t_start) * 1000
                rep.ticks += 1
                rep.max_tick_ms = max(rep.max_tick_ms, tick_ms)
                if tick_ms > self.cfg.tick_budget_s * 1000:
                    rep.late_ticks += 1
                    if not self.held:
                        self._fault(f"tick took {tick_ms:.0f} ms", measured)
                        rep.ok, rep.fault = False, f"tick took {tick_ms:.0f} ms"
                self.logger.tick(
                    t=self._now(),
                    phase="held" if self.held else phase,
                    chunk=rep.chunks_executed,
                    step=step,
                    tick_ms=tick_ms,
                    infer_ms=last_infer_ms,
                    scale=scale,
                    fault=self.fault_reason if self.held else fault,
                    base=base,
                    target=target,
                    measured=measured,
                )
                last_infer_ms = None
                if self.held:
                    # Stay responsive to operator keys while holding. The follower servers keep
                    # the last target on their own, so leaving after hold_wait_s is safe.
                    self._motor_check()  # keeps temps.csv and the heat warnings going
                    if held_since is None:
                        held_since = time.perf_counter()
                    elif time.perf_counter() - held_since > self.cfg.hold_wait_s:
                        self.log(
                            "[guard] hold wait elapsed; exiting. The servers keep this pose and "
                            "the motors keep heating in it: run return-to-start, then rest the arms"
                        )
                        break
                t_next += self.dt
                self._sleep_until(t_next)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        rep.tracking_max_ratio = self._track_ratio
        return rep

    def close(self, rep: ExecutorReport) -> None:
        self.logger.close(
            {
                "mode": rep.mode,
                "ok": rep.ok,
                "fault": rep.fault,
                "chunks_executed": rep.chunks_executed,
                "ticks": rep.ticks,
                "late_ticks": rep.late_ticks,
                "max_tick_ms": round(rep.max_tick_ms, 1),
                "tracking_max_ratio": round(max(rep.tracking_max_ratio, self._track_ratio), 3),
                "motor_temp_max": None if self.motors is None else self.motors.max_temperatures(),
                "floor_clamped_ticks": self.floor_clamped_ticks,
                "shadow": self.cfg.shadow,
                "arms": self.cfg.arms,
                "steps_per_chunk": self.cfg.steps_per_chunk,
                "max_step_joint": self.limits.max_step_joint,
                "extra": rep.extra,
            }
        )
