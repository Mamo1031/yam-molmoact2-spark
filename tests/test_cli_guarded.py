import socket
from pathlib import Path

import pytest

from scripts.run_policy_guarded import main


def test_cli_hold_fake(tmp_path: Path) -> None:
    out = tmp_path / "hold"
    rc = main(
        [
            "--mode",
            "hold",
            "--config",
            "configs/dry_run.yaml",
            "--fake",
            "--yes",
            "--seconds",
            "0.3",
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0 and (out / "summary.json").exists()


def test_cli_direction_fake(tmp_path: Path) -> None:
    out = tmp_path / "dir"
    rc = main(
        [
            "--mode",
            "direction-test",
            "--arms",
            "right",
            "--config",
            "configs/dry_run.yaml",
            "--fake",
            "--yes",
            "--delta",
            "0.02",
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0


def test_cli_return_to_start_fake(tmp_path: Path) -> None:
    out = tmp_path / "ret"
    rc = main(
        [
            "--mode",
            "return-to-start",
            "--config",
            "configs/dry_run.yaml",
            "--fake",
            "--yes",
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0 and (out / "summary.json").exists()


def test_cli_temps_without_socketcan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(socket, "AF_CAN", raising=False)
    assert main(["--mode", "temps", "--config", "configs/dry_run.yaml", "--seconds", "0"]) == 1
