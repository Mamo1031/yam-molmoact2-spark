#!/usr/bin/env python
"""Gate G2: stream several RealSense cameras at once and report fps, read failures and gaps.

Run inside the ``lerobot-client`` venv on the Spark:

    python scripts/camera_stream_test.py --seconds 60 top=138422071505 right=260522272252
"""

from __future__ import annotations

import argparse
import threading
import time


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cameras", nargs="+", help="role=serial pairs")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--warmup", type=float, default=2.0)
    args = p.parse_args()

    from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig

    cams = {}
    for pair in args.cameras:
        role, serial = pair.split("=", 1)
        cfg = RealSenseCameraConfig(
            serial_number_or_name=serial,
            width=args.width,
            height=args.height,
            fps=args.fps,
            warmup_s=args.warmup,
        )
        cam = RealSenseCamera(cfg)
        cam.connect()
        cams[role] = cam
        print(f"connected {role} ({serial})")

    stats = {r: {"ok": 0, "fail": 0, "gaps": 0, "max_gap_ms": 0.0} for r in cams}
    stop_at = time.perf_counter() + args.seconds

    def loop(role: str) -> None:
        cam = cams[role]
        last = time.perf_counter()
        while time.perf_counter() < stop_at:
            try:
                cam.read()
                stats[role]["ok"] += 1
                now = time.perf_counter()
                gap_ms = (now - last) * 1000.0
                stats[role]["max_gap_ms"] = max(stats[role]["max_gap_ms"], gap_ms)
                if gap_ms > 200.0:
                    stats[role]["gaps"] += 1
                last = now
            except Exception:
                stats[role]["fail"] += 1
                time.sleep(0.05)

    threads = [threading.Thread(target=loop, args=(r,), daemon=True) for r in cams]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    worst = 0.0
    for role, s in stats.items():
        fps = s["ok"] / elapsed if elapsed else 0.0
        print(
            f"{role}: {s['ok']} frames in {elapsed:.1f}s = {fps:.1f} fps, "
            f"read failures {s['fail']}, gaps>200ms {s['gaps']}, max gap {s['max_gap_ms']:.0f} ms"
        )
        worst = max(worst, s["fail"] + s["gaps"])
    for cam in cams.values():
        cam.disconnect()
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
