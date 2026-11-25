import time

from fastapi.testclient import TestClient

from hive_exec.api import create_app
from hive_exec.config import ExecSettings, QwenSettings
from hive_exec.executive import Executive
from hive_exec.gateway.fake import FakeGateway
from hive_exec.gateway.synapse import SynapseGateway
from hive_exec.planner.baseline import BaselinePlanner

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
        r = c.post("/api/exec/missions", json={"command": DEMO, "planner": "qwen"})  # alias of "llm"
        assert r.status_code == 503
        body = r.json()
        assert body["detail"]["missing"] == ["planner:llm"]
        assert "planner llm is down" in body["error"] and "not bound to a provider" in body["error"]
        assert c.get("/api/exec/state").json()["mission"] is None  # nothing was started


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


def test_http_task_stop_names_its_revision():
    with _client(time_scale=0.2) as c:
        c.post("/api/exec/reset", json={"scenario": "unknown", "faults": ["hang_scan"]})
        s = c.post("/api/exec/missions", json={"command": DEMO, "planner": "baseline"}).json()
        rev = s["mission"]["revisions"][-1]
        scan = next(t["task_id"] for t in rev["tasks"] if t["action"]["kind"] == "scan_route")
        c.post(f"/api/exec/revisions/{rev['revision']}/approve", json={"proposal_hash": rev["proposal_hash"]})
        _wait(c, {"executing"})
        assert c.post(f"/api/exec/tasks/{scan}/cancel", json={"revision": "one"}).status_code == 422
        assert c.post(f"/api/exec/tasks/{scan}/cancel", json={"revision": 1, "x": 1}).status_code == 422
        assert c.post(f"/api/exec/tasks/{scan}/cancel", json={"revision": 9}).status_code == 404
        r = c.post(f"/api/exec/tasks/{scan}/cancel", json={"revision": rev["revision"]})
        assert r.status_code == 200 and _runtime(r.json(), scan)["state"] in ("canceling", "canceled")


def test_http_health_reports_each_dependency():
    with _client() as c:
        h = c.get("/api/exec/health").json()
    assert h["ok"] is True and h["ready"] is True and h["missing"] == []
    assert h["gateway"] == {"kind": "fake", "label": h["gateway"]["label"], "connected": True,
                            "verified_round_trip": False, "detail": h["gateway"]["detail"],
                            "simulated": True, "fleet": None}
    assert h["default_planner"] == "baseline" and h["planner"]["ready"] is True
    llm = h["planners"]["llm"]
    assert llm["role"] == "executive" and llm["bound"] is False and llm["ready"] is False
    assert llm["last_probe"] is None and "not bound" in llm["reason"]
    assert h["bridge"] == {"used": False} and h["rosbridge"] == {"used": False}


def test_http_health_names_an_unbound_default_planner():
    settings = ExecSettings(sim_time_scale=0.005, default_planner="llm", qwen=QwenSettings(base_url=""))
    with TestClient(create_app(settings)) as c:
        h = c.get("/api/exec/health").json()
        assert h["ready"] is False and h["missing"] == ["planner:llm"]
        r = c.post("/api/exec/missions", json={"command": DEMO})  # default planner
        assert r.status_code == 503 and r.json()["detail"]["missing"] == ["planner:llm"]
        assert c.post("/api/exec/missions", json={"command": DEMO, "planner": "baseline"}).status_code == 200


class _SwitchableGateway(FakeGateway):
    up = True

    def info(self):
        gi = super().info()
        gi.connected = self.up
        gi.detail = gi.detail if self.up else "link down (test)"
        return gi


def test_http_gateway_down_gates_start_and_approve_but_never_cancel():
    gw = _SwitchableGateway(time_scale=0.005)
    ex = Executive(gw, {"baseline": BaselinePlanner()}, default_planner="baseline")
    with TestClient(create_app(ExecSettings(default_planner="baseline"), executive=ex)) as c:
        s = c.post("/api/exec/missions", json={"command": DEMO}).json()
        rev = s["mission"]["revisions"][-1]
        gw.up = False
        h = c.get("/api/exec/health").json()
        assert h["ready"] is False and h["missing"] == ["gateway"] and h["gateway"]["connected"] is False
        r = c.post(f"/api/exec/revisions/{rev['revision']}/approve", json={"proposal_hash": rev["proposal_hash"]})
        assert r.status_code == 503 and r.json()["detail"]["missing"] == ["gateway"]
        assert "robot gateway" in r.json()["error"] and "link down (test)" in r.json()["error"]
        assert c.post("/api/exec/missions", json={"command": DEMO}).status_code == 503
        assert c.post("/api/exec/tasks/t1/cancel").status_code == 409  # not gated: refused on its own terms
        r = c.post("/api/exec/cancel")
        assert r.status_code == 200 and r.json()["mission"]["status"] == "canceled"


def test_synapse_health_reports_bridge_and_rosbridge_separately():
    gw = SynapseGateway("ws://127.0.0.1:8790")  # not started: both links down
    links = gw.transport_health()
    assert links["bridge"]["used"] and links["bridge"]["url"] == "ws://127.0.0.1:8790"
    assert links["bridge"]["connected"] is False and links["rosbridge"]["connected"] is False


def test_http_map_stub():
    with _client() as c:
        assert c.get("/api/exec/map").json() == {
            "available": False, "reason": "shared perception not built yet", "schema": "hive.shared-map.v0"}


# -- sim world plumbing ------------------------------------------------------------------


def test_http_reset_world_and_seed_show_in_state_export_and_truth(monkeypatch):
    monkeypatch.delenv("HIVE_SIM_WORLD", raising=False)
    with _client() as c:
        assert c.get("/api/exec/state").json()["sim"] == {"world": "fixed", "seed": None}
        r = c.post("/api/exec/reset", json={"scenario": "unknown", "world": "random", "seed": 1234})
        assert r.status_code == 200
        assert c.get("/api/exec/state").json()["sim"] == {"world": "random", "seed": 1234}
        assert c.get("/api/exec/export").json()["sim"] == {"world": "random", "seed": 1234}
        truth = c.get("/api/exec/debug/truth").json()
        assert "simulation" in truth["warning"].lower() or "truth" in truth["warning"].lower()
        t = truth["truth"]
        assert t["world"] == "random" and t["seed"] == 1234
        assert set(t["corridor"]) == {"pickup_corridor"}
        assert all({"label", "location_id", "held_by"} <= set(o) for o in t["objects"].values())
        assert all({"location_id", "battery_pct"} <= set(r) for r in t["robots"].values())
        c.post("/api/exec/reset", json={"scenario": "unknown"})
        assert c.get("/api/exec/state").json()["sim"] == {"world": "fixed", "seed": None}


def test_http_reset_rejects_bad_world_or_seed():
    with _client() as c:
        assert c.post("/api/exec/reset", json={"world": "chaos"}).status_code == 422
        assert c.post("/api/exec/reset", json={"world": "random", "seed": -1}).status_code == 422
        assert c.post("/api/exec/reset", json={"world": "random", "seed": "abc"}).status_code == 422


def test_http_env_random_world_is_the_default(monkeypatch):
    monkeypatch.setenv("HIVE_SIM_WORLD", "random")
    with _client() as c:
        sim = c.get("/api/exec/state").json()["sim"]
        assert sim["world"] == "random" and isinstance(sim["seed"], int)
        c.post("/api/exec/reset", json={"scenario": "unknown"})
        assert c.get("/api/exec/state").json()["sim"]["world"] == "random"
        c.post("/api/exec/reset", json={"scenario": "unknown", "world": "fixed"})
        assert c.get("/api/exec/state").json()["sim"] == {"world": "fixed", "seed": None}


def test_http_env_bad_world_fails_loudly(monkeypatch):
    import pytest

    monkeypatch.setenv("HIVE_SIM_WORLD", "chaos")
    with pytest.raises(ValueError, match="HIVE_SIM_WORLD"):
        create_app(ExecSettings(default_planner="baseline", qwen=QwenSettings(base_url="")))


# -- WS5: operator messages ------------------------------------------------------------------
def test_http_operator_messages_queue_cancel_send_now_and_export():
    with _client() as c:
        r = c.post("/api/exec/mission/messages", json={"text": "use the camera only", "mode": "now"})
        assert r.status_code == 409  # no mission yet
        c.post("/api/exec/missions", json={"command": DEMO, "planner": "baseline"})
        for bad in [{"text": "", "mode": "now"}, {"text": "x" * 501, "mode": "now"},
                    {"text": "ok", "mode": "later"}, {"text": "ok", "mode": "now", "extra": 1}]:
            assert c.post("/api/exec/mission/messages", json=bad).status_code in (400, 422)
        s = c.post("/api/exec/mission/messages", json={"text": "use the camera only", "mode": "queue"}).json()
        msg = s["message"]
        assert set(msg) >= {"id", "text", "mode", "status", "created_at"} and msg["status"] == "queued"
        assert s["mission"]["operator_messages"][0]["id"] == msg["id"]  # full state comes back for the UI
        r = c.delete(f"/api/exec/mission/messages/{msg['id']}")
        assert r.status_code == 200 and r.json()["message"]["status"] == "canceled"
        assert c.delete(f"/api/exec/mission/messages/{msg['id']}").status_code == 409
        assert c.delete("/api/exec/mission/messages/msg-404").status_code == 404
        msg = c.post("/api/exec/mission/messages", json={"text": "use the camera only", "mode": "queue"}).json()["message"]
        r = c.post(f"/api/exec/mission/messages/{msg['id']}/send-now")
        assert r.status_code == 200 and r.json()["message"]["status"] == "delivered"
        s = _wait(c, {"awaiting_approval"})
        revs = s["mission"]["revisions"]
        assert revs[0]["status"] == "superseded" and revs[0]["superseded_reason"] == "operator_message"
        assert revs[-1]["kind"] == "feedback" and {t["robot_id"] for t in revs[-1]["tasks"]} == {"camera_01"}
        assert c.delete(f"/api/exec/mission/messages/{msg['id']}").status_code == 409  # delivered
        rows = c.get("/api/exec/export").json()["mission"]["operator_messages"]
        # fix 3 (I1): applied only once revision 2 is approved; until then delivered with no revision
        assert [(x["status"], x["delivered_to_revision"]) for x in rows] == [("canceled", None), ("delivered", None)]
        c.post("/api/exec/cancel")
        _wait(c, {"canceled"})
        r = c.post("/api/exec/mission/messages", json={"text": "hello", "mode": "queue"})
        assert r.status_code == 409 and "new mission" in r.json()["error"]


def test_http_lone_surrogate_is_a_422_and_nothing_is_stored():
    settings = ExecSettings(sim_time_scale=0.005, default_planner="baseline", qwen=QwenSettings(base_url=""))
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        c.post("/api/exec/reset", json={"scenario": "unknown"})
        bad = {"content-type": "application/json"}
        r = c.post("/api/exec/missions", content=b'{"command": "find \ud83d box", "planner": "baseline"}', headers=bad)
        assert r.status_code == 422 and "surrogate" in r.text
        assert c.get("/api/exec/state").json()["mission"] is None
        c.post("/api/exec/missions", json={"command": DEMO, "planner": "baseline"})
        r = c.post("/api/exec/mission/messages", content=b'{"text": "hi \ud83d there", "mode": "queue"}', headers=bad)
        assert r.status_code == 422 and "surrogate" in r.text
        s = c.get("/api/exec/state")
        assert s.status_code == 200 and s.json()["mission"]["operator_messages"] == []
        assert c.get("/api/exec/export").status_code == 200
