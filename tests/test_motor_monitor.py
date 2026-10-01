"""Motor feedback monitor: frame decoding and health checks with injected frames (no CAN bus)."""

from __future__ import annotations

import socket
import struct

import pytest

from dryrun.motor_monitor import (
    MOTORS_PER_ARM,
    MotorLimits,
    MotorMonitor,
    motor_label,
    parse_feedback,
)

BUSES = {"left": "can_follower_l", "right": "can_follower_r"}


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def frame(motor_id: int, state: int = 1, mos: int = 30, rotor: int = 30) -> tuple[int, bytes]:
    return 0x10 + motor_id, bytes([(state << 4) | motor_id, 0x80, 0, 0x7F, 0xF0, 0, mos, rotor])


def feed_arm(mon: MotorMonitor, arm: str, **overrides: int) -> None:
    for motor_id in range(1, MOTORS_PER_ARM + 1):
        mon.feed(arm, *frame(motor_id, **overrides))


def make_monitor() -> tuple[MotorMonitor, Clock]:
    clock = Clock()
    mon = MotorMonitor(BUSES, clock=clock)
    mon.watch_injected()
    for arm in BUSES:
        feed_arm(mon, arm)
    return mon, clock


def test_parse_real_feedback_frame() -> None:
    # Captured on can_follower_l 2026-09-30: left shoulder, enabled, MOS 39 C, rotor 51 C.
    fb = parse_feedback(0x012, bytes.fromhex("12914E7FD5B02733"), 1.0)
    assert fb is not None
    assert (fb.motor_id, fb.state, fb.temp_mos, fb.temp_rotor) == (2, 1, 39.0, 51.0)
    assert fb.state_message == "enabled"


def test_parse_ignores_other_frames() -> None:
    assert parse_feedback(0x002, bytes(8), 0.0) is None  # command frame to motor 2
    assert parse_feedback(0x018, bytes(8), 0.0) is None
    assert parse_feedback(0x012, bytes(4), 0.0) is None
    assert parse_feedback(0x80000012, bytes(8), 0.0) is None  # extended-frame flag
    assert parse_feedback(0x012, bytes.fromhex("13914E7FD5B02733"), 0.0) is None  # id mismatch


def test_motor_label_maps_to_state_names() -> None:
    assert motor_label("right", 2) == "right motor 2 (right_joint_1)"
    assert motor_label("left", 7) == "left motor 7 (left_gripper)"


def test_healthy_buses_have_no_fault() -> None:
    mon, _ = make_monitor()
    health = mon.check(MotorLimits())
    assert health.fault is None and health.too_hot is None and health.warnings == []


def test_silent_bus_is_a_fault() -> None:
    mon, clock = make_monitor()
    clock.t += 1.0
    feed_arm(mon, "left")
    health = mon.check(MotorLimits(feedback_timeout_s=0.5))
    assert health.fault is not None
    assert "can_follower_r" in health.fault and "can_follower_l" not in health.fault


def test_bus_that_never_spoke_is_a_fault() -> None:
    mon = MotorMonitor(BUSES, clock=Clock())
    mon.watch_injected()
    feed_arm(mon, "left")
    health = mon.check(MotorLimits())
    assert health.fault is not None and "no motor feedback on can_follower_r" in health.fault


def test_single_missing_motor_is_a_fault() -> None:
    mon, clock = make_monitor()
    clock.t += 1.0
    for arm in BUSES:
        for motor_id in range(1, MOTORS_PER_ARM + 1):
            if (arm, motor_id) != ("left", 3):
                mon.feed(arm, *frame(motor_id))
    assert mon.check(MotorLimits()).fault == "no feedback from left motor 3 (left_joint_2)"


def test_error_state_names_the_motor() -> None:
    mon, _ = make_monitor()
    mon.feed("right", *frame(2, state=0xC, rotor=120))
    fault = mon.check(MotorLimits()).fault
    assert fault is not None
    assert "right motor 2 (right_joint_1)" in fault and "rotor over temperature" in fault


def test_temperature_warning_and_stop() -> None:
    mon, _ = make_monitor()
    limits = MotorLimits(temp_warn_c=60, temp_stop_c=70)
    mon.feed("left", *frame(2, rotor=62))
    health = mon.check(limits)
    assert health.fault is None and health.too_hot is None
    assert len(health.warnings) == 1 and "left motor 2" in health.warnings[0]
    mon.feed("left", *frame(2, rotor=71))
    mon.feed("right", *frame(3, mos=80))
    health = mon.check(limits)
    assert health.fault is None  # heat alone is not a fault: the caller decides
    assert health.too_hot is not None and "right motor 3" in health.too_hot
    assert len(health.warnings) == 2


def test_max_temperatures_are_kept() -> None:
    mon, _ = make_monitor()
    mon.feed("right", *frame(2, rotor=66, mos=41))
    mon.feed("right", *frame(2, rotor=50, mos=35))
    temps = mon.max_temperatures()["right"]
    assert temps["rotor"][1] == 66.0 and temps["mos"][1] == 41.0
    assert "m2: 50/35" in mon.format_table()


def test_limits_from_mapping() -> None:
    limits = MotorLimits.from_mapping(
        {"motor_temp_warn_c": 55, "motor_temp_stop_c": 60, "max_step_joint": 0.01}
    )
    assert (limits.temp_warn_c, limits.temp_stop_c) == (55.0, 60.0)
    with pytest.raises(ValueError):
        MotorLimits(temp_warn_c=80, temp_stop_c=70)


def test_start_without_socketcan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(socket, "AF_CAN", raising=False)
    mon = MotorMonitor(BUSES)
    mon.start()
    assert mon.watched == () and set(mon.unavailable) == {"left", "right"}
    assert mon.check(MotorLimits()).fault is None
    mon.close()


class FakeSock:
    """Replays a scripted sequence of recv() results, then fails like an unplugged adapter."""

    def __init__(self, items: list[bytes | Exception]) -> None:
        self.items = list(items)

    def recv(self, size: int) -> bytes:
        if not self.items:
            raise OSError(19, "No such device")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_reader_decodes_frames_and_reports_why_it_stopped() -> None:
    pack = struct.Struct("=IB3x8s").pack
    sock = FakeSock(
        [
            TimeoutError(),
            pack(0x002, 8, bytes(8)),
            b"short",
            pack(0x012, 8, bytes.fromhex("12914E7FD5B02733")),
            pack(0x013, 4, bytes(8)),
        ]
    )
    clock = Clock()
    mon = MotorMonitor(BUSES, clock=clock)
    mon.watch_injected()
    mon._reader("left", sock)  # type: ignore[arg-type]
    assert list(mon.snapshot()["left"]) == [2]
    assert mon.snapshot()["left"][2].temp_rotor == 51.0
    clock.t += 1.0
    fault = mon.check(MotorLimits()).fault
    assert fault is not None and "the CAN monitor stopped" in fault and "No such device" in fault
