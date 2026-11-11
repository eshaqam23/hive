import time

from fastapi.testclient import TestClient

from hive_exec.api import create_app
from hive_exec.config import ExecSettings, QwenSettings

from .conftest import DEMO


def _client():
    settings = ExecSettings(sim_time_scale=0.005, default_planner="baseline", qwen=QwenSettings(base_url=""))
    return TestClient(create_app(settings))


def _wait(c, statuses, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get("/api/exec/state").json()
        if s["mission"] and s["mission"]["status"] in statuses:
            return s
        time.sleep(0.02)
    raise AssertionError(s["mission"]["status"])


def test_http_known_story_and_export():
    with _client() as c:
        s = c.get("/api/exec/state").json()
        assert s["gateway"]["kind"] == "fake" and s["gateway"]["verified_transport"] is False
        assert s["planners"]["qwen"]["live_enabled"] is False
        c.post("/api/exec/reset", json={"scenario": "known"})
        s = c.post("/api/exec/missions", json={"command": DEMO, "planner": "baseline"}).json()
        rev = s["mission"]["revisions"][-1]
        assert rev["planner"]["kind"] == "baseline" and "Baseline" in rev["planner"]["label"]
        r = c.post(f"/api/exec/revisions/{rev['revision']}/approve", json={"proposal_hash": "nope"})
        assert r.status_code == 409
        c.post(f"/api/exec/revisions/{rev['revision']}/approve", json={"proposal_hash": rev["proposal_hash"]})
        _wait(c, {"completed"})
        r = c.post("/api/exec/missions", json={"command": DEMO})
        assert r.status_code == 409  # one mission per run; reset first
        export = c.get("/api/exec/export").json()
        assert export["schema_version"] == "hive.run-export.v1"
        assert export["events"] and export["attempts"]


def test_http_qwen_unavailable_is_visible():
    with _client() as c:
        s = c.post("/api/exec/missions", json={"command": DEMO, "planner": "qwen"}).json()
        assert s["mission"]["status"] == "planner_error"
        assert s["mission"]["revisions"] == []


def test_http_rejects_unknown_fields_and_faults():
    with _client() as c:
        assert c.post("/api/exec/missions", json={"command": DEMO, "approve": True}).status_code == 422
        assert c.post("/api/exec/faults", json={"fault": "meteor"}).status_code == 400
