#!/usr/bin/env python
"""Summarize a guarded-executor run directory (ticks.csv, chunks.jsonl, summary.json)."""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

import numpy as np

NAMES = [f"L_j{i}" for i in range(1, 7)] + ["L_g"] + [f"R_j{i}" for i in range(1, 7)] + ["R_g"]


def main(argv: list[str]) -> int:
    d = Path(argv[1])
    summary = json.loads((d / "summary.json").read_text())
    print(
        "summary:",
        {
            k: summary[k]
            for k in (
                "mode",
                "ok",
                "fault",
                "chunks_executed",
                "ticks",
                "late_ticks",
                "max_tick_ms",
                "tracking_max_ratio",
            )
        },
    )
    chunks = [
        json.loads(line) for line in (d / "chunks.jsonl").read_text().splitlines() if line.strip()
    ]
    for r in chunks:
        a = np.array(r["actions"])
        s = np.array(r["state"])
        step = np.abs(np.diff(a, axis=0)).max(0)
        end = a[-1] - s
        print(f"chunk {r['chunk']}: rt {r['round_trip_ms']:.0f} ms rejected={r['rejected']}")
        print(f"   |a0-state| max: {float(np.abs(a[0] - s).max()):.3f}")
        print(
            "   end-of-chunk delta > 0.03:",
            {n: round(float(v), 3) for n, v in zip(NAMES, end, strict=True) if abs(v) > 0.03},
        )
        print(
            "   per-step jump > 0.01:",
            {n: round(float(v), 3) for n, v in zip(NAMES, step, strict=True) if v > 0.01},
        )
    rows = list(csv.DictReader((d / "ticks.csv").open()))
    t = [float(x["tick_ms"]) for x in rows]
    if t:
        p95 = sorted(t)[int(len(t) * 0.95)]
        med = statistics.median(t)
        print(f"ticks {len(t)}  tick_ms median {med:.1f}  p95 {p95:.1f}  max {max(t):.1f}")
    ex = [x for x in rows if x["phase"] == "execute"]
    if ex:
        sl = [float(x["scale_l"]) for x in ex]
        sr = [float(x["scale_r"]) for x in ex]
        print(
            "rate-limit scale during execute (1.0 = policy speed fully allowed): "
            f"L median {statistics.median(sl):.2f} min {min(sl):.2f} | "
            f"R median {statistics.median(sr):.2f} min {min(sr):.2f}"
        )
    faults = [x["fault"] for x in rows if x["fault"]]
    if faults:
        print("faults:", sorted(set(faults)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
