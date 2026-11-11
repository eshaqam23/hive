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
