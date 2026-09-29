"""Policy backends. Each returns an action chunk for one observation and never touches motors."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import json_numpy
import numpy as np
import requests


@dataclass
class Observation:
    """One synchronized observation: three RGB images (H, W, 3) uint8 and a (14,) float32 state."""

    top: np.ndarray
    left: np.ndarray
    right: np.ndarray
    state: np.ndarray
    timestamp: float


@dataclass
class PolicyResult:
    actions: np.ndarray
    server_dt_ms: float | None
    round_trip_ms: float


class Policy(Protocol):
    def act(self, obs: Observation, instruction: str) -> PolicyResult: ...


class FastApiPolicy:
    """Client for the official MolmoAct2 YAM server (``host_server_yam.py``, port 8202).

    Request keys follow that server: ``top_cam``, ``left_cam``, ``right_cam``, ``instruction``,
    ``state`` and the optional ``num_steps`` / ``timestamp``. ``normalization_tag`` mirrors the
    official ``molmoact_client.py``. Arrays travel as ``json_numpy`` payloads.
    """

    def __init__(
        self,
        server_url: str,
        *,
        normalization_tag: str | None = "yam_dual_molmoact2",
        num_steps: int | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.url = server_url.rstrip("/") + "/act"
        self.normalization_tag = normalization_tag
        self.num_steps = num_steps
        self.timeout_s = timeout_s

    def build_payload(self, obs: Observation, instruction: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "top_cam": np.ascontiguousarray(obs.top, dtype=np.uint8),
            "left_cam": np.ascontiguousarray(obs.left, dtype=np.uint8),
            "right_cam": np.ascontiguousarray(obs.right, dtype=np.uint8),
            "instruction": instruction,
            "state": np.ascontiguousarray(obs.state, dtype=np.float32).reshape(14),
            "timestamp": float(obs.timestamp),
        }
        if self.normalization_tag is not None:
            payload["normalization_tag"] = self.normalization_tag
        if self.num_steps is not None:
            payload["num_steps"] = int(self.num_steps)
        return payload

    def act(self, obs: Observation, instruction: str) -> PolicyResult:
        body = json_numpy.dumps(self.build_payload(obs, instruction))
        t0 = time.perf_counter()
        resp = requests.post(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout_s,
        )
        round_trip_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        data = json_numpy.loads(resp.content.decode("utf-8"))
        if "actions" not in data:
            raise ValueError(f"server response has no 'actions' key: {list(data)}")
        actions = np.asarray(data["actions"], dtype=np.float32)
        dt_ms = data.get("dt_ms")
        return PolicyResult(
            actions=actions,
            server_dt_ms=float(dt_ms) if dt_ms is not None else None,
            round_trip_ms=round_trip_ms,
        )
