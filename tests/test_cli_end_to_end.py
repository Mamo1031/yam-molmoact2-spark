"""Run the dry-run CLI against a local stand-in for the FastAPI /act endpoint."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import json_numpy
import numpy as np
import pytest

from scripts.dry_run_no_actuation import main


class _ActHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        payload = json_numpy.loads(self.rfile.read(length).decode())
        _ActHandler.received.append({k: getattr(v, "shape", v) for k, v in payload.items()})
        state = np.asarray(payload["state"], dtype=np.float32)
        actions = np.repeat(state[None, :], 30, axis=0) + 0.001
        body = json_numpy.dumps({"actions": actions, "dt_ms": 42.0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def act_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _ActHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_cli_fake_sensors(tmp_path: Path, act_server: str) -> None:
    example = Path(__file__).resolve().parents[1] / "configs" / "dry_run.example.yaml"
    text = example.read_text()
    for key in ("D435_TOP", "D405_LEFT", "D405_RIGHT"):
        text = text.replace(f"REPLACE_WITH_{key}_SERIAL", "0")
    text = text.replace('server_url: "http://127.0.0.1:8202"', f'server_url: "{act_server}"')
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(text)
    out = tmp_path / "out"
    rc = main(
        [
            "--config",
            str(cfg),
            "--queries",
            "3",
            "--period",
            "0",
            "--fake-sensors",
            "--out-dir",
            str(out),
            "--no-images",
        ]
    )
    assert rc == 0
    assert len(_ActHandler.received) == 3
    first = _ActHandler.received[0]
    assert first["state"] == (14,) and first["top_cam"] == (360, 640, 3)
    assert first["instruction"] == "Pick up the object and place it on the side."
    summary = json.loads((out / "summary.json").read_text())
    assert summary["queries"] == 3 and summary["all_ok"] is True
    assert (out / "actions.png").exists() and (out / "summary.csv").exists()
