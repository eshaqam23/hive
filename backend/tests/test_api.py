import time

from fastapi.testclient import TestClient

from hive_exec.api import create_app
from hive_exec.config import ExecSettings, QwenSettings

from .conftest import DEMO


def _client(time_scale=0.005, **kw):
    settings = ExecSettings(sim_time_scale=time_scale, default_planner="baseline", qwen=QwenSettings(base_url=""),
                            **kw)
    return TestClient(create_app(settings))


def _runtime(s, task_id):
    m = s["mission"]
    return m["revisions"][m["active_revision"] - 1]["runtime"][task_id]


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
        assert s["planners"]["llm"]["live_enabled"] is False and "qwen" not in s["planners"]
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
        s = c.post("/api/exec/missions", json={"command": DEMO, "planner": "qwen"}).json()  # alias of "llm"
        assert s["mission"]["status"] == "planner_error"
        assert s["mission"]["revisions"] == []
        assert s["mission"]["planner_kind"] == "llm"
        assert "not configured" in s["mission"]["outcome"]["reason"]


def test_http_rejects_unknown_fields_and_faults():
    with _client() as c:
        assert c.post("/api/exec/missions", json={"command": DEMO, "approve": True}).status_code == 422
        assert c.post("/api/exec/faults", json={"fault": "meteor"}).status_code == 400


def test_http_task_stop_one_task_then_recovery():
    with _client(time_scale=0.2) as c:
        c.post("/api/exec/reset", json={"scenario": "unknown", "faults": ["hang_scan"]})
        s = c.post("/api/exec/missions", json={"command": DEMO, "planner": "baseline"}).json()
        rev = s["mission"]["revisions"][-1]
        scan = next(t["task_id"] for t in rev["tasks"] if t["action"]["kind"] == "scan_route")
        cam = next(t["task_id"] for t in rev["tasks"] if t["action"]["kind"] == "inspect_area")
        c.post(f"/api/exec/revisions/{rev['revision']}/approve", json={"proposal_hash": rev["proposal_hash"]})
        end = time.time() + 5
        while time.time() < end:
            s = c.get("/api/exec/state").json()
            if _runtime(s, scan)["state"] == "accepted" and _runtime(s, cam)["state"] in ("accepted", "running"):
                break
            time.sleep(0.01)
        r = c.post(f"/api/exec/tasks/{scan}/cancel")
        assert r.status_code == 200
        assert _runtime(r.json(), scan)["state"] in ("canceling", "canceled")
        s = _wait(c, {"awaiting_approval", "blocked", "completed"})
        assert s["mission"]["status"] == "awaiting_approval" and s["mission"]["recovery_used"] is True
        assert s["mission"]["revisions"][0]["runtime"][scan]["state"] == "canceled"
        assert s["mission"]["revisions"][0]["runtime"][cam]["state"] == "succeeded"
        assert c.post("/api/exec/tasks/t99/cancel").status_code == 409  # nothing executing now
