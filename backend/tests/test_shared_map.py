"""A8 step 6: GET /api/exec/map serves hive.shared-map.v0 from the run's latest map record.

The map file is read only from <root>/runs/ (root = HIVE_AGENT_ROOT for an agent fleet, the Hive repo root
for the fake fleets, HIVE_SIM_MAP_ROOT in tests), only when its sha256 matches the record's map_ref, and is
mapped to -1/0/100 by ROS map_server's trinary rule with the PGM's rows flipped (PGM row 0 is the top; the
contract's row 0 is at the origin).
"""

from __future__ import annotations

import hashlib
import os
import time

import pytest
from fastapi.testclient import TestClient

from hive_exec import shared_map
from hive_exec.api import create_app
from hive_exec.catalog import ROBOTS
from hive_exec.config import ExecSettings, QwenSettings
from hive_exec.contracts import Observation
from hive_exec.shared_map import NO_MAP, SCHEMA, build_shared_map, map_evidence
from hive_exec.world import MapRecord

from .conftest import approve_latest, wait_for
from .fleet_stub import EMULATED_FLEET, StubFleetGateway, agent_robots
from .test_fleet_report import _started

# PGM rows are top-first. Contract rows are bottom-first, so this 4 x 3 image reads back as
# [0, 0, 0, 100,  0, -1, 0, 0,  100, 0, 0, -1] (254 free, 205 unknown, 0 occupied).
ROWS = ((0, 254, 254, 205),
        (254, 205, 254, 254),
        (254, 254, 254, 0))
FLIPPED = [0, 0, 0, 100, 0, -1, 0, 0, 100, 0, 0, -1]
DONE = {"completed", "blocked", "rejected", "canceled", "planner_error", "answered"}
HEADER = b"P5\n# test map\n%d %d\n255\n"


def _yaml(image="m.pgm", mode="trinary", resolution=0.1, origin="[1.5, -2.0, 0.25]", negate=0,
          occupied=0.65, free=0.196, extra=""):
    lines = [f"image: {image}", f"resolution: {resolution}", f"origin: {origin}", f"negate: {negate}",
             f"occupied_thresh: {occupied}", f"free_thresh: {free}"]
    if mode is not None:
        lines.insert(1, f"mode: {mode}")
    return "\n".join(lines) + "\n" + extra


def _write(root, rel="runs/t/m.pgm", rows=ROWS, header=HEADER, body=None, yaml_text=None, **yaml_kw):
    pgm = root / rel
    pgm.parent.mkdir(parents=True, exist_ok=True)
    w, h = len(rows[0]), len(rows)
    data = (header % (w, h) if b"%d" in header else header) + (body if body is not None else
                                                              bytes(v for row in rows for v in row))
    pgm.write_bytes(data)
    if yaml_text is not False:
        yaml_kw.setdefault("image", pgm.name)
        pgm.with_suffix(".yaml").write_text(_yaml(**yaml_kw) if yaml_text is None else yaml_text)
    return {"id": "m", "path": rel, "sha256": hashlib.sha256(data).hexdigest()}


def _rec(ref, seq=3, agent=False, evidence="simulated"):
    return MapRecord(id=f"map-{seq}-abcd", seq=seq, area_id="arena", coverage=0.83, map_ref=dict(ref),
                     robot_id="lidar_01", attempt_id="run-1:t1:1", observed_at=0.0, source="lidar_01",
                     evidence=evidence, agent=agent)


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "hive_root"
    r.mkdir()
    monkeypatch.setenv("HIVE_SIM_MAP_ROOT", str(r))
    monkeypatch.delenv("HIVE_AGENT_ROOT", raising=False)
    return r


def _off(m):
    assert m["available"] is False and m["schema"] == SCHEMA and m["reason"], m
    assert set(m) == {"available", "reason", "schema"}
    return m["reason"]


# ---------------------------------------------------------------- the grid
def test_no_record_is_unavailable_with_the_reason():
    assert build_shared_map(None) == {"available": False, "reason": NO_MAP, "schema": SCHEMA}
    assert NO_MAP == "no map reported in this run"


def test_asymmetric_fixture_flips_rows_and_maps_trinary_values(root):
    m = build_shared_map(_rec(_write(root)))
    assert m["available"] is True and m["schema"] == SCHEMA
    occ = m["occupancy"]
    assert (occ["width"], occ["height"], occ["resolution"]) == (4, 3, 0.1)
    assert occ["origin"] == {"x": 1.5, "y": -2.0, "theta": 0.25}
    assert occ["data"] == FLIPPED  # PGM row 0 (top) is the contract's last row
    assert occ["revision"] == 3 and occ["epoch"] == "map-3-abcd"
    assert m["known_fraction"] == round(10 / 12, 4)
    assert m["robots"] == [{"robot_id": rid, "pose": None, "source": None, "age_s": None} for rid in sorted(ROBOTS)]
    assert m["objects"] == [] and m["regions"] == []
    assert m["provenance"] == {"source": "map_area", "robot_id": "lidar_01", "attempt_id": "run-1:t1:1",
                               "coverage": 0.83, "evidence": "simulated"}


def test_negate_and_the_yaml_thresholds_follow_map_server(root):
    m = build_shared_map(_rec(_write(root, rows=((0, 254, 205),), negate=1)))
    assert m["occupancy"]["data"] == [0, 100, 100]  # negate: dark is free, light is occupied
    m = build_shared_map(_rec(_write(root, rows=((205, 254, 0),), occupied=0.1, free=0.05)))
    assert m["occupancy"]["data"] == [100, 0, 100]  # occ 0.196 > occupied_thresh 0.1


def test_header_comments_and_token_layout_are_read(root):
    ref = _write(root, header=b"P5 # magic\n# a comment\n%d\n# between\n%d 255\n")
    assert build_shared_map(_rec(ref))["occupancy"]["data"] == FLIPPED


def test_a_missing_mode_defaults_to_trinary_like_map_server(root):
    assert build_shared_map(_rec(_write(root, mode=None)))["occupancy"]["data"] == FLIPPED


# ---------------------------------------------------------------- refusals: the file
@pytest.mark.parametrize("kw, needle", [
    ({"header": b"P2\n%d %d\n255\n"}, "P5"),
    ({"header": b"P5\n%d %d\n65535\n"}, "maxval"),
    ({"body": bytes(11)}, "cells"),
    ({"body": bytes(13)}, "cells"),
    ({"yaml_text": False}, "yaml"),
    ({"yaml_text": "- not\n- a mapping\n"}, "yaml"),
    ({"yaml_text": "image: [unclosed\n"}, "yaml"),
    ({"mode": "scale"}, "trinary"),
    ({"image": "other.pgm"}, "image"),
    ({"negate": 2}, "negate"),
    ({"resolution": 0}, "resolution"),
    ({"origin": "[1.0, 2.0]"}, "origin"),
    ({"free": 0.7, "occupied": 0.65}, "thresh"),
])
def test_a_malformed_map_is_unavailable_with_a_reason(root, kw, needle):
    assert needle in _off(build_shared_map(_rec(_write(root, **kw))))


def test_a_wrong_sha256_is_unavailable(root):
    ref = _write(root)
    ref["sha256"] = "0" * 64
    assert "sha256" in _off(build_shared_map(_rec(ref)))


def test_a_missing_file_is_unavailable(root):
    assert "not found" in _off(build_shared_map(_rec({"id": "m", "path": "runs/t/none.pgm", "sha256": "0" * 64})))


def test_a_map_larger_than_the_cap_is_unavailable(root, monkeypatch):
    monkeypatch.setattr(shared_map, "MAP_MAX_CELLS", 11)
    assert "too large" in _off(build_shared_map(_rec(_write(root))))


# ---------------------------------------------------------------- refusals: the path
@pytest.mark.parametrize("rel, up, written", [
    ("../outside/m.pgm", True, "outside/m.pgm"),     # beside the root
    ("runs/../outside/m.pgm", False, "outside/m.pgm"),
    ("other/m.pgm", False, "other/m.pgm"),           # inside the root, not in runs/, no ".."
])
def test_a_path_outside_runs_is_unavailable(root, rel, up, written):
    ref = _write(root.parent if up else root, rel=written)  # the file exists there and its sha is right
    ref["path"] = rel
    assert "runs/" in _off(build_shared_map(_rec(ref)))


def test_an_absolute_path_is_unavailable(root):
    ref = _write(root)
    ref["path"] = str(root / "runs/t/m.pgm")
    assert "runs/" in _off(build_shared_map(_rec(ref)))


def test_a_symlink_out_of_runs_is_unavailable(root):
    outside = _write(root, rel="elsewhere/m.pgm")  # outside runs/, sha right
    (root / "runs" / "t").mkdir(parents=True)
    os.symlink(root / "elsewhere" / "m.pgm", root / "runs" / "t" / "m.pgm")
    os.symlink(root / "elsewhere" / "m.yaml", root / "runs" / "t" / "m.yaml")
    assert "runs/" in _off(build_shared_map(_rec({**outside, "path": "runs/t/m.pgm"})))


def test_a_yaml_symlinked_out_of_runs_is_unavailable(root):
    ref = _write(root)
    (root / "elsewhere").mkdir()
    (root / "runs/t/m.yaml").rename(root / "elsewhere/m.yaml")
    os.symlink(root / "elsewhere" / "m.yaml", root / "runs" / "t" / "m.yaml")
    assert "runs/" in _off(build_shared_map(_rec(ref)))


# ---------------------------------------------------------------- roots and provenance
def test_an_agent_map_needs_hive_agent_root(root):
    ref = _write(root)  # present in the Hive root, which an agent map never uses
    assert "HIVE_AGENT_ROOT" in _off(build_shared_map(_rec(ref, agent=True, evidence="emulated")))


def test_an_agent_map_is_read_from_hive_agent_root(root, tmp_path, monkeypatch):
    agent_root = tmp_path / "agent"
    ref = _write(agent_root)
    monkeypatch.setenv("HIVE_AGENT_ROOT", str(agent_root))
    m = build_shared_map(_rec(ref, agent=True, evidence="emulated"))
    assert m["available"] is True and m["occupancy"]["data"] == FLIPPED
    assert m["provenance"]["evidence"] == "emulated"
    assert "not found" in _off(build_shared_map(_rec(ref)))  # a fake-fleet record never reads the agent root


@pytest.mark.parametrize("fleet, evidence", [
    (None, "simulated"),
    ({"kind": "hive_agent", "evidence": "emulated"}, "emulated"),
    ({"kind": "hive_agent", "evidence": "real"}, "real"),
    ({"kind": "hive_agent", "evidence": None}, "simulated"),
    ({"kind": "hive_agent", "evidence": "hardware"}, "simulated"),
    ({"kind": "other", "evidence": "real"}, "simulated"),
])
def test_map_evidence_is_fail_safe(fleet, evidence):
    assert map_evidence(fleet) == evidence


async def test_an_agent_fleet_map_record_carries_its_evidence():
    ex = await _started(StubFleetGateway(agent_robots(), dict(EMULATED_FLEET)))
    obs = Observation(kind="area_mapped", entity_id="map:arena", area_id="arena",
                      attrs={"coverage": 0.9, "map_ref": {"id": "m", "path": "runs/a/m.pgm", "sha256": "a" * 64}})
    rec = ex._record_map(obs, "lidar_01", "run:t1:1")
    assert rec.agent is True and rec.evidence == "emulated" and ex.world.latest_map() is rec


async def test_the_fake_map_area_records_a_simulated_map_that_is_served(make_exec):
    ex = await make_exec()
    await ex.create_mission("Map the arena.")
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"
    rec = ex.world.latest_map()
    assert rec.agent is False and rec.evidence == "simulated"
    m = build_shared_map(rec)
    assert m["available"] is True and m["provenance"]["evidence"] == "simulated"
    assert m["known_fraction"] == rec.coverage == 0.95  # the synthetic grid's own coverage


# ---------------------------------------------------------------- over HTTP
def _wait(c, statuses, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get("/api/exec/state").json()
        if s["mission"] and s["mission"]["status"] in statuses:
            return s
        time.sleep(0.02)
    raise AssertionError(s["mission"]["status"])


def test_http_map_after_the_fake_map_area_is_available_with_sim_provenance():
    settings = ExecSettings(sim_time_scale=0.005, default_planner="baseline", qwen=QwenSettings(base_url=""))
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/exec/map").json() == {"available": False, "reason": NO_MAP, "schema": SCHEMA}
        s = c.post("/api/exec/missions", json={"command": "Map the arena.", "planner": "baseline"}).json()
        rev = s["mission"]["revisions"][-1]
        c.post(f"/api/exec/revisions/{rev['revision']}/approve", json={"proposal_hash": rev["proposal_hash"]})
        _wait(c, {"completed"})
        m = c.get("/api/exec/map").json()
        assert m["available"] is True and m["schema"] == SCHEMA
        occ = m["occupancy"]
        assert (occ["width"], occ["height"], occ["revision"]) == (80, 60, 1) and occ["epoch"].startswith("map-1-")
        assert len(occ["data"]) == 80 * 60 and occ["data"][0] == 100  # bottom-left corner: the wall
        assert m["provenance"]["evidence"] == "simulated" and m["provenance"]["robot_id"] == "lidar_01"
        assert m["provenance"]["source"] == "map_area" and m["provenance"]["coverage"] == 0.95
        assert [r["robot_id"] for r in m["robots"]] == sorted(ROBOTS)
        c.post("/api/exec/reset", json={})
        assert c.get("/api/exec/map").json()["reason"] == NO_MAP  # a reset starts a new run
