#!/usr/bin/env python
"""Gate G5: run MolmoAct2 on the real YAM arms through the guarded executor.

Modes (run them in this order, each with two people and the power strip within reach):

  direction-test  no policy; moves each joint of one arm +0.05 rad and back, one at a time
  hold            no policy; holds a fresh measured snapshot for N seconds, reports tracking error
  shadow          full loop with the policy but NOTHING is sent to the arms
  run             full loop, actions are sent (rate-limited, bounded, monitored)
  temps           no policy, nothing sent; prints motor temperatures from the CAN feedback

    python scripts/run_policy_guarded.py --mode hold --config configs/dry_run.yaml --seconds 30
    python scripts/run_policy_guarded.py --mode run --arms left --max-step 0.005 --chunks 5

Operator keys while running: Enter = hold, q = quit, o = open grippers, r = return to start.
The motors are watched on the CAN buses (listen-only): a silent bus, a motor error or a motor
above safety.motor_temp_stop_c stops the run; return-to-start stays available on a hot arm.
Requires the follower servers (setup_bi_yam_servers --eval) and, for shadow/run, the FastAPI
policy server on port 8202.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dryrun.backends import FastApiPolicy  # noqa: E402
from dryrun.config import DryRunConfig  # noqa: E402
from dryrun.executor import (  # noqa: E402
    ExecutorConfig,
    ExecutorReport,
    GuardedExecutor,
    KeyListener,
)
from dryrun.kinematics import ArmKinematics  # noqa: E402
from dryrun.motor_monitor import MotorLimits, MotorMonitor  # noqa: E402
from dryrun.safety import SafetyLimits  # noqa: E402
from dryrun.sensors import FakeSource, LeRobotSource, ObservationSource  # noqa: E402
from dryrun.yam_stats import DEFAULT_START_POSE  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=[
            "calibrate-table",
            "pose-guide",
            "direction-test",
            "hold",
            "shadow",
            "run",
            "return-to-start",
            "open-grippers",
            "temps",
        ],
    )
    p.add_argument("--config", default="configs/dry_run.yaml")
    p.add_argument("--arms", default="both", choices=["left", "right", "both"])
    p.add_argument("--max-step", type=float, default=None, help="rad per tick (overrides config)")
    p.add_argument("--steps-per-chunk", type=int, default=20)
    p.add_argument("--chunks", type=int, default=5)
    p.add_argument("--seconds", type=float, default=30.0, help="hold test / temps duration")
    p.add_argument("--delta", type=float, default=0.05, help="direction test amplitude [rad]")
    p.add_argument("--instruction", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--fake", action="store_true", help="simulated arms/cameras (no hardware)")
    p.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    p.add_argument(
        "--hold-wait",
        type=float,
        default=5.0,
        help="seconds to stay in the loop after a fault (servers keep the pose either way)",
    )
    p.add_argument(
        "--start-pose-tolerance", type=float, default=None, help="rad per joint (overrides config)"
    )
    p.add_argument(
        "--unsafe-skip-pose-checks",
        action="store_true",
        help="shadow mode only: run the loop from any pose (nothing is sent)",
    )
    return p.parse_args(argv)


def confirm(text: str, auto: bool) -> None:
    print(text)
    if auto:
        return
    answer = input("Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise SystemExit("aborted")


def print_temps(monitor: MotorMonitor, limits: MotorLimits, seconds: float) -> int:
    """Print motor states and temperatures once a second. Nothing is sent to the arms."""
    monitor.start()
    try:
        for arm, why in monitor.unavailable.items():
            print(f"[temps] {arm} arm not monitored ({why})")
        if not monitor.watched:
            return 1
        print(
            f"[temps] rotor/MOS in C per motor (m1..m6 = joints, m7 = gripper) "
            f"for {seconds:.0f} s; Ctrl-C stops earlier"
        )
        monitor.wait_for_feedback(limits.feedback_timeout_s)
        t_end = time.monotonic() + seconds
        while True:
            print(f"--- {time.strftime('%H:%M:%S')}\n{monitor.format_table()}")
            health = monitor.check(limits)
            for line in [health.fault, health.too_hot or "; ".join(health.warnings)]:
                if line:
                    print(f"[temps] \a{line}")
            if time.monotonic() >= t_end:
                return 1 if health.fault or health.too_hot else 0
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        monitor.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = DryRunConfig.load(args.config)
    safety_raw = dict(cfg.safety)
    if args.max_step is not None:
        safety_raw["max_step_joint"] = args.max_step
    limits = SafetyLimits.from_mapping(safety_raw)
    motor_limits = MotorLimits.from_mapping(safety_raw)
    start_pose = np.array(cfg.start_pose) if cfg.start_pose else DEFAULT_START_POSE.copy()
    shadow = args.mode == "shadow"
    ecfg = ExecutorConfig(
        steps_per_chunk=args.steps_per_chunk,
        max_chunks=args.chunks,
        arms=args.arms,
        shadow=shadow,
        skip_pose_checks=args.unsafe_skip_pose_checks,
        hold_wait_s=args.hold_wait,
        instruction=(args.instruction or cfg.instruction).strip(),
        start_pose_tolerance=(
            args.start_pose_tolerance
            if args.start_pose_tolerance is not None
            else cfg.start_pose_tolerance
        ),
        table_z_in_base_frame=cfg.table_z_in_base_frame,
        min_grasp_clearance_m=cfg.min_grasp_clearance_m,
    )
    monitor = MotorMonitor(dict(zip(("left", "right"), ecfg.can_interfaces, strict=True)))
    if args.mode == "temps":
        return print_temps(monitor, motor_limits, args.seconds)
    out_dir = Path(args.out_dir or f"logs/raw/g5_{args.mode}_{time.strftime('%Y%m%d-%H%M%S')}")

    source: ObservationSource
    if args.fake:
        source = FakeSource(cfg.cameras, initial_state=start_pose)  # type: ignore[assignment]
    else:
        source = LeRobotSource(
            cfg.cameras,
            left_port=cfg.left_arm_port,
            right_port=cfg.right_arm_port,
            host=cfg.arm_host,
            with_cameras=args.mode in ("shadow", "run"),
        )
    policy = None
    if args.mode in ("shadow", "run"):
        policy = FastApiPolicy(
            cfg.server_url, normalization_tag=cfg.normalization_tag, num_steps=cfg.num_steps
        )
    kin = None
    try:
        kin = ArmKinematics(cfg.kinematics_xml) if cfg.kinematics_xml else ArmKinematics()
    except Exception as exc:  # noqa: BLE001
        print(f"[guard] kinematics unavailable ({exc}); height check disabled")

    banner = (
        f"\n=== GUARDED EXECUTOR: mode={args.mode} arms={args.arms} shadow={shadow} ===\n"
        f"max_step_joint={limits.max_step_joint} rad/tick  "
        f"max_step_gripper={limits.max_step_gripper}\n"
        f"tracking limits={limits.tracking_err_max[:7].tolist()}  "
        f"bounds margin={limits.joint_margin}\n"
        f"steps_per_chunk={ecfg.steps_per_chunk} chunks={ecfg.max_chunks} "
        f"instruction={ecfg.instruction!r}\n"
        f"start_pose={np.round(start_pose, 3).tolist()}\n"
        f"table_z={ecfg.table_z_in_base_frame} (None = height check off)\n"
        f"motor temperature: warn {motor_limits.temp_warn_c:.0f} C, "
        f"stop {motor_limits.temp_stop_c:.0f} C\n"
        f"output -> {out_dir}\n"
        "Both arms must be held by hand at the start pose. "
        "Keys: Enter=hold q=quit o=open r=return\n"
    )
    confirm(banner, auto=args.yes or args.fake)

    keys = KeyListener()
    keys.start()
    source.connect()
    if not args.fake:
        monitor.start()
    ex = GuardedExecutor(
        source,
        policy,
        limits,
        ecfg,
        start_pose,
        out_dir,
        kinematics=kin,
        keys=keys,
        motors=None if args.fake else monitor,
        motor_limits=motor_limits,
    )
    try:
        if args.mode == "calibrate-table":
            vals = ex.calibrate_table()
            print("[guard] grasp positions at current pose [m]:")
            for k, v in vals.items():
                print(f"  {k}: {v:.4f}")
            print(
                "[guard] with the closed fingertips ON the table, set "
                "execution.table_z_in_base_frame to the reported *_grasp_z (lower of the two)."
            )
            source.close()
            return 0
        if args.mode == "pose-guide":
            ok = ex.pose_guide(seconds=args.seconds if args.seconds > 30 else 180.0)
            source.close()
            print("[guard] pose-guide:", "READY" if ok else "not within tolerance")
            return 0 if ok else 1
        if args.mode in ("return-to-start", "open-grippers"):
            # Operator actions from ANY pose: first command = measured, then a rate-limited ramp.
            ex.cfg.start_pose_tolerance = 10.0
            ex.limits.start_pose_deviation_max = None
            ex.stop_when_hot = False
            ex.arm_and_first_command()
            rep = ex.return_to_start() if args.mode == "return-to-start" else ex.open_grippers()
        elif args.mode == "direction-test":
            arm = args.arms if args.arms != "both" else "left"
            rep = ex.direction_test(arm, delta=args.delta)
        elif args.mode == "hold":
            rep = ex.hold_test(args.seconds)
        else:
            rep = ex.run()
    except RuntimeError as exc:
        # Start refusals and lost follower servers: leave a summary instead of a traceback.
        print(f"[guard] ABORTED: {exc}")
        rep = ExecutorReport(mode=args.mode, ok=False, fault=str(exc))
    finally:
        source.close()
        monitor.close()
    ex.close(rep)
    print(
        f"\n[guard] {rep.mode}: ok={rep.ok} fault={rep.fault!r} chunks={rep.chunks_executed} "
        f"ticks={rep.ticks} late={rep.late_ticks} max_tick={rep.max_tick_ms:.0f} ms "
        f"tracking_max_ratio={rep.tracking_max_ratio:.2f}"
    )
    if rep.extra:
        print(f"[guard] extra: {rep.extra}")
    for arm, temps in monitor.max_temperatures().items():
        print(f"[guard] {arm} motor max temperature [C]: rotor {temps['rotor']} MOS {temps['mos']}")
    print(f"[guard] logs: {out_dir}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
