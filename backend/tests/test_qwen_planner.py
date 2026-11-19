"""Qwen planner wiring against a SCRIPTED mock endpoint (not live Qwen)."""

import json

from hive_exec.contracts import plan_proposal_json_schema

from .conftest import DEMO, approve_latest, wait_for
from .qwen_mock import MOCK_MODEL, scripted_planner

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
    ex = await make_exec(planners={"qwen": planner})
    await ex.create_mission(DEMO)
    body = mock.requests[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["structured_outputs"]["json"] == plan_proposal_json_schema()
    assert body["seed"] == 1234 and body["model"] == MOCK_MODEL
    rec = ex.mission.revisions[-1].planner
    assert rec.kind == "qwen" and rec.model == MOCK_MODEL and "mock" in rec.label
    assert rec.first_pass_ok and not rec.repaired


async def test_malformed_output_gets_one_repair(make_exec):
    planner, mock = scripted_planner(['{"disposition": "plan"', json.dumps(OBS)])
    ex = await make_exec(planners={"qwen": planner})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert rec.repaired and rec.attempts == 2 and rec.first_pass_ok is False
    assert "PREVIOUS OUTPUT WAS REJECTED" in mock.requests[-1]["messages"][-1]["content"]


async def test_two_bad_outputs_surface_planner_error(make_exec):
    planner, _ = scripted_planner(["not json", "still not json"])
    ex = await make_exec(planners={"qwen": planner})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert not ex.mission.revisions


async def test_validation_failure_triggers_repair(make_exec):
    # First output transports without evidence (invalid); repair returns the observation stage.
    planner, mock = scripted_planner([json.dumps(carry()), json.dumps(OBS)])
    ex = await make_exec(planners={"qwen": planner})
    await ex.create_mission(DEMO)
    rev = ex.mission.revisions[-1]
    assert rev.validation.ok and rev.proposal.scope == "observation_stage"
    assert rev.planner.repaired
    assert "route_not_observed_clear" in mock.requests[-1]["messages"][-1]["content"] or \
        "unknown_object" in mock.requests[-1]["messages"][-1]["content"]


async def test_full_two_stage_flow_with_mock(make_exec):
    planner, _ = scripted_planner([json.dumps(OBS), json.dumps(carry())])
    ex = await make_exec(planners={"qwen": planner})
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"awaiting_approval", "blocked", "completed", "planner_error"})
    await approve_latest(ex)
    assert await wait_for(ex, {"completed", "blocked"}) == "completed"


async def test_unconfigured_endpoint_is_visible_not_substituted(make_exec):
    from hive_exec.config import QwenSettings
    from hive_exec.planner.qwen import QwenPlanner

    ex = await make_exec(planners={"qwen": QwenPlanner(QwenSettings(base_url=""))})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert "not configured" in ex.mission.outcome["reason"]
    assert ex.mission.planner_kind == "qwen" and not ex.mission.revisions
