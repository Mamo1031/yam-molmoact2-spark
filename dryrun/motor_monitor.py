"""Listen-only motor feedback monitor for the YAM follower CAN buses.

The follower servers own the buses. Their RPC interface keeps answering with the last known
state after the motor control thread has died, so a dead arm looks like a frozen but valid
pose to the executor (seen 2026-09-30: a shoulder motor tripped on over-temperature). The
feedback frames the motors send are the ground truth: this module opens one extra raw
SocketCAN socket per bus, receives those frames and never transmits.

Feedback frame of a DM motor (i2rt ``dm_driver.parse_recv_message``): arbitration id
``0x10 + motor id``; the high nibble of ``data[0]`` is the state code (1 = enabled),
``data[6]`` the MOS temperature and ``data[7]`` the rotor temperature in degrees Celsius.
A motor only answers a command, so a bus without feedback means nobody is driving it, and
the temperatures of an arm whose server is stopped cannot be read this way.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

FEEDBACK_ID_BASE = 0x10
MOTORS_PER_ARM = 7
STATE_ENABLED = 1
STATE_MESSAGES = {
    0x0: "disabled",
    0x1: "enabled",
    0x8: "over voltage",
    0x9: "under voltage",
    0xA: "over current",
    0xB: "MOS over temperature",
    0xC: "rotor over temperature",
    0xD: "communication lost",
    0xE: "overload",
}

_CAN_FRAME = struct.Struct("=IB3x8s")
_CAN_FLAGS = 0xE0000000  # extended / remote / error frames are never motor feedback
# Standard data frames 0x10..0x17 only: the commands the server sends (0x01..0x07) are looped
# back to every local socket and would double the load.
_FEEDBACK_FILTER = struct.pack("=II", FEEDBACK_ID_BASE, 0x7F8 | 0xC0000000)


@dataclass(frozen=True)
class MotorFeedback:
    motor_id: int
    state: int
    temp_mos: float
    temp_rotor: float
    t: float

    @property
    def state_message(self) -> str:
        return STATE_MESSAGES.get(self.state, f"unknown state 0x{self.state:X}")


def parse_feedback(can_id: int, data: bytes, t: float) -> MotorFeedback | None:
    """Decode one CAN frame; returns None for anything that is not a motor feedback frame."""
    if can_id & _CAN_FLAGS or len(data) < 8:
        return None
    motor_id = can_id - FEEDBACK_ID_BASE
    if not 1 <= motor_id <= MOTORS_PER_ARM or data[0] & 0x0F != motor_id:
        return None
    return MotorFeedback(motor_id, data[0] >> 4, float(data[6]), float(data[7]), t)


def motor_label(arm: str, motor_id: int) -> str:
    """Motor ids are 1-based; state names count joints from 0 and end with the gripper."""
    name = "gripper" if motor_id == MOTORS_PER_ARM else f"joint_{motor_id - 1}"
    return f"{arm} motor {motor_id} ({arm}_{name})"


@dataclass
class MotorLimits:
    """Thresholds for the motor feedback checks (temperatures in degrees Celsius).

    The motor's own coil protection (OT_Value, not read from these motors; the vendor advises
    at most 100 C) trips the whole arm, so the stop limit has to sit well below it.
    """

    feedback_timeout_s: float = 0.5
    temp_warn_c: float = 60.0
    temp_stop_c: float = 70.0

    def __post_init__(self) -> None:
        if self.temp_warn_c > self.temp_stop_c:
            raise ValueError("motor_temp_warn_c must not exceed motor_temp_stop_c")
        if self.feedback_timeout_s <= 0:
            raise ValueError("motor_feedback_timeout_s must be positive")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> MotorLimits:
        raw = raw or {}
        kwargs: dict[str, float] = {}
        for key, name in (
            ("motor_feedback_timeout_s", "feedback_timeout_s"),
            ("motor_temp_warn_c", "temp_warn_c"),
            ("motor_temp_stop_c", "temp_stop_c"),
        ):
            if key in raw:
                kwargs[name] = float(raw[key])  # type: ignore[arg-type]
        return cls(**kwargs)


@dataclass
class MotorHealth:
    """Result of one check: ``fault`` always stops motion; ``too_hot`` is the caller's call."""

    fault: str | None = None
    too_hot: str | None = None
    warnings: list[str] = field(default_factory=list)


class MotorMonitor:
    """Keeps the latest feedback frame of every motor on the watched buses."""

    def __init__(
        self, interfaces: Mapping[str, str], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.interfaces = dict(interfaces)
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: dict[str, dict[int, MotorFeedback]] = {arm: {} for arm in self.interfaces}
        self._max_rotor: dict[str, dict[int, float]] = {arm: {} for arm in self.interfaces}
        self._max_mos: dict[str, dict[int, float]] = {arm: {} for arm in self.interfaces}
        self._socks: dict[str, socket.socket] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.watched: tuple[str, ...] = ()
        self.unavailable: dict[str, str] = {}
        self._reader_errors: dict[str, str] = {}

    def start(self) -> None:
        """Open the buses that exist; the others are listed in ``unavailable`` with the reason."""
        family = getattr(socket, "AF_CAN", None)
        if family is None:
            self.unavailable = {arm: "no SocketCAN on this platform" for arm in self.interfaces}
            return
        for arm, ifname in self.interfaces.items():
            try:
                sock = socket.socket(family, socket.SOCK_RAW, socket.CAN_RAW)  # type: ignore[attr-defined]
                sock.setsockopt(
                    socket.SOL_CAN_RAW,  # type: ignore[attr-defined]
                    socket.CAN_RAW_FILTER,  # type: ignore[attr-defined]
                    _FEEDBACK_FILTER,
                )
                sock.bind((ifname,))
                sock.settimeout(0.2)
            except OSError as exc:
                self.unavailable[arm] = f"{ifname}: {exc}"
                continue
            self._socks[arm] = sock
            thread = threading.Thread(target=self._reader, args=(arm, sock), daemon=True)
            thread.start()
            self._threads.append(thread)
        self.watched = tuple(self._socks)

    def watch_injected(self) -> None:
        """Treat every configured bus as watched without opening sockets (frames via feed())."""
        self.watched = tuple(self.interfaces)

    def _reader(self, arm: str, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                frame = sock.recv(_CAN_FRAME.size)
            except TimeoutError:
                continue
            except OSError as exc:
                # Adapter unplugged or link taken down: the bus now reads as silent.
                self._reader_errors[arm] = str(exc)
                return
            if len(frame) != _CAN_FRAME.size:
                continue
            can_id, dlc, data = _CAN_FRAME.unpack(frame)
            self.feed(arm, can_id, data[:dlc])

    def feed(self, arm: str, can_id: int, data: bytes) -> None:
        fb = parse_feedback(can_id, data, self._clock())
        if fb is None:
            return
        with self._lock:
            self._latest[arm][fb.motor_id] = fb
            if fb.temp_rotor > self._max_rotor[arm].get(fb.motor_id, -1.0):
                self._max_rotor[arm][fb.motor_id] = fb.temp_rotor
            if fb.temp_mos > self._max_mos[arm].get(fb.motor_id, -1.0):
                self._max_mos[arm][fb.motor_id] = fb.temp_mos

    def snapshot(self) -> dict[str, dict[int, MotorFeedback]]:
        with self._lock:
            return {arm: dict(motors) for arm, motors in self._latest.items()}

    def wait_for_feedback(self, timeout_s: float) -> None:
        """Block until every watched bus has reported all its motors, or the timeout passes."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self.snapshot()
            if all(len(snap[arm]) >= MOTORS_PER_ARM for arm in self.watched):
                return
            time.sleep(0.01)

    def check(self, limits: MotorLimits) -> MotorHealth:
        """Judge the watched buses: error states and silence are faults, heat is reported."""
        now = self._clock()
        snap = self.snapshot()
        health = MotorHealth()
        hottest: tuple[float, str] | None = None
        for arm in self.watched:
            motors = snap[arm]
            for fb in motors.values():
                if fb.state != STATE_ENABLED and health.fault is None:
                    health.fault = (
                        f"{motor_label(arm, fb.motor_id)} reports '{fb.state_message}' at "
                        f"{fb.temp_rotor:.0f} C rotor / {fb.temp_mos:.0f} C MOS"
                    )
            newest = max((fb.t for fb in motors.values()), default=None)
            if newest is None or now - newest > limits.feedback_timeout_s:
                waited = limits.feedback_timeout_s if newest is None else now - newest
                silent = (
                    f"no motor feedback on {self.interfaces[arm]} ({arm} arm) for {waited:.1f} s: "
                )
                if arm in self._reader_errors:
                    silent += f"the CAN monitor stopped ({self._reader_errors[arm]})"
                else:
                    silent += (
                        "the follower server is not driving the motors "
                        "(see bi_yam_servers.log, then restart the follower servers)"
                    )
                health.fault = silent if health.fault is None else f"{health.fault}; {silent}"
                continue
            for motor_id in range(1, MOTORS_PER_ARM + 1):
                fb_or_none = motors.get(motor_id)
                if fb_or_none is None or now - fb_or_none.t > limits.feedback_timeout_s:
                    if health.fault is None:
                        health.fault = f"no feedback from {motor_label(arm, motor_id)}"
                    continue
                temp = max(fb_or_none.temp_rotor, fb_or_none.temp_mos)
                if temp >= limits.temp_warn_c:
                    text = (
                        f"{motor_label(arm, motor_id)} at {fb_or_none.temp_rotor:.0f} C rotor / "
                        f"{fb_or_none.temp_mos:.0f} C MOS"
                    )
                    health.warnings.append(text)
                    if temp >= limits.temp_stop_c and (hottest is None or temp > hottest[0]):
                        hottest = (temp, text)
        if hottest is not None:
            health.too_hot = f"{hottest[1]} (stop limit {limits.temp_stop_c:.0f} C)"
        return health

    def max_temperatures(self) -> dict[str, dict[str, list[float | None]]]:
        """Highest temperatures seen since start, per arm, ordered by motor id 1..7."""
        ids = range(1, MOTORS_PER_ARM + 1)
        with self._lock:
            return {
                arm: {
                    "rotor": [self._max_rotor[arm].get(i) for i in ids],
                    "mos": [self._max_mos[arm].get(i) for i in ids],
                }
                for arm in self.watched
            }

    def format_table(self) -> str:
        """Human-readable state and temperatures of every watched motor."""
        now = self._clock()
        snap = self.snapshot()
        lines = []
        for arm in self.watched:
            motors = snap[arm]
            if not motors:
                lines.append(f"{arm:5s} no feedback (follower server not driving the motors)")
                continue
            cells = []
            for motor_id in range(1, MOTORS_PER_ARM + 1):
                fb = motors.get(motor_id)
                if fb is None:
                    cells.append(f"m{motor_id}: --")
                    continue
                flag = "" if fb.state == STATE_ENABLED else f" [{fb.state_message}]"
                stale = " (stale)" if now - fb.t > 1.0 else ""
                cells.append(f"m{motor_id}: {fb.temp_rotor:.0f}/{fb.temp_mos:.0f}{flag}{stale}")
            lines.append(f"{arm:5s} " + "  ".join(cells))
        return "\n".join(lines)

    def close(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        for sock in self._socks.values():
            sock.close()
        self._socks.clear()
        self._threads.clear()
