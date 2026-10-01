#!/usr/bin/env python
"""Gate G4: query the MolmoAct2 policy with real observations and log actions. NO ACTUATION.

Run inside the ``lerobot-client`` venv on the DGX Spark, with the follower servers
(``setup_bi_yam_servers --eval``) and the official FastAPI server (port 8202) running.

    python scripts/dry_run_no_actuation.py --config configs/dry_run.yaml --queries 20

Use ``--fake-sensors`` to smoke-test the server without cameras or arms (Gate G3).
This script never calls ``command_joint_pos`` or any other motor command.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dryrun.backends import FastApiPolicy  # noqa: E402
from dryrun.checks import JointLimits, validate_action_chunk  # noqa: E402
from dryrun.config import DryRunConfig  # noqa: E402
from dryrun.recorder import Recorder  # noqa: E402
from dryrun.sensors import FakeSource, LeRobotSource, ObservationSource  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--config", required=True, help="YAML config (see configs/dry_run.example.yaml)")
    p.add_argument("--queries", type=int, default=10, help="number of policy queries")
    p.add_argument("--period", type=float, default=1.0, help="seconds between queries")
    p.add_argument("--out-dir", default=None, help="default: logs/raw/dry_run_<timestamp>")
    p.add_argument(
        "--fake-sensors", action="store_true", help="synthetic images/state, no hardware"
    )
    p.add_argument("--no-images", action="store_true", help="do not save captured PNGs")
    p.add_argument("--instruction", default=None, help="override the task instruction")
    p.add_argument(
        "--max-failures", type=int, default=3, help="stop after this many consecutive query errors"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = DryRunConfig.load(args.config)
    instruction = args.instruction or cfg.instruction
    out_dir = Path(args.out_dir or f"logs/raw/dry_run_{time.strftime('%Y%m%d-%H%M%S')}")

    source: ObservationSource
    if args.fake_sensors:
        source = FakeSource(cfg.cameras)
    else:
        source = LeRobotSource(
            cfg.cameras,
            left_port=cfg.left_arm_port,
            right_port=cfg.right_arm_port,
            host=cfg.arm_host,
        )
    policy = FastApiPolicy(
        cfg.server_url, normalization_tag=cfg.normalization_tag, num_steps=cfg.num_steps
    )
    limits = JointLimits.from_mapping(cfg.joint_limits) if cfg.joint_limits else None
    recorder = Recorder(out_dir, save_images=not args.no_images)

    print(f"[dry-run] NO ACTUATION. server={policy.url} instruction={instruction!r}")
    print(f"[dry-run] output -> {out_dir}")
    failures = 0
    source.connect()
    try:
        for step in range(args.queries):
            t_start = time.perf_counter()
            obs = source.read()
            try:
                result = policy.act(obs, instruction)
            except Exception as exc:  # noqa: BLE001 - one failed query must not end the run
                failures += 1
                print(f"[ERR] step={step} policy query failed: {exc}")
                if failures >= args.max_failures:
                    print(f"[dry-run] stopping after {failures} consecutive failures")
                    break
                continue
            failures = 0
            report = validate_action_chunk(
                result.actions,
                obs.state,
                limits=limits,
                max_first_step_delta=cfg.max_first_step_delta,
                max_step_delta=cfg.max_step_delta,
            )
            row = recorder.record(step, obs, result, report)
            status = "OK " if report.ok else "NG "
            print(
                f"[{status}] step={step} shape={row['shape']} rt={row['round_trip_ms']}ms "
                f"server={row['server_dt_ms']}ms first_delta={row['max_first_step_delta']:.3f} "
                f"({row['argmax_first_step_delta']}) {row['issues']}"
            )
            sleep = args.period - (time.perf_counter() - t_start)
            if sleep > 0:
                time.sleep(sleep)
    finally:
        source.close()
        png = recorder.plot()
        summary = recorder.write_summary_json(
            {
                "instruction": instruction,
                "server_url": policy.url,
                "fake_sensors": args.fake_sensors,
            }
        )
        print(f"[dry-run] summary -> {summary}")
        if png:
            print(f"[dry-run] plot    -> {png}")
        print(f"[dry-run] latency  {recorder.latency_summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
