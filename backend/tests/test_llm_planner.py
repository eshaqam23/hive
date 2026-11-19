"""LLM planner wiring against a SCRIPTED mock endpoint (not a live LLM).

Moved from test_qwen_planner.py; the original assertions are kept, with the
planner kind now "llm" ("qwen" stays accepted as an alias)."""

import json

from .conftest import DEMO, approve_latest, wait_for
from .llm_mock import MOCK_MODEL, scripted_planner

OBS = {
    "schema_version": "hive.plan-proposal.v1", "disposition": "plan", "scope": "observation_stage",
    "summary": "Locate the red block and observe the pickup corridor.",
    "mission_goals": [{"kind": "object_at", "object_query": {"label": "red_block"}, "location_id": "dropoff_zone"}],
    "stage_goals": [], "issues": [], "clarification_question": None,
    "tasks": [
        {"task_id": "t1", "binding_id": "observer", "action": {"kind": "inspect_area", "area_id": "work_area",
                                                               "object_query": {"label": "red_block"}},
         "depends_on": [], "preferred_robot_id": "camera_01"},
        {"task_id": "t2", "binding_id": "surveyor", "action": {"kind": "scan_route", "route_id": "pickup_corridor"},
         "depends_on": [], "preferred_robot_id": "lidar_01"}],
}


def carry(**over):
    nav = lambda loc, deps: {"kind": "navigate_to", "location_id": loc, "route_id": "pickup_corridor"}
    p = dict(OBS, scope="full", summary="Carry it.", tasks=[
        {"task_id": "t1", "binding_id": "carrier", "action": nav("pickup_zone", []), "depends_on": [],
         "preferred_robot_id": "gripper_01"},
        {"task_id": "t2", "binding_id": "carrier", "action": {"kind": "pick_object", "object_id": "red_block_1"},
         "depends_on": ["t1"], "preferred_robot_id": "gripper_01"},
        {"task_id": "t3", "binding_id": "carrier", "action": nav("dropoff_zone", []), "depends_on": ["t2"],
         "preferred_robot_id": "gripper_01"},
        {"task_id": "t4", "binding_id": "carrier", "action": {"kind": "place_object", "object_id": "red_block_1",
                                                              "location_id": "dropoff_zone"},
         "depends_on": ["t3"], "preferred_robot_id": "gripper_01"}])
    p.update(over)
    return p


async def test_request_shape_non_thinking_structured(make_exec):
    planner, mock = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    body = mock.requests[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["structured_outputs"]["json"] == planner.schema
    assert body["seed"] == 1234 and body["model"] == MOCK_MODEL
    rec = ex.mission.revisions[-1].planner
    assert rec.kind == "llm" and rec.model == MOCK_MODEL and "mock" in rec.label
    assert rec.first_pass_ok and not rec.repaired


async def test_malformed_output_gets_one_repair(make_exec):
    planner, mock = scripted_planner(['{"disposition": "plan"', json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert rec.repaired and rec.attempts == 2 and rec.first_pass_ok is False
    assert "PREVIOUS OUTPUT WAS REJECTED" in mock.requests[-1]["messages"][-1]["content"]


async def test_two_bad_outputs_surface_planner_error(make_exec):
    planner, _ = scripted_planner(["not json", "still not json"])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert not ex.mission.revisions


async def test_validation_failure_triggers_repair(make_exec):
    # First output transports without evidence (invalid); repair returns the observation stage.
    planner, mock = scripted_planner([json.dumps(carry()), json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rev = ex.mission.revisions[-1]
    assert rev.validation.ok and rev.proposal.scope == "observation_stage"
    assert rev.planner.repaired
    assert "route_not_observed_clear" in mock.requests[-1]["messages"][-1]["content"] or \
        "unknown_object" in mock.requests[-1]["messages"][-1]["content"]


async def test_full_two_stage_flow_with_mock(make_exec):
    planner, _ = scripted_planner([json.dumps(OBS), json.dumps(carry())])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"awaiting_approval", "blocked", "completed", "planner_error"})
    await approve_latest(ex)
    assert await wait_for(ex, {"completed", "blocked"}) == "completed"


async def test_unconfigured_endpoint_is_visible_not_substituted(make_exec):
    from hive_exec.config import QwenSettings
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    ex = await make_exec(planners={"llm": LLMPlanner(Router(legacy=QwenSettings(base_url="")))})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert "not configured" in ex.mission.outcome["reason"]
    assert ex.mission.planner_kind == "llm" and not ex.mission.revisions


# ---------------------------------------------------------------- prompt 4: provider store + router
import httpx  # noqa: E402

from .llm_mock import MOCK_ID, ScriptedQwen, mock_row, save_chain  # noqa: E402

FAKE_KEY = "sk-test-NOT-A-REAL-KEY-9f3k"


def _planner(handler):
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    return LLMPlanner(Router(transport=httpx.MockTransport(handler)))


async def test_record_carries_provider_fields(make_exec):
    planner, _ = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert rec.provider_id == MOCK_ID and rec.provider_kind == "openai_compatible"
    assert rec.fallback_used is False and rec.failovers == []
    assert rec.provider_latency_ms is not None and rec.provider_latency_ms >= 0
    assert rec.usage == {"prompt_tokens": 100, "completion_tokens": 50}
    assert rec.endpoint == "http://mock.invalid/v1"
    assert rec.settings["structured_mode"] == "structured_outputs"
    assert rec.settings["provider_kind"] == "openai_compatible"


async def test_failover_on_timeout_is_recorded(make_exec):
    mock = ScriptedQwen([json.dumps(OBS)])
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "down.invalid":
            raise httpx.ConnectTimeout("timed out", request=request)
        return mock.handler(request)

    save_chain([mock_row("down-first", host="down.invalid"), mock_row()])
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert hosts == ["down.invalid", "mock.invalid"]
    assert rec.fallback_used is True and rec.provider_id == MOCK_ID
    assert len(rec.failovers) == 1 and rec.failovers[0].startswith("down-first: ConnectTimeout")


async def test_schema_failure_is_repaired_not_failed_over(make_exec):
    mock = ScriptedQwen(["not json", "still not json"])
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "second.invalid":
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(OBS)}}]})
        return mock.handler(request)

    save_chain([mock_row(), mock_row("second", host="second.invalid")])
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    assert hosts == ["mock.invalid", "mock.invalid"]  # first pass + the Executive's one repair
    assert ex.mission.status == "planner_error" and not ex.mission.revisions
    assert "PREVIOUS OUTPUT WAS REJECTED" in mock.requests[-1]["messages"][-1]["content"]


async def test_every_provider_down_is_planner_error_with_attempts(make_exec):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    save_chain([mock_row("a", host="a.invalid"), mock_row("b", host="b.invalid")])
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert "every provider for role 'executive' failed" in ex.mission.outcome["reason"]
    rec = ex.mission.planning_log[-1]["planner"]
    assert rec["provider_id"] == "b" and len(rec["failovers"]) == 2


async def test_provider_401_echoing_the_key_is_redacted(make_exec):
    auth = []

    def handler(request):
        auth.append(request.headers.get("authorization"))
        if request.url.host == "second.invalid":
            raise AssertionError("a 401 must not fail over")
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {FAKE_KEY}"}})

    save_chain([mock_row(), mock_row("second", host="second.invalid")], keys={MOCK_ID: FAKE_KEY})
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    assert auth == [f"Bearer {FAKE_KEY}"]  # the key reached the client through its env var
    assert ex.mission.status == "planner_error"
    reason = ex.mission.outcome["reason"]
    assert "HTTP 401" in reason and "Incorrect API key provided" in reason and "…9f3k" in reason
    assert FAKE_KEY not in json.dumps(ex.state(), default=str)
    assert FAKE_KEY not in repr(ex.planners["llm"].status())


async def test_qwen_alias_is_accepted_in_state(make_exec):
    from hive_exec.contracts import PlannerRecord

    assert PlannerRecord(kind="qwen", label="old run").kind == "llm"
    planner, _ = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"qwen": planner})  # old key and default normalize to "llm"
    assert set(ex.planners) == {"llm"} and ex.default_planner == "llm"
    await ex.create_mission(DEMO, "qwen")
    assert ex.mission.planner_kind == "llm"
    assert ex.mission.revisions[-1].planner.kind == "llm"


async def test_status_reports_provider_chain():
    planner, _ = scripted_planner([])
    save_chain([mock_row(), mock_row("backup", host="backup.invalid")])
    s = planner.status()
    assert s["kind"] == "llm" and s["configured"] is True and s["role"] == "executive"
    assert s["provider"] == MOCK_ID and s["model"] == MOCK_MODEL and s["fallbacks"] == ["backup"]
    assert s["live_enabled"] is False and s["probe"] is None


async def test_status_unconfigured_is_explicit():
    from hive_exec.config import QwenSettings
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    s = LLMPlanner(Router(legacy=QwenSettings(base_url=""))).status()
    assert s["kind"] == "llm" and s["configured"] is False and s["provider"] is None
    assert any("not configured" in p for p in s["problems"])


async def test_baseline_is_labeled_no_llm(make_exec):
    ex = await make_exec("known")
    await ex.create_mission(DEMO)
    assert ex.mission.revisions[-1].planner.label == "Baseline (no LLM)"


async def test_probe_runs_against_the_primary_and_enables_live():
    planner, mock = scripted_planner([])
    assert planner.status()["live_enabled"] is False
    result = await planner.probe()
    assert result["ok"] is True and result["provider_id"] == MOCK_ID
    s = planner.status()
    assert s["live_enabled"] is True and s["probe"]["schema_roundtrip"]["ok"] is True
    assert mock.contents == []  # the probe never consumed a scripted planning answer


async def test_probe_unconfigured_is_a_clean_result():
    from hive_exec.config import QwenSettings
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    result = await LLMPlanner(Router(legacy=QwenSettings(base_url=""))).probe()
    assert result["ok"] is False and "not configured" in result["error"]


async def test_propose_does_not_wait_for_a_probe(make_exec):
    planner, mock = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "awaiting_approval" and len(mock.requests) == 1


def test_catalog_text_presents_labels_as_open_vocabulary():
    from hive_exec.catalog import OBJECT_PROFILES
    from hive_exec.planner.prompt import RULES, _catalog_text, system_prompt

    text = _catalog_text()
    assert "OBJECT LABELS: red_block, blue_block, green_block" not in text
    assert "open vocabulary" in text and "lowercase snake_case" in text
    assert "objects with a trusted profile (mass known)" in text
    assert "can be found but not carried" in text
    for label, prof in OBJECT_PROFILES.items():
        assert f"{label} ({prof.mass_kg} kg" in text
    assert "rubber_duck" in text  # aliases are listed so the model can map synonyms
    assert RULES in system_prompt()
