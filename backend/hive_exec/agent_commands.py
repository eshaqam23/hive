"""What an instance agent receives: the exact task envelope JSON and a one-line rendering of it.

``envelope_json`` reproduces the Synapse bridge byte for byte. The bridge parses the envelope the
Executive sends and builds ``ExecuteSkill.Goal.envelope_json`` with ``JSON.stringify``, so floats
such as ``timeout_s=120.0`` reach the robot as ``120``. Writing the same bytes here lets the
recorded command files be compared directly with what a robot logged.

``describe_command`` matches ``describeAction`` in src/executive/api.js.
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


def _js_number(x: float) -> str:
    """ECMAScript Number::toString for a finite double (JSON.stringify gives null for NaN/Infinity)."""
    if not math.isfinite(x):
        return "null"
    if x == 0:
        return "0"  # JSON.stringify(-0) is "0"
    sign = "-" if x < 0 else ""
    _, digits, exp = Decimal(repr(abs(x))).as_tuple()  # repr = shortest round-trip digits, as in JS
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    s = "".join(map(str, digits))
    k, n = len(s), len(s) + exp  # value = s * 10^(n-k)
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + s
    e = n - 1
    mant = s if k == 1 else s[0] + "." + s[1:]
    return f"{sign}{mant}e{'+' if e >= 0 else '-'}{abs(e)}"


def _js_key_order(keys: list[str]) -> list[str]:
    """JS objects list array-index keys first (ascending), then string keys in insertion order."""
    idx = sorted((k for k in keys if _ARRAY_INDEX.fullmatch(k) and int(k) < 2**32 - 1), key=int)
    return idx + [k for k in keys if k not in idx]


def _encode(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _js_number(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_encode(i) for i in v) + "]"
    if isinstance(v, dict):
        keys = _js_key_order([str(k) for k in v])
        by_str = {str(k): val for k, val in v.items()}
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{_encode(by_str[k])}" for k in keys) + "}"
    raise TypeError(f"not JSON-serializable: {type(v).__name__}")


def envelope_json(envelope: dict) -> str:
    """The exact ExecuteSkill.Goal.envelope_json string the Synapse bridge sends for this envelope."""
    return _encode(envelope)


def _arg(v: Any) -> str:
    if isinstance(v, dict):
        return _encode(v["label"]) if v.get("label") else _encode(v)
    if isinstance(v, (list, tuple)):
        return _encode(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _encode(v)
    return _encode(str(v))


def describe_command(skill: str, params: dict) -> str:
    """One line, e.g. inspect_area(area_id="work_area", object_query="red_block")."""
    args = ", ".join(f"{k}={_arg(v)}" for k, v in params.items() if v is not None)
    return f"{skill}({args})"


def command_path(runs_dir: Path, run_id: str, robot_id: str, task_id: str, revision: int, attempt_no: int) -> Path:
    return Path(runs_dir) / run_id / "agent_commands" / robot_id / f"{task_id}-r{revision}-a{attempt_no}.json"


def write_command(runs_dir: Optional[Path], envelope: dict, attempt_no: int) -> Optional[Path]:
    """Write the envelope exactly as sent. Returns the path, or None when recording is disabled."""
    if runs_dir is None:
        return None
    path = command_path(runs_dir, envelope["run_id"], envelope["robot_id"], envelope["task_id"],
                        envelope["revision"], attempt_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(envelope_json(envelope), encoding="utf-8")
    return path
