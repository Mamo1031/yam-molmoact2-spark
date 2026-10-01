#!/usr/bin/env python
"""Record short MP4 clips from several RealSense cameras at once (for sharing / Gate G2 evidence).

Run inside the ``lerobot-client`` venv on the Spark:

    python scripts/camera_record.py --seconds 20 --out ~/molmoact2-setup/logs/videos \
        top=138422071505 right=260522272252

Writes ``<out>/<timestamp>_<role>.mp4`` (and a first-frame PNG) per camera.
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cameras", nargs="+", help="role=serial pairs")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--out", default="~/molmoact2-setup/logs/videos")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--fps", type=int, default=30)
    args = p.parse_args()

    import cv2
    import numpy as np
    from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    cams = {}
    for pair in args.cameras:
        role, serial = pair.split("=", 1)
        cfg = RealSenseCameraConfig(
            serial_number_or_name=serial,
            width=args.width,
            height=args.height,
            fps=args.fps,
            warmup_s=2,
        )
        cam = RealSenseCamera(cfg)
        cam.connect()
        cams[role] = cam
        print(f"connected {role} ({serial})")

    results: dict[str, str] = {}
    stop_at = time.perf_counter() + args.seconds

    def record(role: str) -> None:
        cam = cams[role]
        path = out_dir / f"{stamp}_{role}.mp4"
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (args.width, args.height)
        )
        n = 0
        while time.perf_counter() < stop_at:
            try:
                rgb = np.asarray(cam.read())
            except Exception:
                continue
            if n == 0:
                cv2.imwrite(
                    str(out_dir / f"{stamp}_{role}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                )
            writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            n += 1
        writer.release()
        results[role] = f"{path} ({n} frames, {path.stat().st_size / 1e6:.1f} MB)"

    threads = [threading.Thread(target=record, args=(r,), daemon=True) for r in cams]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for cam in cams.values():
        cam.disconnect()
    for role, info in results.items():
        print(f"{role}: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
