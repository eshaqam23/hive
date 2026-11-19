import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from hive_exec import catalog
from hive_exec.agent_commands import describe_command, envelope_json
from hive_exec.contracts import (
    AreaId,
    LocationId,
    ObjectLabel,
    PlanProposal,
    RobotId,
    RouteId,
    TaskEnvelope,
    plan_proposal_json_schema,
    task_envelope_json_schema,
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


# -- agent commands: byte parity with the Synapse bridge JSON.stringify ---------------
# Reference outputs from Node 24: JSON.stringify(x) for each value.
JS_NUMBERS = [
    (120.0, "120"), (0.1, "0.1"), (1e21, "1e+21"), (1e-7, "1e-7"), (1.2345678901234568e20, "123456789012345680000"),
    (0.000001, "0.000001"), (1.5e-10, "1.5e-10"), (-0.0, "0"), (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"), (123.456, "123.456"), (-42.5, "-42.5"),
    (1e20, "100000000000000000000"), (100, "100"), (2.5e-7, "2.5e-7"), (0.00001234, "0.00001234"),
    (float("nan"), "null"),
]


@pytest.mark.parametrize("value,expected", JS_NUMBERS)
def test_numbers_match_js(value, expected):
    assert envelope_json(value) == expected


def test_objects_match_js_stringify():
    obj = {"b": 1, "10": 2, "2": 3, "01": 4,
           "s": "a\"b\\c\n\t\u0001\u001f\u007f é\U0001F600", "arr": [True, False, None, {"z": 1.0}]}
    # Node: JSON.stringify(obj); array-index keys come first, non-ASCII stays raw.
    expected = ('{"2":3,"10":2,"b":1,"01":4,"s":"a\\"b\\\\c\\n\\t\\u0001\\u001f\u007f é\U0001F600",'
                '"arr":[true,false,null,{"z":1}]}')
    assert envelope_json(obj) == expected


def test_envelope_json_round_trips_and_drops_float_suffix():
    env = TaskEnvelope(run_id="run-1", mission_id="m1", revision=1, task_id="t1", attempt_id="1.t1.a1-abcd",
                       robot_id="camera_01", skill="inspect_area",
                       params={"area_id": "work_area", "object_query": {"label": "red_block"}}, timeout_s=60.0)
    s = envelope_json(env.model_dump())
    assert '"timeout_s":60}' in s and " " not in s.replace("red_block", "")
    assert json.loads(s) == json.loads(env.model_dump_json())
    assert list(json.loads(s)) == list(env.model_dump())  # field order preserved


def test_describe_command():
    assert describe_command("inspect_area", {"area_id": "work_area", "object_query": {"label": "red_block"}}) == \
        'inspect_area(area_id="work_area", object_query="red_block")'
    assert describe_command("navigate_to", {"location_id": "pickup_zone", "route_id": None}) == \
        'navigate_to(location_id="pickup_zone")'
    assert describe_command("x", {"n": 2.0, "f": True, "o": {"a": 1}}) == 'x(n=2, f=true, o={"a":1})'


def test_saved_task_envelope_schema_matches_model():
    saved = json.loads((Path(catalog.__file__).parent / "schemas" / "task_envelope.v1.json").read_text())
    assert saved == json.loads(json.dumps(task_envelope_json_schema(), sort_keys=True))
    assert saved["properties"]["schema_version"]["const"] == "hive.task-envelope.v1"


