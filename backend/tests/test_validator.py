import pytest

from hive_exec.allocator import CapabilityAllocator
from hive_exec.contracts import Observation, PlanProposal
from hive_exec.validator import validate
from hive_exec.world import ObservedWorld, initial_robot_states


def _proposal(tasks, scope="full", goals=None):
    return PlanProposal.model_validate({
        "schema_version": "hive.plan-proposal.v1", "disposition": "plan", "scope": scope, "summary": "test",
        "mission_goals": goals or [{"kind": "object_at", "object_query": {"label": "red_block"},
                                    "location_id": "dropoff_zone"}],
        "stage_goals": [], "tasks": tasks, "issues": [], "clarification_question": None})


def _t(tid, binding, action, deps=(), robot=None):
    return {"task_id": tid, "binding_id": binding, "action": action, "depends_on": list(deps),
            "preferred_robot_id": robot}


NAV_PICKUP = {"kind": "navigate_to", "location_id": "pickup_zone", "route_id": "pickup_corridor"}
NAV_DROP = {"kind": "navigate_to", "location_id": "dropoff_zone", "route_id": "pickup_corridor"}
PICK = {"kind": "pick_object", "object_id": "red_block_1"}
PLACE = {"kind": "place_object", "object_id": "red_block_1", "location_id": "dropoff_zone"}
CARRY = [_t("t1", "carrier", NAV_PICKUP), _t("t2", "carrier", PICK, ["t1"]),
         _t("t3", "carrier", NAV_DROP, ["t2"]), _t("t4", "carrier", PLACE, ["t3"])]


def _known_world(route="route_clear"):
    w = ObservedWorld()
    w.accept(Observation(kind="object_seen", entity_id="red_block_1", label="red_block",
                         location_id="pickup_zone", area_id="work_area"), "camera_01")
    w.accept(Observation(kind=route, entity_id="pickup_corridor", route_id="pickup_corridor"), "lidar_01")
    return w


def run(p, world=None, robots=None, constraints=None):
    robots = robots or initial_robot_states()
    world = world or _known_world()
    alloc = CapabilityAllocator().allocate(p, robots, constraints or {})
    return validate(p, alloc, robots, world, None, constraints or {}), alloc


def codes(res):
    return {f.code for f in res.report.findings if f.severity == "error"}


def test_valid_carrier_chain_allocates_one_robot():
    res, alloc = run(_proposal(CARRY))
    assert res.report.ok, res.report.findings
    assert alloc.robots == {"carrier": "gripper_01"}
    assert {t.robot_id for t in res.tasks} == {"gripper_01"}


def test_invalid_capability_assignment():
    res, _ = run(_proposal(CARRY), constraints={"carrier": "camera_01"})
    assert "unsupported_assignment" in codes(res)


def test_unknown_object_id():
    tasks = [_t("t1", "carrier", NAV_PICKUP), _t("t2", "carrier", {"kind": "pick_object", "object_id": "red_block_9"}, ["t1"])]
    res, _ = run(_proposal(tasks))
    assert "unknown_object" in codes(res)


def test_dependency_cycle_and_missing():
    tasks = [_t("t1", "a", {"kind": "scan_route", "route_id": "pickup_corridor"}, ["t2"]),
             _t("t2", "a", {"kind": "scan_route", "route_id": "pickup_corridor"}, ["t1"]),
             _t("t3", "b", {"kind": "capture_image", "area_id": "work_area"}, ["t9"])]
    res, _ = run(_proposal(tasks, goals=[{"kind": "route_observed", "route_id": "pickup_corridor"}]))
    assert {"dependency_cycle", "missing_dependency"} <= codes(res)


def test_possession_order():
    tasks = [_t("t1", "carrier", NAV_PICKUP), _t("t2", "carrier", PLACE, ["t1"])]
    res, _ = run(_proposal(tasks))
    assert "possession_order" in codes(res)


def test_unexecuted_scan_cannot_establish_clearance():
    w = ObservedWorld()
    w.accept(Observation(kind="object_seen", entity_id="red_block_1", label="red_block",
                         location_id="pickup_zone", area_id="work_area"), "camera_01")
    tasks = [_t("t0", "surveyor", {"kind": "scan_route", "route_id": "pickup_corridor"})] + [
        dict(t, depends_on=t["depends_on"] or ["t0"]) for t in CARRY]
    res, _ = run(_proposal(tasks), world=w)
    assert "route_not_observed_clear" in codes(res)


def test_blocked_route():
    res, _ = run(_proposal(CARRY), world=_known_world("route_blocked"))
    assert "route_blocked" in codes(res)


def test_low_battery():
    robots = initial_robot_states()
    robots["gripper_01"].battery_pct = 25.0
    res, _ = run(_proposal(CARRY), robots=robots)
    assert "insufficient_battery" in codes(res)


def test_unavailable_robot_has_no_eligible_alternative():
    robots = initial_robot_states()
    robots["gripper_01"].available = False
    res, alloc = run(_proposal(CARRY), robots=robots)
    assert alloc.robots["carrier"] is None
    assert "no_eligible_robot" in codes(res)


def test_resource_conflict_same_robot_unordered():
    tasks = [_t("t1", "a", {"kind": "capture_image", "area_id": "work_area"}, robot="camera_01"),
             _t("t2", "b", {"kind": "capture_image", "area_id": "pickup_zone"}, robot="camera_01")]
    res, _ = run(_proposal(tasks, goals=[{"kind": "image_captured", "area_id": "work_area"}]),
                 constraints={"a": "camera_01", "b": "camera_01"})
    assert "resource_conflict" in codes(res)


def test_ambiguous_query_not_resolved():
    w = _known_world()
    w.accept(Observation(kind="object_seen", entity_id="red_block_2", label="red_block",
                         location_id="pickup_zone", area_id="work_area"), "camera_01")
    tasks = [_t("t1", "carrier", NAV_PICKUP),
             _t("t2", "carrier", {"kind": "pick_object", "object_query": {"label": "red_block"}}, ["t1"])]
    res, _ = run(_proposal(tasks), world=w)
    assert "ambiguous_object" in codes(res)


def test_query_resolves_to_unique_observed_instance():
    tasks = [dict(t) for t in CARRY]
    tasks[1] = _t("t2", "carrier", {"kind": "pick_object", "object_query": {"label": "red_block"}}, ["t1"])
    res, _ = run(_proposal(tasks))
    assert res.report.ok
    assert res.tasks[1].action.object_id == "red_block_1"


# -- open-vocabulary carry rules ------------------------------------------------------


def _world_with(oid, label, route="route_clear"):
    w = ObservedWorld()
    w.accept(Observation(kind="object_seen", entity_id=oid, label=label, location_id="pickup_zone",
                         area_id="work_area"), "camera_01")
    w.accept(Observation(kind=route, entity_id="pickup_corridor", route_id="pickup_corridor"), "lidar_01")
    return w


def _carry(oid, label):
    pick = {"kind": "pick_object", "object_id": oid}
    place = {"kind": "place_object", "object_id": oid, "location_id": "dropoff_zone"}
    tasks = [_t("t1", "carrier", NAV_PICKUP), _t("t2", "carrier", pick, ["t1"]),
             _t("t3", "carrier", NAV_DROP, ["t2"]), _t("t4", "carrier", place, ["t3"])]
    return _proposal(tasks, goals=[{"kind": "object_at", "object_query": {"label": label},
                                    "location_id": "dropoff_zone"}])


def test_pick_of_a_profiled_open_vocab_object_validates():
    res, _ = run(_carry("duck_1", "duck"), world=_world_with("duck_1", "duck"))
    assert res.report.ok, res.report.findings


def test_pick_of_an_object_without_profile_is_an_error():
    res, _ = run(_carry("mystery_crate_1", "mystery_crate"), world=_world_with("mystery_crate_1", "mystery_crate"))
    assert "unknown_object_profile" in codes(res)


def test_pick_by_query_of_an_unknown_label_is_an_error_even_unlocated():
    tasks = [_t("t1", "carrier", NAV_PICKUP),
             _t("t2", "carrier", {"kind": "pick_object", "object_query": {"label": "plush_toy"}}, ["t1"])]
    res, _ = run(_proposal(tasks, scope="observation_stage"))
    assert "unknown_object_profile" in codes(res)
    assert sum(f.code == "unknown_object_profile" for f in res.report.findings) == 1


def test_pick_over_payload_uses_the_existing_finding():
    res, _ = run(_carry("rc_car_1", "rc_car"), world=_world_with("rc_car_1", "rc_car"))
    assert "over_payload" in codes(res) and "unknown_object_profile" not in codes(res)


def test_pick_by_alias_query_resolves_the_canonical_object():
    tasks = [_t("t1", "carrier", NAV_PICKUP),
             _t("t2", "carrier", {"kind": "pick_object", "object_query": {"label": "toy_car"}}, ["t1"])]
    res, _ = run(_proposal(tasks, scope="observation_stage"), world=_world_with("rc_car_1", "rc_car"))
    assert res.tasks[1].action.object_id == "rc_car_1"
    assert "over_payload" in codes(res)


def test_sensing_accepts_any_label():
    tasks = [_t("t1", "observer", {"kind": "inspect_area", "area_id": "work_area",
                                   "object_query": {"label": "mystery_crate"}}),
             _t("t2", "observer", {"kind": "capture_image", "area_id": "pickup_zone"}, ["t1"])]
    goals = [{"kind": "object_located", "object_query": {"label": "mystery_crate"}, "area_id": "work_area"}]
    res, _ = run(_proposal(tasks, goals=goals))
    assert res.report.ok, res.report.findings


# ================================================== WS3: missing_rationale for sensorless robots

def _with_rationale(p, rationale):
    return p.model_copy(update={"rationale": rationale})


def _info(res, code):
    return [f for f in res.report.findings if f.code == code]


def test_missing_rationale_info_when_sensorless_robot_moves_without_rationale():
    res, _ = run(_proposal(CARRY))
    assert res.report.ok
    (f,) = _info(res, "missing_rationale")
    assert f.severity == "info" and f.task_id == "t1"  # the first move of the sensorless gripper car
    assert "gripper_01" in f.message


def test_no_missing_rationale_when_rationale_is_given():
    res, _ = run(_with_rationale(_proposal(CARRY), ["Camera sees the block; lidar cleared the corridor."]))
    assert not _info(res, "missing_rationale")


@pytest.mark.parametrize("rationale", [[], [""], ["   ", "\t"]])
def test_blank_rationale_counts_as_empty(rationale):
    res, _ = run(_with_rationale(_proposal(CARRY), rationale))
    assert _info(res, "missing_rationale")


def test_no_missing_rationale_for_sensing_only_plans():
    tasks = [_t("t1", "observer", {"kind": "inspect_area", "area_id": "work_area",
                                   "object_query": {"label": "red_block"}})]
    res, _ = run(_proposal(tasks, goals=[{"kind": "object_located", "object_query": {"label": "red_block"},
                                          "area_id": "work_area"}]))
    assert not _info(res, "missing_rationale")


def test_no_missing_rationale_when_the_sensorless_robot_does_not_move():
    # gripper already at dropoff_zone: a navigate_to the same place moves nothing
    tasks = [_t("t1", "carrier", {"kind": "navigate_to", "location_id": "dropoff_zone"})]
    res, _ = run(_proposal(tasks))
    assert not _info(res, "missing_rationale")


def test_missing_rationale_never_blocks_the_plan():
    res, _ = run(_proposal(CARRY))
    assert res.report.ok and "missing_rationale" not in codes(res)


# ================================================== WS4: notes about the next-stage preview

from hive_exec.validator import validate_preview  # noqa: E402

Q_NAV_PICKUP = {"task_id": "t1", "binding_id": "carrier", "depends_on": [], "preferred_robot_id": None,
                "action": NAV_PICKUP}
Q_PICK = {"task_id": "t2", "binding_id": "carrier", "depends_on": ["t1"], "preferred_robot_id": None,
          "action": {"kind": "pick_object", "object_query": {"label": "red_block"}}}


def _staged(preview_tasks, waits=("camera_01 sighting of red_block",), **extra):
    tasks = [_t("t1", "observer", {"kind": "inspect_area", "area_id": "work_area",
                                   "object_query": {"label": "red_block"}}, robot="camera_01")]
    goals = [{"kind": "object_at", "object_query": {"label": "red_block"}, "location_id": "dropoff_zone"}]
    d = _proposal(tasks, scope="observation_stage", goals=goals).model_dump()
    d["next_stage_preview"] = {"summary": "Carry it over.", "tasks": preview_tasks, "waits_for": list(waits)}
    d.update(extra)
    return PlanProposal.model_validate(d)


def preview_notes(p, world=None, robots=None):
    return validate_preview(p, CapabilityAllocator(), robots or initial_robot_states(), world or ObservedWorld())


def test_no_preview_means_no_notes():
    assert preview_notes(_proposal(CARRY)) == []


def test_preview_waiting_on_evidence_is_not_flagged_for_missing_evidence():
    # nothing observed yet: the object is not located and the corridor not scanned; that is what stage 1 is for
    notes = preview_notes(_staged([Q_NAV_PICKUP, Q_PICK]))
    assert {n.code for n in notes} & {"object_not_located", "route_not_observed_clear", "ambiguous_object",
                                      "pick_location_mismatch"} == set()


def test_preview_notes_are_never_errors_and_keep_their_code():
    bad = dict(Q_NAV_PICKUP, binding_id="looker",
               action={"kind": "inspect_area", "area_id": "dropoff_zone", "object_query": {"label": "x"}})
    notes = preview_notes(_staged([bad]))
    assert notes and all(n.severity in ("warning", "info") for n in notes)
    assert any(n.code == "no_eligible_robot" and n.severity == "warning" for n in notes)


def test_preview_pick_of_an_unprofiled_object_is_noted_early():
    pick = dict(Q_PICK, action={"kind": "pick_object", "object_query": {"label": "duck_toy"}}, depends_on=[])
    notes = preview_notes(_staged([pick]))
    assert any(n.code == "unknown_object_profile" and n.severity == "warning" and n.task_id == "t2" for n in notes)


def test_preview_over_a_blocked_corridor_is_noted():
    notes = preview_notes(_staged([Q_NAV_PICKUP]), world=_known_world("route_blocked"))
    assert any(n.code == "route_blocked" and n.severity == "warning" for n in notes)


def test_preview_for_an_unavailable_robot_is_noted():
    robots = initial_robot_states()
    robots["gripper_01"].available = False
    notes = preview_notes(_staged([Q_NAV_PICKUP]), robots=robots)
    assert any(n.code in ("no_eligible_robot", "robot_unavailable") and n.severity == "warning" for n in notes)


def test_preview_checks_leave_the_plan_untouched_and_never_change_ok():
    p = _staged([Q_NAV_PICKUP, Q_PICK])
    before = p.model_dump()
    res, alloc = run(p)
    assert res.report.ok and p.model_dump() == before
    assert set(alloc.robots) == {"observer"}  # the preview's "carrier" binding is never allocated
    assert [t.task_id for t in res.tasks] == ["t1"] and res.tasks[0].robot_id == "camera_01"


def test_preview_check_that_raises_becomes_one_info_note():
    p = _staged([Q_NAV_PICKUP])

    class Boom(CapabilityAllocator):
        def allocate(self, *a, **k):
            raise RuntimeError("boom")

    notes = validate_preview(p, Boom(), initial_robot_states(), ObservedWorld())
    assert [(n.code, n.severity) for n in notes] == [("preview_unchecked", "info")]
