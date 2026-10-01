import csv
import json
from pathlib import Path

import numpy as np
import pytest

from dryrun import STATE_DIM
from dryrun.backends import PolicyResult
from dryrun.checks import validate_action_chunk
from dryrun.config import CameraSpec, DryRunConfig
from dryrun.recorder import Recorder
from dryrun.sensors import FakeSource, arm_obs_to_state7, combine_state


def test_state_order_is_left_then_right() -> None:
    left = arm_obs_to_state7({"joint_pos": np.arange(6), "gripper_pos": [0.5]})
    right = arm_obs_to_state7({"joint_pos": np.arange(10, 16), "gripper_pos": [0.9]})
    state = combine_state(left, right)
    assert state.shape == (STATE_DIM,)
    assert state[6] == 0.5 and state[13] == pytest.approx(0.9)
    assert list(state[:6]) == list(range(6)) and list(state[7:13]) == list(range(10, 16))


def test_recorder_writes_csv_jsonl_plot(tmp_path: Path) -> None:
    cams = {r: CameraSpec(serial=r, width=64, height=32) for r in ("top", "left", "right")}
    src = FakeSource(cams)
    rec = Recorder(tmp_path, save_images=True)
    for step in range(3):
        obs = src.read()
        actions = np.repeat(obs.state[None, :], 30, axis=0)
        result = PolicyResult(actions=actions, server_dt_ms=50.0, round_trip_ms=60.0 + step)
        rec.record(step, obs, result, validate_action_chunk(actions, obs.state))
    rows = list(csv.DictReader(open(tmp_path / "summary.csv", encoding="utf-8")))
    assert len(rows) == 3 and rows[0]["shape"] == "30x14" and rows[0]["ok"] == "True"
    lines = (tmp_path / "chunks.jsonl").read_text().splitlines()
    assert len(lines) == 3 and len(json.loads(lines[0])["actions"]) == 30
    assert (tmp_path / "images" / "step0000_top.png").exists()
    assert rec.plot() == tmp_path / "actions.png"
    summary = json.loads(rec.write_summary_json().read_text())
    assert summary["queries"] == 3 and summary["all_ok"] is True
    assert summary["latency"]["p95_ms"] >= summary["latency"]["median_ms"]


def test_config_rejects_placeholder_serials(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[1] / "configs" / "dry_run.example.yaml"
    with pytest.raises(ValueError, match="placeholder"):
        DryRunConfig.load(example)
    filled = example.read_text().replace("REPLACE_WITH_D435_TOP_SERIAL", "111")
    filled = filled.replace("REPLACE_WITH_D405_LEFT_SERIAL", "222")
    filled = filled.replace("REPLACE_WITH_D405_RIGHT_SERIAL", "333")
    p = tmp_path / "c.yaml"
    p.write_text(filled)
    cfg = DryRunConfig.load(p)
    assert cfg.cameras["left"].serial == "222" and cfg.right_arm_port == 1234
    assert cfg.server_url == "http://127.0.0.1:8202"


def test_lerobot_source_without_cameras_never_touches_them(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    class Arm:
        def __init__(self, port: int, host: str) -> None:
            self.port = port

        def connect(self) -> None:
            return None

        def get_observations(self) -> dict[str, list[float]]:
            return {"joint_pos": [0.1 * self.port] * 6, "gripper_pos": [1.0]}

    arm_module = types.ModuleType("lerobot.robots.bi_yam_follower.bi_yam_follower")
    arm_module.YamArmClient = Arm  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lerobot.robots.bi_yam_follower.bi_yam_follower", arm_module)
    # Any camera import would fail here: there is no lerobot.cameras module in the test env.
    monkeypatch.setitem(sys.modules, "lerobot.cameras.realsense", None)

    from dryrun.sensors import LeRobotSource

    src = LeRobotSource({}, left_port=1, right_port=2, with_cameras=False)
    src.connect()
    state = src.read_state_only()
    assert (
        state.shape == (14,) and state[0] == pytest.approx(0.1) and state[7] == pytest.approx(0.2)
    )
    with pytest.raises(RuntimeError, match="without cameras"):
        src.read()
