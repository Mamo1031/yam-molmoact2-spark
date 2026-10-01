#!/usr/bin/env python
"""Run LeRobot's async ``robot_client`` with actuation disabled (Gate G4, LeRobot backend).

Every argument is forwarded unchanged to ``lerobot.async_inference.robot_client``; use the
exact command from reference.md section 9.3 but replace the module with this script:

    python scripts/dry_run_lerobot_client.py --server_address 127.0.0.1:8000 ... \
        --dry-run-out logs/raw/lerobot_dry_run

``BiYamFollower.send_action`` is replaced before the client starts, so the policy server,
observation capture and action queue all run for real while ``command_joint_pos`` is never
called. Each would-be action is validated against the current arm state and logged.
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dryrun import STATE_NAMES  # noqa: E402
from dryrun.checks import validate_action_chunk  # noqa: E402
from dryrun.sensors import arm_obs_to_state7, combine_state  # noqa: E402


def _pop_flag(argv: list[str], name: str, default: str) -> str:
    if name in argv:
        i = argv.index(name)
        value = argv[i + 1]
        del argv[i : i + 2]
        return value
    return default


def install_no_actuation(out_dir: Path) -> None:
    from lerobot.robots.bi_yam_follower.bi_yam_follower import BiYamFollower

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ticks.csv"
    fieldnames = ["tick", "timestamp", "max_first_step_delta", "argmax", "ok", "issues"]
    fieldnames += [f"a_{n}" for n in STATE_NAMES] + [f"s_{n}" for n in STATE_NAMES]
    f = open(csv_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    tick = {"n": 0}

    def action_dict_to_vec(action: dict[str, Any]) -> np.ndarray:
        return np.array(
            [float(action.get(f"{n}.pos", np.nan)) for n in STATE_NAMES], dtype=np.float32
        )

    def send_action_logged(self: Any, action: dict[str, Any]) -> dict[str, Any]:
        left7 = arm_obs_to_state7(self.left_arm.get_observations())
        right7 = arm_obs_to_state7(self.right_arm.get_observations())
        state = combine_state(left7, right7)
        vec = action_dict_to_vec(action)
        report = validate_action_chunk(vec[None, :], state)
        row: dict[str, Any] = {
            "tick": tick["n"],
            "timestamp": time.time(),
            "max_first_step_delta": float(np.max(report.first_step_delta)),
            "argmax": STATE_NAMES[int(np.argmax(report.first_step_delta))],
            "ok": report.ok,
            "issues": "; ".join(report.issues),
        }
        row.update({f"a_{n}": float(vec[i]) for i, n in enumerate(STATE_NAMES)})
        row.update({f"s_{n}": float(state[i]) for i, n in enumerate(STATE_NAMES)})
        writer.writerow(row)
        f.flush()
        tick["n"] += 1
        return action

    BiYamFollower.send_action = send_action_logged  # type: ignore[method-assign]
    print(f"[dry-run] BiYamFollower.send_action replaced. NO ACTUATION. log -> {csv_path}")


def main() -> None:
    argv = sys.argv[1:]
    out_dir = Path(
        _pop_flag(
            argv, "--dry-run-out", f"logs/raw/lerobot_dry_run_{time.strftime('%Y%m%d-%H%M%S')}"
        )
    )
    install_no_actuation(out_dir)
    sys.argv = [sys.argv[0], *argv]
    from lerobot.async_inference.robot_client import async_client

    async_client()


if __name__ == "__main__":
    main()
