#!/usr/bin/env python
"""Trace one arm's target/measured joints, tracking error and grasp height through a run.

python scripts/trace_g5_run.py <run_dir> --arm right --every 6
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dryrun import STATE_NAMES  # noqa: E402
from dryrun.kinematics import ArmKinematics  # noqa: E402

THR = np.array([0.05, 0.06, 0.08, 0.10, 0.10, 0.10, 0.2] * 2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--arm", choices=["left", "right"], default="right")
    p.add_argument("--every", type=int, default=6)
    p.add_argument("--xml", default=None)
    a = p.parse_args()
    kin = ArmKinematics(a.xml) if a.xml else ArmKinematics()
    sl = slice(0, 7) if a.arm == "left" else slice(7, 14)
    rows = [
        r for r in csv.DictReader((Path(a.run_dir) / "ticks.csv").open()) if r["phase"] != "held"
    ]
    print(f"non-held ticks: {len(rows)}  arm={a.arm}")
    print("   t   phase   j2 tgt  j2 meas  err_j2  err_j3  err_j4  grip tgt/meas  grasp_z   scale")
    for r in rows[:: a.every]:
        if not r[f"target_{STATE_NAMES[sl.start]}"]:
            continue
        tg = np.array([float(r[f"target_{n}"]) for n in STATE_NAMES])[sl]
        me = np.array([float(r[f"meas_{n}"]) for n in STATE_NAMES])[sl]
        z = kin.grasp_height(me[:6])
        scale = r["scale_l"] if a.arm == "left" else r["scale_r"]
        t = float(r["t"])
        print(
            f"{t:6.2f} {r['phase'][:7]:7s} {tg[1]:+.3f}  {me[1]:+.3f}  {tg[1] - me[1]:+.3f}  "
            f"{tg[2] - me[2]:+.3f}  {tg[3] - me[3]:+.3f}   {tg[6]:.2f}/{me[6]:.2f}     "
            f"{z:+.3f}   {scale}"
        )
    with_target = [r for r in rows if r[f"target_{STATE_NAMES[sl.start]}"]]
    if not with_target:
        print("no target rows (the run never executed a step)")
        return 0
    last = with_target[-1]
    tg = np.array([float(last[f"target_{n}"]) for n in STATE_NAMES])[sl]
    me = np.array([float(last[f"meas_{n}"]) for n in STATE_NAMES])[sl]
    ratio = np.abs(tg - me) / THR[sl]
    print("last target  :", np.round(tg, 3).tolist())
    print("last measured:", np.round(me, 3).tolist())
    print("tracking ratio per dim:", np.round(ratio, 2).tolist())
    print("grasp xyz at end:", np.round(kin.grasp_position(me[:6]), 3).tolist())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
