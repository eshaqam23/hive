"""GET /api/exec/map (A8): hive.shared-map.v0 from the run's latest map record.

The map is the file a succeeded map_area reported (world.MapRecord.map_ref). It is read only when:

- its path is relative and realpaths inside <root>/runs/ (no "..", no absolute path, no symlink out), where
  <root> is HIVE_AGENT_ROOT for an agent fleet's record and the Hive repo root (fake_world.sim_map_root, which
  tests point at a temp dir with HIVE_SIM_MAP_ROOT) for a fake fleet's record;
- the image's sha256 equals map_ref.sha256;
- it is a binary PGM (P5, maxval 255) with the YAML that map_server/map_saver writes beside it (same stem).

Cells follow ROS map_server's trinary rule (nav2 map_io): occ = shade if negate else 1 - shade, with
shade = value / 255; occ > occupied_thresh is 100, occ < free_thresh is 0, anything else -1. PGM row 0 is the
top of the image; the contract's row 0 is at the origin, so rows are flipped.

Anything else gives {available: false, reason, schema}. Reasons never carry an absolute filesystem path.
docs/shared-map-contract.md is the contract.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml

from .catalog import ROBOTS
from .fleet_report import is_agent
from .sim.fake_world import sim_map_root
from .world import MapRecord

SCHEMA = "hive.shared-map.v0"
NO_MAP = "no map reported in this run"
MAP_MAX_CELLS = 4_000_000  # 100 m x 100 m at 5 cm; read at call time, so tests can lower it
PGM_HEADER_MAX = 4096
YAML_MAX_BYTES = 64 * 1024
EVIDENCE = ("simulated", "emulated", "real")
_WS = b" \t\r\n\v\f"


class MapUnavailable(ValueError):
    """The map record exists but its file cannot be served; the message is the reason."""


def unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "schema": SCHEMA}


def map_evidence(fleet: Optional[dict]) -> str:
    """The evidence a map from this fleet carries. Fail safe: simulated unless a hive_agent report says
    emulated or real."""
    if is_agent(fleet) and fleet.get("evidence") in ("emulated", "real"):
        return fleet["evidence"]
    return "simulated"


def build_shared_map(rec: Optional[MapRecord]) -> dict:
    """The shared map for the latest map record, or why there is none. Blocking file I/O: call it off the
    event loop (api.py uses asyncio.to_thread)."""
    if rec is None:
        return unavailable(NO_MAP)
    try:
        return _build(rec)
    except MapUnavailable as e:
        return unavailable(str(e))
    except OSError as e:  # never echo the absolute path an OSError carries
        return unavailable(f"map file could not be read: {e.strerror or type(e).__name__}")


# ---------------------------------------------------------------- paths
def _root(rec: MapRecord) -> Path:
    if rec.agent:
        env = os.environ.get("HIVE_AGENT_ROOT")
        if not env:
            raise MapUnavailable("an agent fleet's map is read from HIVE_AGENT_ROOT (the hive-agent repo root), "
                                 "which is not set")
        return Path(env)
    return sim_map_root()


def _shown(rel: object) -> str:
    """A path for a reason: quoted, bounded, and never an absolute filesystem path."""
    text = str(rel)
    if text.startswith(("/", "\\")) or (len(text) > 1 and text[1] == ":"):
        return "(an absolute path)"
    return repr(text if len(text) <= 120 else text[:117] + "...")


def _inside_runs(root: Path, rel: object) -> Path:
    """The real path of root/rel when rel is a plain relative path under runs/ that realpaths inside
    root/runs/; MapUnavailable otherwise."""
    bad = MapUnavailable(f"map path {_shown(rel)} must stay inside runs/ under the fleet's root")
    if not isinstance(rel, str) or not rel or "\\" in rel or "\0" in rel:
        raise bad
    parts = PurePosixPath(rel).parts
    if PurePosixPath(rel).is_absolute() or ".." in parts or len(parts) < 2 or parts[0] != "runs":
        raise bad
    runs = os.path.realpath(root / "runs")
    real = os.path.realpath(root / rel)
    if real == runs or os.path.commonpath([runs, real]) != runs:
        raise bad
    return Path(real)


# ---------------------------------------------------------------- the image
def _pgm(data: bytes) -> tuple[int, int, bytes]:
    """(width, height, body) of a binary PGM with maxval 255. Header tokens are separated by whitespace,
    '#' starts a comment to the end of the line, and exactly one whitespace byte follows maxval."""
    tokens: list[bytes] = []
    pos, n = 0, len(data)
    while len(tokens) < 4:
        while pos < n and (data[pos] in _WS or data[pos] == 0x23):
            if data[pos] == 0x23:
                while pos < n and data[pos] not in b"\r\n":
                    pos += 1
            else:
                pos += 1
        start = pos
        while pos < n and data[pos] not in _WS and data[pos] != 0x23:
            pos += 1
        if pos == start or pos > PGM_HEADER_MAX:
            raise MapUnavailable("map image header is truncated or too long")
        tokens.append(data[start:pos])
        if len(tokens) == 1 and tokens[0] != b"P5":
            raise MapUnavailable("map image is not a binary PGM (P5)")
    if not all(t.isdigit() for t in tokens[1:]):
        raise MapUnavailable("map image header has a non-numeric width, height or maxval")
    width, height, maxval = (int(t) for t in tokens[1:])
    if maxval != 255:
        raise MapUnavailable(f"map image maxval {maxval} is not 255")
    if width <= 0 or height <= 0:
        raise MapUnavailable("map image width and height must be positive")
    if width * height > MAP_MAX_CELLS:
        raise MapUnavailable(f"map image too large: {width} x {height} cells (cap {MAP_MAX_CELLS})")
    if pos >= n or data[pos] not in _WS:
        raise MapUnavailable("map image header must end with one whitespace byte")
    body = data[pos + 1:]
    if len(body) != width * height:
        raise MapUnavailable(f"map image has {len(body)} cells, expected {width * height}")
    return width, height, body


# ---------------------------------------------------------------- the yaml
def _number(meta: dict, key: str) -> Optional[float]:
    v = meta.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _yaml_meta(root: Path, rel: str, pgm_real: Path) -> dict:
    yrel = str(PurePosixPath(rel).with_suffix(".yaml"))
    ypath = _inside_runs(root, yrel)
    if not ypath.is_file():
        raise MapUnavailable(f"map yaml {_shown(yrel)} not found beside the image")
    if ypath.stat().st_size > YAML_MAX_BYTES:
        raise MapUnavailable(f"map yaml is larger than {YAML_MAX_BYTES} bytes")
    try:
        meta = yaml.safe_load(ypath.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        raise MapUnavailable("map yaml does not parse") from None
    if not isinstance(meta, dict):
        raise MapUnavailable("map yaml is not a mapping")

    image = meta.get("image")
    if not isinstance(image, str) or not image or "\0" in image:
        raise MapUnavailable("map yaml has no image")
    if os.path.realpath(ypath.parent / image) != str(pgm_real):
        raise MapUnavailable(f"map yaml image {_shown(image)} is not the map_ref image")
    mode = meta.get("mode", "trinary")  # map_server's default
    if mode != "trinary":
        raise MapUnavailable(f"map yaml mode {_shown(mode)} is not trinary")
    negate = meta.get("negate")
    if isinstance(negate, bool) or negate not in (0, 1):
        raise MapUnavailable("map yaml negate must be 0 or 1")
    resolution = _number(meta, "resolution")
    if resolution is None or resolution <= 0:
        raise MapUnavailable("map yaml resolution must be a positive number")
    origin = meta.get("origin")
    if (not isinstance(origin, list) or len(origin) != 3
            or any(_number({"v": v}, "v") is None for v in origin)):
        raise MapUnavailable("map yaml origin must be [x, y, yaw]")
    occupied, free = _number(meta, "occupied_thresh"), _number(meta, "free_thresh")
    if occupied is None or free is None or not 0.0 <= free < occupied <= 1.0:
        raise MapUnavailable("map yaml needs 0 <= free_thresh < occupied_thresh <= 1")
    return {"negate": negate, "resolution": resolution, "origin": [float(v) for v in origin],
            "occupied": occupied, "free": free}


# ---------------------------------------------------------------- the map
def _lut(negate: int, occupied: float, free: float) -> list[int]:
    out = []
    for v in range(256):
        shade = v / 255.0
        occ = shade if negate else 1.0 - shade
        out.append(100 if occ > occupied else 0 if occ < free else -1)
    return out


def _build(rec: MapRecord) -> dict:
    ref = rec.map_ref if isinstance(rec.map_ref, dict) else {}
    root = _root(rec)
    rel = ref.get("path")
    pgm = _inside_runs(root, rel)
    if not pgm.is_file():
        raise MapUnavailable(f"map image {_shown(rel)} not found under the fleet's root")
    if pgm.stat().st_size > MAP_MAX_CELLS + PGM_HEADER_MAX:
        raise MapUnavailable(f"map image too large (cap {MAP_MAX_CELLS} cells)")
    data = pgm.read_bytes()
    want = ref.get("sha256")
    if not isinstance(want, str) or hashlib.sha256(data).hexdigest() != want.lower():
        raise MapUnavailable("map image sha256 does not match map_ref.sha256")
    width, height, body = _pgm(data)
    meta = _yaml_meta(root, rel, pgm)

    lut = _lut(meta["negate"], meta["occupied"], meta["free"])
    cells: list[int] = []
    for y in range(height):  # contract row y is PGM row height-1-y
        start = (height - 1 - y) * width
        cells.extend(lut[v] for v in body[start:start + width])
    known = sum(1 for v in cells if v != -1)
    x, y, yaw = meta["origin"]
    return {
        "available": True,
        "schema": SCHEMA,
        "occupancy": {
            "resolution": meta["resolution"],
            "origin": {"x": x, "y": y, "theta": yaw},
            "width": width,
            "height": height,
            "data": cells,
            "revision": rec.seq,
            "epoch": rec.id,
        },
        "robots": [{"robot_id": rid, "pose": None, "source": None, "age_s": None} for rid in sorted(ROBOTS)],
        "objects": [],
        "regions": [],
        "known_fraction": round(known / len(cells), 4),
        "provenance": {
            "source": "map_area",
            "robot_id": rec.robot_id,
            "attempt_id": rec.attempt_id,
            "coverage": rec.coverage,
            "evidence": rec.evidence if rec.evidence in EVIDENCE else "simulated",
        },
    }
