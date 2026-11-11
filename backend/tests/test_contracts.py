import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from hive_exec import catalog
from hive_exec.contracts import (
    AreaId,
    LocationId,
    ObjectLabel,
    PlanProposal,
    RobotId,
    RouteId,
    plan_proposal_json_schema,
)
from hive_exec.sim import fake_world

APPENDIX_A = {
    "schema_version": "hive.plan-proposal.v1", "disposition": "plan", "scope": "observation_stage",
    "summary": "Locate the red block and observe the pickup corridor.",
    "mission_goals": [{"kind": "object_at", "object_query": {"label": "red_block"}, "location_id": "dropoff_zone"}],
    "stage_goals": [{"kind": "object_located", "object_query": {"label": "red_block"}, "area_id": "work_area"},
                    {"kind": "route_observed", "route_id": "pickup_corridor"}],
    "tasks": [
        {"task_id": "t1", "binding_id": "observer", "action": {"kind": "inspect_area", "area_id": "work_area",
                                                               "object_query": {"label": "red_block"}},
         "depends_on": [], "preferred_robot_id": "camera_01"},
        {"task_id": "t2", "binding_id": "surveyor", "action": {"kind": "scan_route", "route_id": "pickup_corridor"},
         "depends_on": [], "preferred_robot_id": "lidar_01"}],
    "issues": [], "clarification_question": None,
}


def test_appendix_a_example_parses():
    p = PlanProposal.model_validate(APPENDIX_A)
    assert p.scope == "observation_stage" and len(p.tasks) == 2


def test_clarify_cannot_carry_tasks():
    bad = dict(APPENDIX_A, disposition="clarify", scope=None, clarification_question="Which?")
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)


def test_extra_fields_forbidden_and_unknown_ids_rejected():
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(dict(APPENDIX_A, approved=True))
    bad = json.loads(json.dumps(APPENDIX_A))
    bad["tasks"][0]["action"]["area_id"] = "kitchen"
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)
    bad = json.loads(json.dumps(APPENDIX_A))
    bad["tasks"][1]["action"] = {"kind": "launch_rocket"}
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)


def test_saved_schema_matches_models():
    saved = json.loads((Path(catalog.__file__).parent / "schemas" / "plan_proposal.v1.json").read_text())
    assert saved == json.loads(json.dumps(plan_proposal_json_schema(), sort_keys=True))


def test_vocabularies_match_catalog():
    assert set(typing.get_args(RobotId)) == set(catalog.ROBOTS)
    assert set(typing.get_args(LocationId)) == set(catalog.LOCATIONS)
    assert set(typing.get_args(AreaId)) == set(catalog.AREAS)
    assert set(typing.get_args(RouteId)) == set(catalog.ROUTES)
    assert set(typing.get_args(ObjectLabel)) == set(catalog.OBJECT_PROFILES)


def test_fake_world_fixture_matches_catalog():
    for rid, d in catalog.ROBOTS.items():
        assert fake_world.START_LOCATIONS[rid] == d.start_location
        assert fake_world.SKILLS[rid] == set(d.skills)
        assert fake_world.REACHABLE[rid] == set(d.reachable_locations)
    for kind, s in catalog.SKILLS.items():
        assert fake_world.DURATION_S[kind] == s.duration_s
        assert fake_world.ENERGY_PCT[kind] == s.energy_pct
