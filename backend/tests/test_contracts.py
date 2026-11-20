import json
import re
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
    # Object labels are open vocabulary: every profile label and alias fits the label pattern.
    assert typing.get_args(ObjectLabel)[0] is str
    for label, prof in catalog.OBJECT_PROFILES.items():
        for name in (label, *prof.aliases):
            assert re.fullmatch(catalog.LABEL_PATTERN, name), name


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




# -- open-vocabulary object labels ---------------------------------------------------


def _inspect(label):
    p = json.loads(json.dumps(APPENDIX_A))
    p["tasks"][0]["action"]["object_query"]["label"] = label
    p["mission_goals"][0]["object_query"]["label"] = label
    return p


def test_open_vocab_label_validates():
    p = PlanProposal.model_validate(_inspect("duck"))
    assert p.tasks[0].action.object_query.label == "duck"
    assert PlanProposal.model_validate(_inspect("mystery_crate")).tasks[0].action.object_query.label == "mystery_crate"


@pytest.mark.parametrize("bad", ["Duck!", "1duck", "", "a" * 33, "_duck", "red.block", 7])
def test_label_pattern_rejects_bad_labels(bad):
    # Case, spaces and hyphens are normalised first; anything else outside the pattern is rejected.
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(_inspect(bad))


@pytest.mark.parametrize("given,canonical", [
    ("red_box", "red_block"), ("red_cube", "red_block"), ("toy_car", "rc_car"), ("rc_truck", "rc_car"),
    ("rubber_duck", "duck"), ("Red Box", "red_block"), ("traffic-cone", "traffic_cone"), ("duck", "duck"),
    ("mystery_crate", "mystery_crate"), ("  Water  Bottle ", "water_bottle"),
])
def test_canonical_label(given, canonical):
    assert catalog.canonical_label(given) == canonical


def test_profile_for_uses_canonical_label():
    assert catalog.profile_for("toy_car").label == "rc_car"
    assert catalog.profile_for("mystery_crate") is None


def test_object_query_labels_are_canonicalized_in_goals_and_actions():
    p = PlanProposal.model_validate(_inspect("Red Box"))
    assert p.tasks[0].action.object_query.label == "red_block"
    assert p.mission_goals[0].object_query.label == "red_block"


def test_aliases_are_unique_and_never_shadow_a_profile():
    seen = {}
    for label, prof in catalog.OBJECT_PROFILES.items():
        assert prof.label == label
        for alias in prof.aliases:
            assert alias not in catalog.OBJECT_PROFILES, alias
            assert alias not in seen, (alias, label, seen.get(alias))
            seen[alias] = label


def test_object_profiles_cover_the_pool_and_are_synthetic():
    masses = {k: v.mass_kg for k, v in catalog.OBJECT_PROFILES.items()}
    assert masses == {"red_block": 0.10, "blue_block": 0.10, "green_block": 0.10, "duck": 0.05,
                      "tennis_ball": 0.06, "traffic_cone": 0.20, "rc_car": 1.20, "cardboard_box": 0.40,
                      "water_bottle": 0.50}
    assert all(p.synthetic for p in catalog.OBJECT_PROFILES.values())
    assert catalog.OBJECT_PROFILES["rc_car"].mass_kg > catalog.ROBOTS["gripper_01"].payload_limit_kg
    assert "rubber_duck" in catalog.catalog_dict()["object_profiles"]["duck"]["aliases"]


def test_object_id_accepts_ids_from_any_label():
    from pydantic import TypeAdapter

    from hive_exec.contracts import ObjectId
    ta = TypeAdapter(ObjectId)
    for oid in ("rc_car_1", "traffic_cone_2", "red_block_1", "mystery_crate_12", "a" * 32 + "_1"):
        assert ta.validate_python(oid) == oid
    for bad in ("rc_car", "_x_1", "RC_1", "a" * 33 + "_1"):
        with pytest.raises(ValidationError):
            ta.validate_python(bad)


def test_schema_label_is_a_string_with_a_pattern():
    schema = plan_proposal_json_schema()
    label = schema["$defs"]["ObjectQuery"]["properties"]["label"]
    assert label["type"] == "string" and label["pattern"] == catalog.LABEL_PATTERN and "enum" not in label


# -- fake world: fixed and random ---------------------------------------------------


def test_fake_world_profile_pool_matches_catalog():
    assert fake_world.PROFILE_MASS_KG == {k: v.mass_kg for k, v in catalog.OBJECT_PROFILES.items()}
    assert len(fake_world.UNKNOWN_MASS_KG) >= 2
    assert not set(fake_world.UNKNOWN_MASS_KG) & set(catalog.OBJECT_PROFILES)
    assert all(catalog.profile_for(lab) is None for lab in fake_world.UNKNOWN_MASS_KG)


def test_fixed_world_is_todays_world():
    w = fake_world.FakeWorld()
    assert w.kind == "fixed" and w.seed is None
    assert w.objects == {"red_block_1": {"label": "red_block", "location_id": "pickup_zone", "held_by": None,
                                         "mass_kg": 0.10}}
    assert w.obstacles == set()
    assert {r: s["location_id"] for r, s in w.robots.items()} == fake_world.START_LOCATIONS
    assert {s["battery_pct"] for s in w.robots.values()} == {80.0}


def test_random_world_same_seed_same_world():
    a, b = fake_world.FakeWorld(seed=42, world="random"), fake_world.FakeWorld(seed=42, world="random")
    assert a.snapshot() == b.snapshot() and a.seed == 42 and a.kind == "random"
    worlds = [fake_world.FakeWorld(seed=s, world="random").snapshot() for s in range(40)]
    assert len({json.dumps(w, sort_keys=True) for w in worlds}) > 30


def test_random_world_without_seed_picks_and_keeps_one():
    w = fake_world.FakeWorld(world="random")
    assert isinstance(w.seed, int)
    assert fake_world.FakeWorld(seed=w.seed, world="random").snapshot() == w.snapshot()


def test_random_world_stays_in_bounds_and_varies():
    pool = set(fake_world.PROFILE_MASS_KG) | set(fake_world.UNKNOWN_MASS_KG)
    counts, unknown_seen, blocked, gripper_starts, dupes, absent_red, work_area = set(), 0, 0, set(), 0, 0, 0
    for seed in range(400):
        w = fake_world.FakeWorld(seed=seed, world="random")
        labels = [o["label"] for o in w.objects.values()]
        counts.add(len(labels))
        for oid, o in w.objects.items():
            assert o["label"] in pool and o["held_by"] is None
            assert o["location_id"] in ("pickup_zone", "work_area")
            assert oid.rsplit("_", 1)[0] == o["label"]
            work_area += o["location_id"] == "work_area"
        unknown_seen += any(lab in fake_world.UNKNOWN_MASS_KG for lab in labels)
        dupes += len(labels) != len(set(labels))
        absent_red += "red_block" not in labels
        blocked += "pickup_corridor" in w.obstacles
        gripper_starts.add(w.robots["gripper_01"]["location_id"])
        for rid, r in w.robots.items():
            assert 35.0 <= r["battery_pct"] <= 100.0
            if rid != "gripper_01":
                assert r["location_id"] == fake_world.START_LOCATIONS[rid]
    assert counts == {1, 2, 3, 4, 5, 6}
    assert unknown_seen > 40 and dupes > 20 and absent_red > 40 and work_area > 100
    assert 60 < blocked < 140  # ~25 %
    assert gripper_starts == {"dropoff_zone", "pickup_zone"}


def test_random_world_distribution_over_2000_seeds():
    """Most operators try "find the red box", so a red block is common but not guaranteed."""
    pool = set(fake_world.PROFILE_MASS_KG) | set(fake_world.UNKNOWN_MASS_KG)
    n, red, reds2, unknown, blocked, seen = 2000, 0, 0, 0, 0, set()
    for seed in range(n):
        w = fake_world.FakeWorld(seed=seed, world="random")
        labels = [o["label"] for o in w.objects.values()]
        assert 1 <= len(labels) <= 6
        seen |= set(labels)
        red += "red_block" in labels
        reds2 += labels.count("red_block") >= 2
        unknown += any(lab in fake_world.UNKNOWN_MASS_KG for lab in labels)
        blocked += "pickup_corridor" in w.obstacles
    assert 0.45 <= red / n <= 0.60
    assert 0.05 <= reds2 / n <= 0.12
    assert unknown / n >= 0.30
    assert seen == pool
    assert 0.21 <= blocked / n <= 0.29


# Baseline (7db6669) fault effects on the fixed world, written out by hand so a change to
# FakeWorld cannot also change what the test expects.
_BASE_RED_1 = {"label": "red_block", "location_id": "pickup_zone", "held_by": None, "mass_kg": 0.10}


def _baseline_fixed(faults):
    objects = {"red_block_1": dict(_BASE_RED_1)}
    obstacles, robots = set(), {
        "camera_01": {"location_id": "camera_post", "battery_pct": 80.0, "carried": None, "health": "ok", "available": True},
        "lidar_01": {"location_id": "lidar_post", "battery_pct": 80.0, "carried": None, "health": "ok", "available": True},
        "gripper_01": {"location_id": "dropoff_zone", "battery_pct": 80.0, "carried": None, "health": "ok", "available": True},
    }
    for f in faults:
        if f == "route_blocked":
            obstacles.add("pickup_corridor")
        elif f == "block_missing":
            objects = {k: v for k, v in objects.items() if v["label"] != "red_block" or v["held_by"]}
        elif f == "two_red_blocks":
            objects["red_block_2"] = dict(_BASE_RED_1)
        elif f == "gripper_unavailable":
            robots["gripper_01"]["available"] = False
        elif f == "low_battery_gripper":
            robots["gripper_01"]["battery_pct"] = 24.0
    return {"objects": objects, "obstacles": sorted(obstacles), "robots": robots, "faults": sorted(set(faults))}


def test_fixed_world_faults_match_baseline_for_every_pair():
    names = sorted(fake_world.KNOWN_FAULTS)
    combos = [()] + [(a,) for a in names] + [(a, b) for a in names for b in names]
    for combo in combos:
        snap = fake_world.FakeWorld(list(combo)).snapshot()
        got = {k: snap[k] for k in ("objects", "obstacles", "robots", "faults")}
        assert got == _baseline_fixed(combo), combo
    assert fake_world.FakeWorld(["block_missing", "two_red_blocks"]).objects == {"red_block_2": _BASE_RED_1}
    assert fake_world.FakeWorld(["two_red_blocks", "block_missing"]).objects == {}


def test_random_world_skills_work_on_drawn_objects():
    seed = next(s for s in range(200) if len(fake_world.FakeWorld(seed=s, world="random").objects) >= 2)
    w = fake_world.FakeWorld(seed=seed, world="random")
    label = next(iter(w.objects.values()))["label"]
    out = w.execute("camera_01", "inspect_area", {"area_id": "work_area", "object_query": {"label": label}})
    assert out.status == "succeeded" and out.result["found"]


def test_faults_layer_on_a_random_world():
    seed = next(s for s in range(200) if not any(
        o["label"] == "red_block" for o in fake_world.FakeWorld(seed=s, world="random").objects.values()))
    w = fake_world.FakeWorld(["two_red_blocks", "route_blocked", "low_battery_gripper"], seed=seed, world="random")
    reds = [o for o in w.objects.values() if o["label"] == "red_block" and o["held_by"] is None]
    assert len(reds) == 2 and "pickup_corridor" in w.obstacles and w.robots["gripper_01"]["battery_pct"] == 24.0
    many = next(s for s in range(400) if sum(
        o["label"] == "red_block" for o in fake_world.FakeWorld(seed=s, world="random").objects.values()) >= 2)
    before = fake_world.FakeWorld(seed=many, world="random").objects
    after = fake_world.FakeWorld(["two_red_blocks"], seed=many, world="random").objects
    assert after == before  # already ambiguous: nothing added
    w2 = fake_world.FakeWorld(["block_missing"], seed=many, world="random")
    assert not any(o["label"] == "red_block" for o in w2.objects.values())


def test_truth_snapshot_exposes_seed_and_world():
    snap = fake_world.FakeWorld(["route_blocked"], seed=7, world="random").snapshot()
    assert snap["world"] == "random" and snap["seed"] == 7
    assert snap["corridor"] == {"pickup_corridor": "blocked"}
    assert set(snap["robots"]["gripper_01"]) >= {"location_id", "battery_pct"}
    assert fake_world.FakeWorld().snapshot()["corridor"] == {"pickup_corridor": "clear"}


@pytest.mark.parametrize("args", [{"world": "chaos"}, {"seed": "7", "world": "random"},
                                  {"seed": True, "world": "random"}, {"seed": -1, "world": "random"}])
def test_world_from_args_rejects_bad_input(args):
    with pytest.raises(ValueError):
        fake_world.world_from_args(args)


def test_world_from_args_defaults_to_fixed():
    assert fake_world.world_from_args({}).kind == "fixed"
    assert fake_world.world_from_args({"world": None, "seed": None}).kind == "fixed"
    w = fake_world.world_from_args({"faults": ["route_blocked"], "world": "random", "seed": 3})
    assert w.kind == "random" and w.seed == 3 and "route_blocked" in w.faults


# ------------------------------------------------------------ WS2: answer + clarification options

ANSWER = {"schema_version": "hive.plan-proposal.v1", "disposition": "answer", "summary": "Gripper car abilities.",
          "answer": "The gripper car can pick, place and drive to the pickup and drop-off zones."}
CLARIFY = {"schema_version": "hive.plan-proposal.v1", "disposition": "clarify", "summary": "Destination unclear.",
           "clarification_question": "Where should it go?"}


def test_answer_disposition_parses():
    p = PlanProposal.model_validate(ANSWER)
    assert p.disposition == "answer" and p.answer.startswith("The gripper car") and p.scope is None


@pytest.mark.parametrize("bad", [
    {k: v for k, v in ANSWER.items() if k != "answer"},                  # answer missing
    dict(ANSWER, answer=""),                                              # empty answer
    dict(ANSWER, answer="x" * 601),                                       # too long
    dict(ANSWER, scope="full"),                                           # scope must be null
    dict(ANSWER, tasks=APPENDIX_A["tasks"]),                              # no tasks
    dict(ANSWER, mission_goals=APPENDIX_A["mission_goals"]),              # no goals
    dict(ANSWER, stage_goals=APPENDIX_A["stage_goals"]),
    dict(ANSWER, clarification_question="Which?"),
    dict(APPENDIX_A, answer="not for plans"),                             # answer only with answer
    dict(CLARIFY, answer="not for clarify"),
])
def test_answer_rules(bad):
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)


def test_answer_max_length_is_600():
    assert PlanProposal.model_validate(dict(ANSWER, answer="x" * 600)).answer == "x" * 600


def test_clarification_options_drop_case_insensitive_repeats_keeping_the_first():
    p = PlanProposal.model_validate(dict(CLARIFY, clarification_options=[
        "The red block", "the red block", "duck", " Duck", "THE RED BLOCK"]))
    assert p.clarification_options == ["The red block", "duck"]


def test_clarification_options():
    p = PlanProposal.model_validate(dict(CLARIFY, clarification_options=["the drop-off zone", "the pickup zone"]))
    assert p.clarification_options == ["the drop-off zone", "the pickup zone"]
    assert PlanProposal.model_validate(CLARIFY).clarification_options == []
    assert PlanProposal.model_validate(dict(CLARIFY, clarification_options=["o" * 60] * 4))


@pytest.mark.parametrize("bad", [
    dict(CLARIFY, clarification_options=["a", "b", "c", "d", "e"]),       # more than 4
    dict(CLARIFY, clarification_options=["o" * 61]),                      # longer than 60
    dict(CLARIFY, clarification_options=[""]),                            # empty option
    dict(APPENDIX_A, clarification_options=["red_block_1"]),              # only with clarify
    dict(ANSWER, clarification_options=["yes"]),
])
def test_clarification_options_rules(bad):
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)


def test_schema_offers_answer_and_options():
    schema = plan_proposal_json_schema()
    assert "answer" in schema["properties"]["disposition"]["enum"]
    assert "answer" in schema["properties"] and "clarification_options" in schema["properties"]


# ================================================== WS3: plan rationale and sensors

def _plan_with(**kw):
    base = json.loads(json.dumps(APPENDIX_A))
    base.update(kw)
    return base


def test_rationale_is_optional_and_defaults_to_empty():
    assert "rationale" not in APPENDIX_A
    assert PlanProposal.model_validate(APPENDIX_A).rationale == []


def test_rationale_accepts_up_to_four_items_of_200_chars():
    items = ["x" * 200, "b", "c", "d"]
    assert PlanProposal.model_validate(_plan_with(rationale=items)).rationale == items


@pytest.mark.parametrize("rationale", [["a", "b", "c", "d", "e"], ["x" * 201], [1], "one string"])
def test_rationale_limits_are_enforced(rationale):
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(_plan_with(rationale=rationale))


def test_rationale_is_an_optional_array_of_strings_in_the_schema():
    schema = plan_proposal_json_schema()
    prop = schema["properties"]["rationale"]
    assert prop["type"] == "array" and prop["maxItems"] == 4
    assert prop["items"] == {"type": "string", "maxLength": 200}
    assert "rationale" not in schema["required"]


def test_robot_descriptors_state_their_sensors_and_localization():
    r = catalog.ROBOTS
    assert r["camera_01"].sensors == ("rgb_camera",) and "camera_post" in r["camera_01"].localization
    assert "work_area" in r["camera_01"].localization and "pickup_zone" in r["camera_01"].localization
    assert r["lidar_01"].sensors == ("2d_lidar",) and "lidar_post" in r["lidar_01"].localization
    assert "pickup_corridor" in r["lidar_01"].localization
    g = r["gripper_01"]
    assert g.sensors == ()
    for phrase in ["no onboard camera or LiDAR", "dead reckoning", "cannot detect obstacles",
                   "confirm its position itself", "synthetic"]:
        assert phrase in g.localization, phrase
    for d in r.values():
        assert "synthetic" in d.localization


def test_sensors_and_localization_reach_as_dict_and_catalog_dict():
    d = catalog.ROBOTS["gripper_01"].as_dict()
    assert d["sensors"] == [] and d["localization"] == catalog.ROBOTS["gripper_01"].localization
    by_id = {x["robot_id"]: x for x in catalog.catalog_dict()["robots"]}
    assert by_id["camera_01"]["sensors"] == ["rgb_camera"] and by_id["lidar_01"]["sensors"] == ["2d_lidar"]
    assert by_id["lidar_01"]["localization"]


# ================================================== WS4: next-stage preview

PREVIEW_TASKS = [
    {"task_id": "t1", "binding_id": "carrier", "depends_on": [],
     "action": {"kind": "navigate_to", "location_id": "pickup_zone", "route_id": "pickup_corridor"}},
    {"task_id": "t2", "binding_id": "carrier", "depends_on": ["t1"],
     "action": {"kind": "pick_object", "object_query": {"label": "red_block"}}},
]
PREVIEW = {"summary": "Carry the red block to the drop-off zone.", "tasks": PREVIEW_TASKS,
           "waits_for": ["camera_01 sighting of red_block", "lidar_01 scan of pickup_corridor"]}


def _staged(**kw):
    return _plan_with(next_stage_preview=json.loads(json.dumps(PREVIEW)), **kw)


def test_preview_is_optional_and_defaults_to_none():
    p = PlanProposal.model_validate(APPENDIX_A)
    assert p.next_stage_preview is None and p.preview_changes == []


def test_observation_stage_plan_accepts_a_preview_with_object_queries():
    p = PlanProposal.model_validate(_staged())
    pv = p.next_stage_preview
    assert pv.summary.startswith("Carry") and [t.task_id for t in pv.tasks] == ["t1", "t2"]
    assert pv.tasks[1].action.object_query.label == "red_block" and len(pv.waits_for) == 2


@pytest.mark.parametrize("override", [
    {"scope": "full"},
    {"disposition": "clarify", "scope": None, "tasks": [], "mission_goals": [], "stage_goals": [],
     "clarification_question": "Which?"},
    {"disposition": "reject", "scope": None, "tasks": [], "mission_goals": [], "stage_goals": []},
    {"disposition": "answer", "scope": None, "tasks": [], "mission_goals": [], "stage_goals": [], "answer": "Hi."},
])
def test_preview_only_with_a_plan_in_observation_stage(override):
    with pytest.raises(ValidationError, match="next_stage_preview"):
        PlanProposal.model_validate(_staged(**override))


def test_preview_limits_are_enforced():
    def with_preview(**kw):
        return _plan_with(next_stage_preview={**PREVIEW, **kw})

    with pytest.raises(ValidationError):
        PlanProposal.model_validate(with_preview(waits_for=["a", "b", "c", "d", "e"]))
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(with_preview(waits_for=["x" * 161]))
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(with_preview(waits_for=[""]))
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(with_preview(summary=""))
    PlanProposal.model_validate(with_preview(waits_for=["x" * 160, "b", "c", "d"]))
    many = [{"task_id": f"t{i}", "binding_id": "b", "depends_on": [],
             "action": {"kind": "capture_image", "area_id": "work_area"}} for i in range(1, 18)]
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(with_preview(tasks=many))
    with pytest.raises(ValidationError):  # unknown keys are refused like everywhere else
        PlanProposal.model_validate(with_preview(allocated=True))


def test_preview_tasks_must_have_unique_ids_and_known_dependencies():
    dup = [PREVIEW_TASKS[0], dict(PREVIEW_TASKS[1], task_id="t1", depends_on=[])]
    with pytest.raises(ValidationError, match="unique"):
        PlanProposal.model_validate(_plan_with(next_stage_preview={**PREVIEW, "tasks": dup}))
    bad = [PREVIEW_TASKS[0], dict(PREVIEW_TASKS[1], depends_on=["t9"])]
    with pytest.raises(ValidationError, match="t9"):
        PlanProposal.model_validate(_plan_with(next_stage_preview={**PREVIEW, "tasks": bad}))


def test_preview_task_actions_are_validated_like_real_ones():
    bad = [dict(PREVIEW_TASKS[0], action={"kind": "navigate_to", "location_id": "kitchen"})]
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(_plan_with(next_stage_preview={**PREVIEW, "tasks": bad}))


def test_a_preview_without_tasks_is_dropped_not_an_error():
    p = PlanProposal.model_validate(_plan_with(next_stage_preview={**PREVIEW, "tasks": []}))
    assert p.next_stage_preview is None
    p = PlanProposal.model_validate_json(json.dumps(_plan_with(next_stage_preview=None)))
    assert p.next_stage_preview is None


def test_preview_does_not_make_the_preview_tasks_proposal_tasks():
    p = PlanProposal.model_validate(_staged())
    assert [t.task_id for t in p.tasks] == ["t1", "t2"]  # the plan's own two sensing tasks
    assert p.tasks[0].action.kind == "inspect_area" and p.next_stage_preview.tasks[0].action.kind == "navigate_to"


def test_preview_changes_limits_and_where_they_are_allowed():
    ok = ["Object is at pickup_zone, not work_area.", "b", "c", "d"]
    assert PlanProposal.model_validate(_plan_with(preview_changes=ok)).preview_changes == ok
    for bad in (["a", "b", "c", "d", "e"], ["x" * 161], [1], "one"):
        with pytest.raises(ValidationError):
            PlanProposal.model_validate(_plan_with(preview_changes=bad))
    with pytest.raises(ValidationError, match="preview_changes"):
        PlanProposal.model_validate(_plan_with(disposition="reject", scope=None, tasks=[], mission_goals=[],
                                               stage_goals=[], preview_changes=["x"]))
    # allowed on a full plan too (a continuation that ends the staging explains what changed)
    PlanProposal.model_validate(_plan_with(scope="full", preview_changes=["Carry goes ahead as previewed."]))


def test_schema_has_an_optional_nested_preview_and_optional_changes():
    schema = plan_proposal_json_schema()
    assert "next_stage_preview" not in schema["required"] and "preview_changes" not in schema["required"]
    prop = schema["properties"]["next_stage_preview"]
    refs = [o.get("$ref") for o in prop["anyOf"]]
    assert "#/$defs/NextStagePreview" in refs and {"type": "null"} in prop["anyOf"]
    defn = schema["$defs"]["NextStagePreview"]
    assert defn["properties"]["tasks"]["maxItems"] == catalog_max_tasks()
    assert defn["properties"]["waits_for"]["maxItems"] == 4
    assert defn["properties"]["waits_for"]["items"]["maxLength"] == 160
    assert "summary" in defn["required"] and "tasks" not in defn["required"]
    pc = schema["properties"]["preview_changes"]
    assert pc["maxItems"] == 4 and pc["items"]["maxLength"] == 160


def catalog_max_tasks():
    from hive_exec.contracts import MAX_TASKS
    return MAX_TASKS


def test_execution_plan_and_report_defaults_for_the_preview_fields():
    from hive_exec.contracts import ExecutionPlan, ValidationReport

    assert ValidationReport(ok=True, world_version=1).preview_findings == []
    assert ExecutionPlan.model_fields["preview_diff"].default is None
