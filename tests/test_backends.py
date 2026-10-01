import json_numpy
import numpy as np
import pytest

from dryrun.backends import FastApiPolicy, Observation


def _obs() -> Observation:
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    return Observation(top=img, left=img, right=img, state=np.zeros(14, np.float32), timestamp=1.0)


def test_payload_keys_match_official_server() -> None:
    policy = FastApiPolicy("http://127.0.0.1:8202/", num_steps=10)
    payload = policy.build_payload(_obs(), "pick up the object")
    assert policy.url == "http://127.0.0.1:8202/act"
    assert set(payload) == {
        "top_cam",
        "left_cam",
        "right_cam",
        "instruction",
        "state",
        "timestamp",
        "normalization_tag",
        "num_steps",
    }
    state = payload["state"]
    assert isinstance(state, np.ndarray)
    assert state.dtype == np.float32 and state.shape == (14,)
    top = payload["top_cam"]
    assert isinstance(top, np.ndarray) and top.dtype == np.uint8
    # Must survive the json_numpy round trip the server performs.
    decoded = json_numpy.loads(json_numpy.dumps(payload))
    assert decoded["state"].shape == (14,)
    assert decoded["left_cam"].shape == (360, 640, 3)


def test_act_parses_json_numpy_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResp:
        content = json_numpy.dumps(
            {"actions": np.ones((30, 14), dtype=np.float32), "dt_ms": 123.4}
        ).encode()

        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, data: str, headers: dict[str, str], timeout: float) -> FakeResp:
        captured["url"] = url
        captured["body"] = json_numpy.loads(data)
        return FakeResp()

    monkeypatch.setattr("dryrun.backends.requests.post", fake_post)
    result = FastApiPolicy("http://127.0.0.1:8202").act(_obs(), "task")
    assert captured["url"] == "http://127.0.0.1:8202/act"
    assert captured["body"]["instruction"] == "task"  # type: ignore[index]
    assert result.actions.shape == (30, 14)
    assert result.server_dt_ms == pytest.approx(123.4)
    assert result.round_trip_ms >= 0


def test_missing_actions_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("dryrun.backends.requests.post", lambda *a, **k: FakeResp())
    with pytest.raises(ValueError, match="actions"):
        FastApiPolicy("http://x").act(_obs(), "t")
