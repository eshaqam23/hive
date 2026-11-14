"""Pure robot-link logic: motor-line parsing, stop confirmation, power/twist math,
gripper interface detection and camera profile constants. No ROS imports."""
import math
import re
from typing import Any, NamedTuple, Optional

LINEAR_SCALE = 0.5   # m/s at power 1.0
ANGULAR_SCALE = 5.0  # rad/s at power 1.0
MIN_POWER = 0.2
MAX_POWER = 1.0
DEFAULT_POWER = 0.5

STOP_WINDOW = 5.0         # seconds of resends after a stop request
UNCONFIRMED_AFTER = 0.5   # seconds before the first resend when reporting
RESEND_INTERVAL = 0.2
_EPS = 1e-9

CAMERA_PROFILES = {"low": "424x240x6", "medium": "424x240x15", "high": "640x480x15"}


_INT_RE = re.compile(r"-?[0-9]+")


class MotorLine(NamedTuple):
    kind: str
    left_pwm: int
    left_dir: int
    right_pwm: int
    right_dir: int
    angles: tuple


def _ints(parts):
    if not all(_INT_RE.fullmatch(p) for p in parts):
        return None
    try:
        return [int(p) for p in parts]
    except ValueError:  # e.g. over the int string-conversion digit limit
        return None


def _floats(parts):
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    return vals if all(math.isfinite(v) for v in vals) else None


def parse_motor_line(line: str) -> Optional[MotorLine]:
    """Parse a serial line echoed on /ros_data; None if malformed."""
    if not isinstance(line, str):
        return None
    parts = [p.strip() for p in line.strip().split(",")]
    if parts and parts[0] == "G":
        if len(parts) not in (2, 3):
            return None
        angles = _floats(parts[1:])
        return MotorLine("gripper", 0, 0, 0, 0, tuple(angles)) if angles else None
    if parts and parts[0] == "D":
        parts = parts[1:]
    if len(parts) != 4:
        return None
    nums = _ints(parts)
    return MotorLine("drive", *nums, ()) if nums else None


def is_zero_drive(line: Optional[MotorLine]) -> bool:
    return line is not None and line.kind == "drive" and line.left_pwm == 0 and line.right_pwm == 0


def clamp_power(value: Any) -> float:
    if value is None:
        return DEFAULT_POWER
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        raise ValueError("power must be a number")
    return min(MAX_POWER, max(MIN_POWER, float(value)))


def twist_for(action: str, power: float) -> tuple:
    """Return (linear, angular) for a drive action at the given power."""
    if action == "forward":
        return (power * LINEAR_SCALE, 0.0)
    if action == "backward":
        return (-power * LINEAR_SCALE, 0.0)
    if action == "left":
        return (0.0, power * ANGULAR_SCALE)
    if action == "right":
        return (0.0, -power * ANGULAR_SCALE)
    if action == "stop":
        return (0.0, 0.0)
    raise ValueError(f"unknown action: {action}")


def detect_gripper(subscribed: set, services: set) -> Optional[str]:
    if "/gripper/grab" in services and "/gripper/release" in services:
        return "trigger"
    if "/gripper/position" in subscribed:
        return "position"
    if "/gripper_open" in subscribed:
        return "bool"
    return None


def camera_params(quality: str, depth: bool) -> list:
    if quality not in CAMERA_PROFILES:
        raise ValueError(f"unknown camera quality: {quality}")
    return [("enable_depth", depth), ("rgb_camera.color_profile", CAMERA_PROFILES[quality])]


def profile_variants(profile: str) -> list:
    return [profile, profile.replace("x", ",")]


class StopConfirmer:
    """Tracks whether a stop request was confirmed by a zero-drive line.

    States: idle | pending | confirmed | unconfirmed | unreported. Time is injected.
    """

    def __init__(self):
        self._reporting = True
        self._t0: Optional[float] = None
        self._confirmed = False
        self._next_k = 0  # index of the next scheduled resend

    def set_reporting(self, reporting: bool) -> None:
        self._reporting = bool(reporting)

    def stop_requested(self, now: float) -> None:
        self._t0 = now
        self._confirmed = False
        self._next_k = 0

    def observe(self, line: Optional[MotorLine], now: float) -> None:
        if self._t0 is not None and now >= self._t0 and is_zero_drive(line):
            self._confirmed = True

    def poll(self, now: float) -> tuple:
        if self._t0 is None:
            return ("idle", False)
        if self._confirmed:
            return ("confirmed", False)
        elapsed = now - self._t0
        if self._reporting:
            if elapsed < UNCONFIRMED_AFTER - _EPS:
                return ("pending", False)
            state, first = "unconfirmed", UNCONFIRMED_AFTER
        else:
            state, first = "unreported", RESEND_INTERVAL
        if elapsed > STOP_WINDOW + _EPS:
            return (state, False)
        due = first + self._next_k * RESEND_INTERVAL
        if elapsed + _EPS < due:
            return (state, False)
        # resend once, then skip any missed slots
        self._next_k = int((elapsed + _EPS - first) / RESEND_INTERVAL) + 1
        return (state, True)
