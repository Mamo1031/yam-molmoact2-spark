"""Persist observations, action chunks, validation results and plots for the G4 report."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from dryrun import STATE_DIM, STATE_NAMES
from dryrun.backends import Observation, PolicyResult
from dryrun.checks import ActionReport


class Recorder:
    """Writes ``summary.csv`` (one row per query), ``chunks.jsonl`` and PNG images."""

    def __init__(self, out_dir: str | Path, *, save_images: bool = True) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.save_images = save_images
        self._csv_path = self.out_dir / "summary.csv"
        self._jsonl_path = self.out_dir / "chunks.jsonl"
        self._rows: list[dict[str, object]] = []
        self._round_trips: list[float] = []
        self._timestamps: list[float] = []
        self._states: list[np.ndarray] = []
        self._chunks: list[np.ndarray] = []

    def record(
        self, step: int, obs: Observation, result: PolicyResult, report: ActionReport
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "step": step,
            "timestamp": obs.timestamp,
            "round_trip_ms": round(result.round_trip_ms, 2),
            "server_dt_ms": result.server_dt_ms,
            **report.to_row(),
        }
        self._rows.append(row)
        self._round_trips.append(float(result.round_trip_ms))
        self._timestamps.append(float(obs.timestamp))
        self._states.append(np.asarray(obs.state, dtype=np.float32))
        self._chunks.append(np.asarray(result.actions, dtype=np.float32))
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "step": step,
                        "timestamp": obs.timestamp,
                        "state": obs.state.tolist(),
                        "actions": result.actions.tolist(),
                        "report": _report_json(report),
                    }
                )
                + "\n"
            )
        if self.save_images:
            self._save_images(step, obs)
        self._write_csv()
        return row

    def _write_csv(self) -> None:
        if not self._rows:
            return
        with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(self._rows[0].keys()))
            writer.writeheader()
            writer.writerows(self._rows)

    def _save_images(self, step: int, obs: Observation) -> None:
        from PIL import Image

        img_dir = self.out_dir / "images"
        img_dir.mkdir(exist_ok=True)
        for role, img in (("top", obs.top), ("left", obs.left), ("right", obs.right)):
            Image.fromarray(np.asarray(img, dtype=np.uint8)).save(
                img_dir / f"step{step:04d}_{role}.png"
            )

    def latency_summary(self) -> dict[str, float]:
        rt = np.array(self._round_trips, dtype=np.float64)
        if rt.size == 0:
            return {}
        summary = {
            "count": float(rt.size),
            "mean_ms": float(rt.mean()),
            "median_ms": float(np.median(rt)),
            "p95_ms": float(np.percentile(rt, 95)),
            "max_ms": float(rt.max()),
        }
        if rt.size > 1:
            summary["mean_ms_excluding_first"] = float(rt[1:].mean())
        return summary

    def plot(self, *, chunk_dt_s: float = 1.0 / 30.0) -> Path | None:
        """Plot every action chunk per dimension with the observed state at each query."""
        if not self._chunks:
            return None
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(7, 2, figsize=(14, 16), sharex=True)
        for dim in range(STATE_DIM):
            ax = axes[dim % 7][dim // 7]
            for k, (chunk, state) in enumerate(zip(self._chunks, self._states, strict=True)):
                if chunk.ndim != 2 or chunk.shape[1] <= dim:
                    continue
                t0 = self._timestamps[k] - self._timestamps[0]
                t = t0 + np.arange(len(chunk)) * chunk_dt_s
                ax.plot(t, chunk[:, dim], lw=0.8)
                ax.plot(t[0], state[dim], "k.", ms=4)
            ax.set_title(STATE_NAMES[dim], fontsize=9)
            ax.grid(alpha=0.3)
        for ax in axes[-1]:
            ax.set_xlabel("time since first query [s]")
        fig.suptitle("Action chunks (lines) and observed state at query time (dots). NO ACTUATION.")
        fig.tight_layout()
        path = self.out_dir / "actions.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        return path

    def write_summary_json(self, extra: dict[str, object] | None = None) -> Path:
        payload: dict[str, object] = {
            "queries": len(self._rows),
            "latency": self.latency_summary(),
            "all_ok": all(bool(r["ok"]) for r in self._rows),
            "issues": [r["issues"] for r in self._rows if r["issues"]],
        }
        if extra:
            payload.update(extra)
        path = self.out_dir / "summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return path


def _report_json(report: ActionReport) -> dict[str, object]:
    d = asdict(report)
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in d.items()}
