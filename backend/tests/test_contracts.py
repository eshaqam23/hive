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
