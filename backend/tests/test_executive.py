"""Five acceptance stories (Baseline planner, fake fleet) plus robustness checks.

These prove the deterministic pipeline. They do NOT count as live-Qwen acceptance.
"""

import asyncio
import json

import pytest

from hive_exec.agent_commands import envelope_json
from hive_exec.context import build_context
from hive_exec.contracts import TaskEvent
from hive_exec.executive import ExecError, Executive
from hive_exec.gateway.fake import FakeGateway
from hive_exec.planner.baseline import BaselinePlanner

from .conftest import DEMO, approve_latest, wait_for

DONE = {"completed", "blocked", "rejected", "canceled", "planner_error", "answered"}


def robots_with_attempts(ex):
    return {a.robot_id for a in ex.attempts.values()}


async def test_story_known_object_delivery(make_exec):
    ex = await make_exec("known")
    await ex.create_mission(DEMO)
    p = ex.mission.revisions[-1]
    assert p.proposal.scope == "full" and p.validation.ok
    assert set(p.allocations.values()) == {"gripper_01"}
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"
    b = ex.world.objects["red_block_1"]
    assert b.location_id == "dropoff_zone" and b.held_by is None and b.source == "gripper_01"
    assert robots_with_attempts(ex) == {"gripper_01"}


async def test_story_unknown_object_and_route(make_exec):
    ex = await make_exec("unknown")
    await ex.create_mission(DEMO)
    r1 = ex.mission.revisions[-1]
    assert r1.proposal.scope == "observation_stage"
    assert {t.robot_id for t in r1.tasks} == {"camera_01", "lidar_01"}
    assert all(not t.depends_on for t in r1.tasks)  # concurrent sensing
    await approve_latest(ex)
    await wait_for(ex, {"awaiting_approval"} | DONE)
    r2 = ex.mission.revisions[-1]
    assert r2.revision == 2 and r2.proposal.scope == "full" and r2.status == "awaiting_approval"
    assert ex.mission.status == "awaiting_approval"  # transport needs a fresh approval
    assert not any(a.robot_id == "gripper_01" for a in ex.attempts.values())
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"


async def test_story_camera_only(make_exec):
    ex = await make_exec()
    await ex.create_mission("Take a picture of the work area.")
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"
    assert robots_with_attempts(ex) == {"camera_01"}


@pytest.mark.parametrize("cmd,status", [("Bring the block over here.", "clarification"),
                                        ("Fly the drone over the warehouse.", "rejected")])
async def test_story_ambiguous_or_unsupported(make_exec, cmd, status):
    ex = await make_exec()
    await ex.create_mission(cmd)
    assert ex.mission.status == status
    assert not ex.mission.revisions and not ex.attempts


async def test_story_failure_after_pickup_recovers(make_exec):
    ex = await make_exec("known", ["nav_fault_after_pickup"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"awaiting_approval"} | DONE)
    assert ex.mission.recovery_used
    assert ex.robots["gripper_01"].carried_object == "red_block_1"  # possession preserved
    rec = ex.mission.revisions[-1]
    assert rec.kind == "recovery"
    assert [t.skill for t in rec.tasks] == ["navigate_to", "place_object"]  # replanning starts from held block
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"


async def test_story_failure_after_pickup_honest_block(make_exec):
    ex = await make_exec("known", ["gripper_fault_after_pickup"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "blocked"
    assert ex.robots["gripper_01"].carried_object == "red_block_1"
    assert ex.world.objects["red_block_1"].held_by == "gripper_01"


@pytest.mark.parametrize("fault,reason", [("block_missing", "no red_block"), ("route_blocked", "blocked")])
async def test_observation_outcomes_block(make_exec, fault, reason):
    ex = await make_exec("unknown", [fault])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "blocked"
    assert reason in ex.mission.outcome["reason"]
    assert "gripper_01" not in robots_with_attempts(ex)


async def test_ambiguous_target_after_observation_asks(make_exec):
    ex = await make_exec("unknown", ["two_red_blocks"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE | {"clarification"}) == "clarification"
    await ex.answer_clarification("red_block_2")
    rev = ex.mission.revisions[-1]
    assert rev.validation.ok and any(getattr(t.action, "object_id", None) == "red_block_2" for t in rev.tasks)


async def test_low_battery_rejected_before_dispatch(make_exec):
    ex = await make_exec("known", ["low_battery_gripper"])
    await ex.create_mission(DEMO)
    p = ex.mission.revisions[-1]
    assert not p.validation.ok
    assert "insufficient_battery" in {f.code for f in p.validation.findings}
    with pytest.raises(ExecError):
        await ex.approve(p.revision, p.proposal_hash)
    assert not ex.attempts


async def test_stale_approval(make_exec):
    ex = await make_exec("known")
    await ex.create_mission(DEMO)
    p = ex.mission.revisions[-1]
    with pytest.raises(ExecError):
        await ex.approve(p.revision, "deadbeef")
    await ex.edit(p.revision, [{"op": "set_robot", "binding_id": "carrier", "robot_id": "gripper_01"}])
    with pytest.raises(ExecError):  # superseded revision
        await ex.approve(p.revision, p.proposal_hash)
    # state change after validation invalidates the approval
    await ex.inject_fault("gripper_unavailable")
    p2 = ex.mission.revisions[-1]
    with pytest.raises(ExecError):
        await ex.approve(p2.revision, p2.proposal_hash)
    assert not ex.attempts


async def test_duplicate_and_old_run_events(make_exec):
    ex = await make_exec("known", ["hang_navigate"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"executing"})
    att = next(iter(ex.attempts.values()))
    fake_success = TaskEvent(event_id="x1", run_id="run-old", attempt_id=att.attempt_id, robot_id=att.robot_id,
                             seq=999, status="succeeded", result={"location_id": "pickup_zone"})
    await ex.handle_event(fake_success)  # old run: discarded
    assert ex.robots["gripper_01"].location_id == "dropoff_zone"
    real = fake_success.model_copy(update={"run_id": ex.run_id, "event_id": "x2"})
    await ex.handle_event(real)
    loc_after = ex.robots["gripper_01"].location_id
    await ex.handle_event(real)  # duplicate
    await ex.handle_event(real.model_copy(update={"event_id": "x3", "seq": 1000}))  # late after terminal
    kinds = [e["kind"] for e in ex.timeline]
    assert "event_discarded" in kinds and "event_duplicate" in kinds and "event_late" in kinds
    assert loc_after == "pickup_zone"
    await ex.cancel()
    await wait_for(ex, DONE)


async def test_confirmed_cancellation(make_exec):
    ex = await make_exec("known", ["hang_navigate"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"executing"})
    for _ in range(50):
        if any(a.status == "accepted" for a in ex.attempts.values()):
            break
        import asyncio
        await asyncio.sleep(0.01)
    await ex.cancel()
    # Must not claim canceled until the robot confirms termination.
    assert ex.mission.status in ("canceling", "canceled")
    assert await wait_for(ex, DONE) == "canceled"
    assert all(a.status == "canceled" for a in ex.attempts.values())
    assert any(e["kind"] == "event" and "canceled" in e["message"] for e in ex.timeline)


async def test_uncertain_outcome_blocks_without_retry(make_exec):
    ex = await make_exec("known", ["uncertain_place"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "blocked"
    places = [a for a in ex.attempts.values() if a.envelope["skill"] == "place_object"]
    assert len(places) == 1 and places[0].status == "uncertain"
    assert not ex.mission.revisions[-1].kind == "recovery"


async def test_hidden_truth_does_not_leak_into_context(make_exec):
    ex = await make_exec("unknown", ["two_red_blocks", "route_blocked"])
    ctx = build_context(phase="initial", command=DEMO, robots=ex.robots, world=ex.world).to_json()
    assert "red_block_1" not in ctx and "red_block_2" not in ctx and "blocked" not in ctx
    assert "mass_kg" not in ctx and "obstacle" not in ctx
    assert json.loads(ctx)["observed"]["routes"]["pickup_corridor"] == "unknown"
    # state served to the UI also excludes truth
    assert "red_block_2" not in json.dumps(ex.state())


async def test_reset_discards_prior_run(make_exec):
    ex = await make_exec("known", ["hang_navigate"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"executing"})
    old_run = ex.run_id
    await ex.reset("unknown")
    assert ex.run_id != old_run and ex.mission is None and not ex.attempts
    assert ex.world.objects == {}


async def test_late_result_for_uncertain_attempt_is_reconciled_not_advanced(make_exec):
    ex = await make_exec("known", ["hang_navigate"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"executing"})
    att = next(iter(ex.attempts.values()))
    base = dict(run_id=ex.run_id, attempt_id=att.attempt_id, robot_id=att.robot_id)
    await ex.handle_event(TaskEvent(event_id="u1", seq=900, status="uncertain", **base))
    assert await wait_for(ex, DONE) == "blocked"
    await ex.handle_event(TaskEvent(event_id="u2", seq=901, status="succeeded",
                                    result={"location_id": "pickup_zone"}, **base))
    assert ex.robots["gripper_01"].location_id == "pickup_zone"  # effect reconciled
    assert ex.mission.status == "blocked"  # plan not advanced
    assert sum(1 for a in ex.attempts.values()) == 1  # nothing redispatched
    await ex.reset("unknown")


async def test_sibling_success_after_failure_still_recovers():
    """A sibling that completes before its cancel lands must still let the bounded recovery run."""
    import asyncio

    from hive_exec.executive import Executive
    from hive_exec.gateway.fake import FakeGateway
    from hive_exec.planner.baseline import BaselinePlanner

    class LateCancelGateway(FakeGateway):
        async def cancel(self, run_id, attempt_id):  # the robot passed its commit point; cancel is moot
            return None

    ex = Executive(LateCancelGateway(time_scale=0.2), {"baseline": BaselinePlanner()}, default_planner="baseline")
    await ex.gateway.start(ex.handle_event)
    await ex.reset("unknown")
    try:
        await ex.create_mission(DEMO)
        await approve_latest(ex)
        for _ in range(200):
            if len(ex.attempts) == 2 and all(a.status in ("accepted", "running") for a in ex.attempts.values()):
                break
            await asyncio.sleep(0.01)
        lidar = next(a for a in ex.attempts.values() if a.robot_id == "lidar_01")
        await ex.handle_event(TaskEvent(event_id="f1", run_id=ex.run_id, attempt_id=lidar.attempt_id,
                                        robot_id="lidar_01", seq=900, status="failed",
                                        error={"code": "lidar_fault", "message": "injected"}))
        assert ex.mission.status == "recovering"
        assert await wait_for(ex, {"awaiting_approval"} | DONE) == "awaiting_approval"
        camera = next(a for a in ex.attempts.values() if a.robot_id == "camera_01")
        assert camera.status == "succeeded"
        assert ex.mission.recovery_used and ex.mission.revisions[-1].kind == "recovery"
    finally:
        await ex.gateway.stop()


async def test_task_stop_cancels_one_task_and_recovers_while_others_run(make_exec):
    import asyncio

    ex = await make_exec("unknown", ["hang_scan"], time_scale=0.2)  # camera inspect takes ~0.6 s
    await ex.create_mission(DEMO)
    rev = ex.mission.revisions[-1]
    scan_task = next(t.task_id for t in rev.tasks if t.skill == "scan_route")
    await approve_latest(ex)
    for _ in range(200):
        if len(ex.attempts) == 2 and all(a.status in ("accepted", "running") for a in ex.attempts.values()):
            break
        await asyncio.sleep(0.01)
    scan = next(a for a in ex.attempts.values() if a.robot_id == "lidar_01")
    camera = next(a for a in ex.attempts.values() if a.robot_id == "camera_01")
    rt = await ex.cancel_task(scan_task)
    assert rt.state == "canceling" and ex.mission.status == "executing"  # not canceled until the robot confirms
    again = await ex.cancel_task(scan_task)  # idempotent while the cancel is in flight
    assert again.state == "canceling"
    for _ in range(200):
        if scan.status == "canceled":
            break
        await asyncio.sleep(0.005)
    assert scan.status == "canceled"
    assert camera.status in ("accepted", "running"), "the other task must keep running"
    assert ex.mission.status == "recovering"
    rt = ex.mission.tasks[f"{rev.revision}:{scan_task}"]
    assert rt.state == "canceled" and rt.error["code"] == "operator_stop"
    assert await wait_for(ex, {"awaiting_approval"} | DONE) == "awaiting_approval"
    assert camera.status == "succeeded"
    assert ex.mission.recovery_used and ex.mission.revisions[-1].kind == "recovery"
    assert any("canceled (operator_stop)" in h for h in ex.mission.history)


async def test_task_stop_refuses_when_nothing_is_running(make_exec):
    ex = await make_exec("known")
    with pytest.raises(ExecError):
        await ex.cancel_task("t1")  # no mission
    await ex.create_mission(DEMO)
    with pytest.raises(ExecError) as e:
        await ex.cancel_task("t1")  # awaiting approval
    assert e.value.status == 409
    await approve_latest(ex)
    with pytest.raises(ExecError) as e:
        await ex.cancel_task("t99")
    assert e.value.status == 404
    assert await wait_for(ex, DONE) == "completed"


async def test_every_dispatched_attempt_is_written_and_shown(tmp_path):
    ex = Executive(FakeGateway(time_scale=0.005), {"baseline": BaselinePlanner()}, default_planner="baseline",
                   runs_dir=tmp_path)
    await ex.gateway.start(ex.handle_event)
    await ex.reset("known")
    try:
        await ex.create_mission(DEMO)
        pending = ex.state()["mission"]["revisions"][-1]["tasks"]
        assert all(t["agent_command"]["envelope"] is None and t["agent_command"]["line"] for t in pending)
        await approve_latest(ex)
        assert await wait_for(ex, DONE) == "completed"
        files = sorted(tmp_path.rglob("*.json"))
        assert len(files) == len(ex.attempts) > 0
        for att in ex.attempts.values():
            n = ex.mission.tasks[f"{att.revision}:{att.task_id}"].attempt_ids.index(att.attempt_id) + 1
            f = tmp_path / ex.run_id / "agent_commands" / att.robot_id / f"{att.task_id}-r{att.revision}-a{n}.json"
            assert f.read_text(encoding="utf-8") == envelope_json(att.envelope)
        s = ex.state()
        task = s["mission"]["revisions"][0]["tasks"][0]
        cmd = task["agent_command"]
        assert cmd["envelope"]["schema_version"] == "hive.task-envelope.v1"
        assert cmd["envelope"]["task_id"] == task["task_id"] and cmd["attempt_id"]
        assert cmd["line"].startswith(task["action"]["kind"] + "(")
        assert cmd["file"].endswith(f"{task['task_id']}-r1-a1.json")
    finally:
        await ex.gateway.stop()


async def test_no_runs_dir_means_no_files(make_exec, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ex = await make_exec("known")
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, DONE)
    assert not list(tmp_path.rglob("*.json"))
    cmd = ex.state()["mission"]["revisions"][0]["tasks"][0]["agent_command"]
    assert cmd["envelope"] is not None and cmd["file"] is None


# -- open vocabulary + random sim world -----------------------------------------------


async def test_reset_random_world_records_seed_and_robot_start_state(make_exec):
    from hive_exec.sim.fake_world import FakeWorld

    seed = next(s for s in range(200) if FakeWorld(seed=s, world="random").robots["gripper_01"]["location_id"]
                == "pickup_zone")
    ex = await make_exec()
    assert ex.state()["sim"] == {"world": "fixed", "seed": None}
    await ex.reset("unknown", [], world="random", seed=seed)
    s = ex.state()
    assert s["sim"] == {"world": "random", "seed": seed}
    truth = await ex.gateway.debug_truth()
    assert truth["seed"] == seed and truth["world"] == "random"
    for rid, r in truth["robots"].items():
        assert ex.robots[rid].location_id == r["location_id"]
        assert ex.robots[rid].battery_pct == pytest.approx(r["battery_pct"])
    assert ex.robots["gripper_01"].location_id == "pickup_zone"
    assert ex.export()["sim"] == {"world": "random", "seed": seed}
    assert f"seed={seed}" in ex.timeline[-1]["message"] or any(f"seed={seed}" in e["message"] for e in ex.timeline)


async def test_reset_random_without_seed_picks_one_that_replays(make_exec):
    ex = await make_exec()
    await ex.reset("unknown", [], world="random")
    seed = ex.state()["sim"]["seed"]
    assert isinstance(seed, int)
    first = await ex.gateway.debug_truth()
    await ex.reset("unknown", [], world="random", seed=seed)
    again = await ex.gateway.debug_truth()
    assert first["objects"] == again["objects"] and first["robots"] == again["robots"]


async def test_executive_default_world_applies_to_start_and_plain_reset():
    from hive_exec.executive import Executive

    ex = Executive(FakeGateway(time_scale=0.005), {"baseline": BaselinePlanner()}, default_planner="baseline",
                   sim_world="random")
    await ex.start()
    try:
        assert ex.state()["sim"]["world"] == "random" and isinstance(ex.state()["sim"]["seed"], int)
        await ex.reset("unknown")
        assert ex.state()["sim"]["world"] == "random"
        await ex.reset("unknown", world="fixed")
        assert ex.state()["sim"] == {"world": "fixed", "seed": None}
    finally:
        await ex.gateway.stop()


def test_executive_rejects_an_unknown_default_world():
    from hive_exec.executive import Executive

    with pytest.raises(ValueError):
        Executive(FakeGateway(), {"baseline": BaselinePlanner()}, sim_world="chaos")


async def test_reset_logs_when_the_gateway_ignores_the_world(make_exec):
    ex = await make_exec()

    async def old_fleet_reset(run_id, faults=None, world="fixed", seed=None):
        await FakeGateway.reset(ex.gateway, run_id, faults)  # an old fleet: always the fixed world
        return {"world": "fixed", "seed": None}

    ex.gateway.reset = old_fleet_reset
    await ex.reset("unknown", [], world="random", seed=9)
    assert ex.state()["sim"] == {"world": "fixed", "seed": None}
    assert any("ignored" in e["message"] for e in ex.timeline)


async def test_story_open_vocab_delivery_in_a_random_world(make_exec):
    from hive_exec.sim.fake_world import FakeWorld

    def good(s):
        w = FakeWorld(seed=s, world="random")
        ducks = [o for o in w.objects.values() if o["label"] == "duck"]
        return (len(ducks) == 1 and ducks[0]["location_id"] == "pickup_zone" and not w.obstacles
                and all(r["battery_pct"] > 50 for r in w.robots.values()))

    seed = next(s for s in range(5000) if good(s))
    ex = await make_exec()
    await ex.reset("unknown", [], world="random", seed=seed)
    await ex.create_mission("Find the rubber duck in the work area, check the pickup corridor, "
                            "and bring the duck to the drop-off area.")
    goals = ex.mission.revisions[-1].proposal.mission_goals
    assert goals[0].object_query.label == "duck"
    await approve_latest(ex)
    await wait_for(ex, {"awaiting_approval"} | DONE)
    assert ex.mission.revisions[-1].proposal.scope == "full"
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"
    assert ex.world.objects["duck_1"].location_id == "dropoff_zone"


def test_initial_context_lists_no_unasked_object_labels():
    from hive_exec.world import ObservedWorld, initial_robot_states

    ctx = build_context(phase="initial", command="Find the duck.", robots=initial_robot_states(),
                        world=ObservedWorld())
    assert ctx.unresolved == ["clearance of pickup_corridor unknown (no fresh scan)"]


def test_context_flags_ambiguity_of_any_observed_label():
    from hive_exec.contracts import Observation
    from hive_exec.world import ObservedWorld, initial_robot_states

    w = ObservedWorld()
    for oid in ("traffic_cone_1", "traffic_cone_2"):
        w.accept(Observation(kind="object_seen", entity_id=oid, label="traffic_cone", location_id="pickup_zone",
                             area_id="work_area"), "camera_01")
    ctx = build_context(phase="initial", command="x", robots=initial_robot_states(), world=w)
    assert "2 instances of traffic_cone observed: ambiguous" in ctx.unresolved


@pytest.mark.parametrize("text,goal", [
    ("Find the duck.", {"kind": "object_located", "object_query": {"label": "duck"}, "area_id": "work_area"}),
    ("Find a red box in the feild", {"kind": "object_located", "object_query": {"label": "red_block"},
                                     "area_id": "work_area"}),
    ("Locate the traffic cone", {"kind": "object_located", "object_query": {"label": "traffic_cone"},
                                 "area_id": "work_area"}),
    ("Bring the toy car to the drop-off area", {"kind": "object_at", "object_query": {"label": "rc_car"},
                                                "location_id": "dropoff_zone"}),
    ("Find the red block", {"kind": "object_located", "object_query": {"label": "red_block"}, "area_id": "work_area"}),
])
def test_baseline_reads_any_profiled_label_or_alias(text, goal):
    from hive_exec.planner.baseline import parse_goals

    goals, issues, question = parse_goals(text)
    assert goals == [goal] and not issues and question is None


async def test_synapse_reset_passes_world_and_seed_through():
    from hive_exec.gateway.synapse import SynapseGateway

    gw = SynapseGateway("ws://127.0.0.1:1")
    sent = []

    async def fake_request(op, **payload):
        sent.append((op, payload))
        return {"run_id": payload["run_id"], "settled": True, "world": payload.get("world"), "seed": payload.get("seed")}

    gw._request = fake_request
    assert await gw.reset("run-x", ["route_blocked"], world="random", seed=5) == {"world": "random", "seed": 5}
    assert sent == [("reset", {"run_id": "run-x", "faults": ["route_blocked"], "world": "random", "seed": 5})]

    async def old_fleet(op, **payload):
        return {"run_id": payload["run_id"], "settled": True}

    gw._request = old_fleet
    assert await gw.reset("run-y", world="random", seed=5) == {"world": "fixed", "seed": None}


# ================================================== WS2: answer, find-only report, clarification rounds

from hive_exec.contracts import Observation, PlannerRecord, PlanProposal  # noqa: E402
from hive_exec.executive import TERMINAL_MISSION  # noqa: E402
from hive_exec.planner.base import Planner, PlannerOutput  # noqa: E402

V1 = "hive.plan-proposal.v1"
ANSWER = {"schema_version": V1, "disposition": "answer", "summary": "Gripper car abilities.",
          "answer": "The gripper car picks, places and drives to the pickup and drop-off zones."}
CAP_REASON = "The planner still could not resolve the request after 3 questions."


def ask(q="Which object?", options=()):
    return {"schema_version": V1, "disposition": "clarify", "summary": "Unclear.", "clarification_question": q,
            "clarification_options": list(options)}


class StubPlanner(Planner):
    """Returns scripted proposals in order (the last one repeats) and records every context."""

    kind = "stub"

    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.contexts = []

    async def propose(self, ctx, repair_feedback=None, previous_output=None):
        self.contexts.append(ctx)
        raw = self.proposals.pop(0) if len(self.proposals) > 1 else self.proposals[0]
        return PlannerOutput(PlanProposal.model_validate(raw),
                             PlannerRecord(kind="baseline", label="stub", latency_ms=0.0))


async def stub_exec(make_exec, proposals, scenario="unknown", faults=None):
    stub = StubPlanner(proposals)
    ex = await make_exec(scenario, faults, planners={"stub": stub})
    return ex, stub


def test_answered_is_terminal():
    assert "answered" in TERMINAL_MISSION
    assert "answered" in DONE


async def test_answer_proposal_ends_mission_answered(make_exec):
    ex, _ = await stub_exec(make_exec, [ANSWER])
    await ex.create_mission("What can the gripper car do?")
    m = ex.mission
    assert m.status == "answered"
    assert m.outcome == {"status": "answered", "reason": ANSWER["answer"]}
    assert not m.revisions and not ex.attempts
    assert any(e["kind"] == "answer" and ANSWER["answer"] in e["message"] for e in ex.timeline)
    assert ex.state()["mission"]["status"] == "answered"


async def test_answer_after_clarification_is_answered(make_exec):
    ex, _ = await stub_exec(make_exec, [ask("Which car?"), ANSWER])
    await ex.create_mission("What can it do?")
    assert ex.mission.status == "clarification"
    await ex.answer_clarification("the gripper car")
    assert ex.mission.status == "answered"


async def test_answered_mission_allows_no_more_actions(make_exec):
    ex, _ = await stub_exec(make_exec, [ANSWER])
    await ex.create_mission("What can the gripper car do?")
    with pytest.raises(ExecError):
        await ex.answer_clarification("x")
    with pytest.raises(ExecError):
        await ex.replan()


async def test_answer_on_continuation_blocks_like_reject(make_exec):
    ex = await make_exec("unknown", ["two_red_blocks"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE | {"clarification"}) == "clarification"
    ex.planners[ex.mission.planner_kind] = StubPlanner([ANSWER])
    await ex.answer_clarification("red_block_2")
    assert ex.mission.status == "blocked"
    assert ex.mission.outcome["reason"] == "No continuation: " + ANSWER["answer"]
    assert "gripper_01" not in robots_with_attempts(ex)


async def test_find_only_reports_what_was_found(make_exec):
    ex = await make_exec("unknown")
    await ex.create_mission("Find a red box in the feild")
    rev = ex.mission.revisions[-1]
    assert rev.proposal.scope == "full" and {t.skill for t in rev.tasks} == {"inspect_area"}
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"
    found = ex.world.instances("red_block", "work_area")
    assert found
    reason = ex.mission.outcome["reason"]
    assert reason.startswith("Found ")
    for b in found:
        assert f"{b.object_id} in {b.location_id}" in reason
    assert robots_with_attempts(ex) == {"camera_01"}


async def test_find_only_lists_several_found(make_exec):
    ex = await make_exec("unknown", ["two_red_blocks"])
    await ex.create_mission("Find the red block.")
    await approve_latest(ex)
    assert await wait_for(ex, DONE | {"clarification"}) == "completed"
    reason = ex.mission.outcome["reason"]
    assert "red_block_1 in" in reason and "red_block_2 in" in reason


async def test_find_only_nothing_found_completes_with_search_report(make_exec):
    ex = await make_exec("unknown")
    await ex.create_mission("find me the duck in the feild")
    goals = ex.mission.revisions[-1].proposal.mission_goals
    assert [g.kind for g in goals] == ["object_located"] and goals[0].object_query.label == "duck"
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "completed"
    assert ex.mission.outcome["reason"] == "Searched work_area: no duck found."


async def test_object_located_satisfied_by_fresh_absence_only(make_exec):
    ex = await make_exec("unknown")
    await ex.create_mission("Find the duck.")
    g = ex.mission.revisions[-1].proposal.mission_goals[0]
    assert not ex.goal_satisfied(g, ex.mission.created_at)
    ex.world.accept(Observation(kind="object_absent", entity_id="work_area:duck", area_id="work_area",
                                label="duck"), source="camera_01")
    assert ex.goal_satisfied(g, ex.mission.created_at)
    assert not ex.goal_satisfied(g, ex.clock() + 5)  # a report older than `since` does not count


async def test_fetch_with_missing_object_still_blocks(make_exec):
    ex = await make_exec("unknown", ["block_missing"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE) == "blocked"
    assert "no red_block" in ex.mission.outcome["reason"]
    assert ex.mission.outcome["reason"] != "Searched work_area: no red_block found."


async def test_clarifications_history_reaches_context_oldest_first(make_exec):
    reject = {"schema_version": V1, "disposition": "reject", "summary": "Cannot do it."}
    ex, stub = await stub_exec(make_exec, [ask("Which object?", ["the red block", "the duck"]),
                                           ask("Where should it go?"), reject])
    await ex.create_mission("get it")
    m = ex.mission
    assert m.clarifications == [{"question": "Which object?", "options": ["the red block", "the duck"],
                                 "answer": None, "phase": "initial"}]
    assert ex.state()["mission"]["clarifications"] == m.clarifications
    await ex.answer_clarification("the red block")
    await ex.answer_clarification("the drop-off zone")
    assert [c["answer"] for c in m.clarifications] == ["the red block", "the drop-off zone"]
    assert [c["phase"] for c in m.clarifications] == ["initial", "clarification_answer"]
    last = stub.contexts[-1]
    assert [c["question"] for c in last.clarifications] == ["Which object?", "Where should it go?"]
    assert [c["answer"] for c in last.clarifications] == ["the red block", "the drop-off zone"]
    assert last.clarification == {"question": "Where should it go?", "answer": "the drop-off zone"}
    assert "Which object?" in last.to_json()
    assert ex.export()["mission"]["clarifications"] == m.clarifications


async def test_fourth_clarify_blocks_the_mission(make_exec):
    ex, _ = await stub_exec(make_exec, [ask("Q?")])
    await ex.create_mission("get it")
    for n in range(3):
        assert ex.mission.status == "clarification"
        await ex.answer_clarification(f"a{n}")
    assert ex.mission.status == "blocked"
    assert ex.mission.outcome["reason"] == CAP_REASON
    assert len(ex.mission.clarifications) == 3
    assert not ex.attempts


async def test_mid_mission_clarification_offers_instance_ids(make_exec):
    ex = await make_exec("unknown", ["two_red_blocks"])
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    assert await wait_for(ex, DONE | {"clarification"}) == "clarification"
    c = ex.mission.clarifications[-1]
    assert c["options"] == ["red_block_1", "red_block_2"] and c["answer"] is None
    assert c["question"] == ex.mission.clarification_question
    await ex.answer_clarification("red_block_2")
    assert ex.mission.clarifications[-1]["answer"] == "red_block_2"


class ConstructedPlanner(StubPlanner):
    """Bypasses model validation, as a buggy or hostile planner adapter might."""

    async def propose(self, ctx, repair_feedback=None, previous_output=None):
        self.contexts.append(ctx)
        return PlannerOutput(PlanProposal.model_construct(**self.proposals[0]),
                             PlannerRecord(kind="baseline", label="stub", latency_ms=0.0))


async def test_unknown_disposition_fails_closed(make_exec):
    bad = {"schema_version": V1, "disposition": "execute_now", "scope": None, "summary": "Do it.",
           "mission_goals": [], "stage_goals": [], "tasks": [], "issues": [], "clarification_question": None,
           "clarification_options": [], "answer": None}
    ex = await make_exec("unknown", planners={"stub": ConstructedPlanner([bad])})
    await ex.create_mission("Find the duck.")
    assert ex.mission.status == "blocked"
    assert "Unsupported planner disposition 'execute_now'" in ex.mission.outcome["reason"]
    assert not ex.mission.revisions and not ex.attempts


async def test_answer_constructed_without_validation_still_dispatches_nothing(make_exec):
    raw = dict(ANSWER, scope=None, mission_goals=[], stage_goals=[], tasks=[], issues=[],
               clarification_question=None, clarification_options=[])
    ex = await make_exec("unknown", planners={"stub": ConstructedPlanner([raw])})
    await ex.create_mission("What can the gripper car do?")
    assert ex.mission.status == "answered" and not ex.mission.revisions and not ex.attempts


async def test_make_revision_refuses_unvalidated_proposals(make_exec):
    ex, _ = await stub_exec(make_exec, [ANSWER])
    await ex.create_mission("What can the gripper car do?")
    record = PlannerRecord(kind="baseline", label="stub", latency_ms=0.0)
    with pytest.raises(ExecError):
        ex._make_revision(ex.mission, PlanProposal.model_validate(ANSWER), record, None, None, "initial", {})
    assert not ex.mission.revisions


# ================================================== WS2 fixes: stale beliefs, revision build failures

PHOTO_FOR_FIND = {"schema_version": V1, "disposition": "plan", "scope": "full", "summary": "Photograph the area.",
                  "mission_goals": [{"kind": "object_located", "object_query": {"label": "red_block"},
                                     "area_id": "work_area"}],
                  "stage_goals": [], "tasks": [{"task_id": "t1", "binding_id": "observer",
                                                "action": {"kind": "capture_image", "area_id": "work_area"},
                                                "depends_on": [], "preferred_robot_id": "camera_01"}]}


async def test_seeded_belief_does_not_complete_a_find_without_an_inspection(make_exec):
    ex, _ = await stub_exec(make_exec, [PHOTO_FOR_FIND], scenario="known")
    await ex.create_mission("Find the red block.")
    assert ex.world.instances("red_block", "work_area")  # the operator_seed prior survey
    g = ex.mission.revisions[-1].proposal.mission_goals[0]
    assert not ex.goal_satisfied(g, ex.mission.created_at)
    await approve_latest(ex)
    for _ in range(1000):
        m = ex.mission
        if m.status in DONE or (len(m.revisions) > 1 and m.status == "awaiting_approval"):
            break
        await asyncio.sleep(0.01)
    assert ex.mission.status != "completed"
    assert not (ex.mission.outcome or {}).get("reason", "").startswith("Found")
    assert {a.envelope["skill"] for a in ex.attempts.values()} == {"capture_image"}  # never inspected
    assert any(e["kind"] == "evaluation" for e in ex.timeline)  # the normal continuation path ran


async def test_object_located_counts_only_fleet_reports_since_mission_start(make_exec):
    ex = await make_exec("unknown")
    await ex.create_mission("Find the duck.")
    g = ex.mission.revisions[-1].proposal.mission_goals[0]
    start = ex.mission.created_at
    seen = Observation(kind="object_seen", entity_id="duck_1", location_id="work_area", area_id="work_area",
                       label="duck")
    ex.world.accept(seen, source="operator_seed")
    assert not ex.goal_satisfied(g, start)  # not a fleet robot
    ex.world.accept(Observation(kind="object_absent", entity_id="work_area:duck", area_id="work_area",
                                label="duck"), source="operator_seed")
    assert not ex.goal_satisfied(g, start)  # an absence needs a fleet robot too
    ex.world.accept(seen, source="camera_01")
    assert ex.goal_satisfied(g, start)
    assert not ex.goal_satisfied(g, ex.clock() + 5)  # seen before `since`
    assert ex._completion_reason([g], start).startswith("Found duck_1")


class TasklessPlanner(StubPlanner):
    """A plan stripped of its tasks without validation: the revision cannot be built."""

    async def propose(self, ctx, repair_feedback=None, previous_output=None):
        self.contexts.append(ctx)
        good = PlanProposal.model_validate(self.proposals[0])
        return PlannerOutput(good.model_copy(update={"tasks": []}),
                             PlannerRecord(kind="baseline", label="stub", latency_ms=0.0))


async def test_revision_build_failure_ends_the_mission_planner_error(make_exec):
    ex = await make_exec("unknown", planners={"stub": TasklessPlanner([PHOTO_FOR_FIND])})
    await ex.create_mission("Find the red block.")
    m = ex.mission
    assert m.status == "planner_error"
    assert m.outcome["status"] == "planner_error" and "revision" in m.outcome["reason"].lower()
    assert not m.revisions and not ex.attempts
    assert any(e["kind"] == "planner_error" for e in ex.timeline)


# ================================================== WS2: baseline intents (find vs fetch vs question)

from hive_exec.planner.baseline import find_label, parse_goals  # noqa: E402
from hive_exec.world import ObservedWorld, initial_robot_states  # noqa: E402


@pytest.mark.parametrize("text,label", [
    ("find the mystery crate", "mystery_crate"),
    ("Find the plush toy.", "plush_toy"),
    ("find me the duck in the feild", "duck"),
    ("Find a red box in the feild", "red_block"),
    ("Look for the rubber duck near the drop-off zone", "duck"),
    ("find the mystery crate near the cone", "mystery_crate"),   # the operator's noun, not a nearby profile
    ("Find the big red block", "red_block"),                     # profile phrase inside the noun phrase
    ("bring the red one to the drop-off area", "red_block"),     # bare colour fallback
    ("Locate the toy car", "rc_car"),
])
def test_find_label_takes_the_operators_noun(text, label):
    assert find_label(text) == label


@pytest.mark.parametrize("text", ["get the block", "check on the thing", "Bring the block over here.",
                                  "find something", "inspect the work area", "take it"])
def test_find_label_none_for_vague_or_place_nouns(text):
    assert find_label(text) is None


async def baseline_propose(command, **kw):
    ctx = build_context(phase=kw.pop("phase", "initial"), command=command, robots=initial_robot_states(),
                        world=ObservedWorld(), **kw)
    return (await BaselinePlanner().propose(ctx)).proposal


@pytest.mark.parametrize("cmd,label", [("find the mystery crate", "mystery_crate"),
                                       ("Find a red box in the feild", "red_block"),
                                       ("check for the plush toy", "plush_toy")])
async def test_baseline_find_only_is_inspect_only(cmd, label):
    p = await baseline_propose(cmd)
    assert p.disposition == "plan" and p.scope == "full"
    assert [(g.kind, g.object_query.label) for g in p.mission_goals] == [("object_located", label)]
    assert {t.action.kind for t in p.tasks} == {"inspect_area"}
    assert all(t.preferred_robot_id == "camera_01" for t in p.tasks)


@pytest.mark.parametrize("cmd,needle", [("What can the gripper car do?", "pick_object"),
                                        ("where is the camera car?", "camera_post"),
                                        ("Which robots do you have?", "lidar_01")])
async def test_baseline_answers_fleet_questions(cmd, needle):
    p = await baseline_propose(cmd)
    assert p.disposition == "answer" and needle in p.answer
    assert not p.tasks and not p.mission_goals and p.scope is None and len(p.answer) <= 600


async def test_baseline_where_is_an_object_is_a_find():
    p = await baseline_propose("Where is the red block?")
    assert p.disposition == "plan" and p.mission_goals[0].kind == "object_located"


@pytest.mark.parametrize("cmd,options", [
    ("find the red box and bring it back to base", ["the drop-off zone"]),
    ("Bring the duck to me", ["the drop-off zone"]),
    ("get the block", ["the red block", "the blue block", "the green block"]),
    ("check on the thing", ["the red block", "the duck", "the traffic cone"]),
    ("Bring the block over here.", ["the red block", "the blue block", "the green block"]),
])
async def test_baseline_clarifies_with_options(cmd, options):
    p = await baseline_propose(cmd)
    assert p.disposition == "clarify" and p.clarification_options == options
    assert not p.tasks


async def test_baseline_destination_question_lists_named_locations():
    goals, issues, question = parse_goals("find the red box and bring it back to base")
    assert not goals and "drop-off zone" in question


async def test_baseline_uses_every_clarification_answer():
    hist = [{"question": "Which object should I move?", "options": [], "answer": "the red block",
             "phase": "initial"},
            {"question": "Where should it go?", "options": [], "answer": "the drop-off zone",
             "phase": "clarification_answer"}]
    p = await baseline_propose("get it", phase="clarification_answer", clarifications=hist,
                               clarification={"question": hist[-1]["question"], "answer": hist[-1]["answer"]})
    assert p.disposition == "plan"
    assert [(g.kind, g.object_query.label, g.location_id) for g in p.mission_goals] == \
        [("object_at", "red_block", "dropoff_zone")]


async def test_baseline_several_instances_offers_ids(make_exec):
    ex = await make_exec("unknown", ["two_red_blocks"])
    await ex.create_mission("Find the red block.")
    await approve_latest(ex)
    await wait_for(ex, DONE)
    ctx = build_context(phase="initial", command="Bring the red block to the drop-off area.", robots=ex.robots,
                        world=ex.world)
    p = (await BaselinePlanner().propose(ctx)).proposal
    assert p.disposition == "clarify" and p.clarification_options == ["red_block_1", "red_block_2"]


# ================================================== WS3: plan rationale

from hive_exec.catalog import ROBOTS  # noqa: E402

RATIONALE = ["gripper_01 is the only robot that picks and places.",
             "It has no camera or LiDAR; lidar_01 scans the corridor first and it drives by dead reckoning."]
RAT_PLAN = dict(PHOTO_FOR_FIND, rationale=RATIONALE)


def latest_dump(ex):
    return ex._serialize_plan(ex.mission, ex.mission.revisions[-1])


async def test_revision_keeps_the_planners_rationale_in_state_and_export(make_exec):
    ex, _ = await stub_exec(make_exec, [RAT_PLAN])
    await ex.create_mission("Find the red block.")
    assert ex.mission.revisions[-1].proposal.rationale == RATIONALE
    state_rev = ex.state()["mission"]["revisions"][-1]
    assert state_rev["proposal"]["rationale"] == RATIONALE
    assert ex.export()["mission"]["revisions"][-1]["proposal"]["rationale"] == RATIONALE
    assert ex.state()["mission"]["last_proposal"]["rationale"] == RATIONALE


async def test_revision_without_rationale_serialises_an_empty_list(make_exec):
    ex, _ = await stub_exec(make_exec, [PHOTO_FOR_FIND])
    await ex.create_mission("Find the red block.")
    assert latest_dump(ex)["proposal"]["rationale"] == []


async def test_edit_keeps_the_base_rationale(make_exec):
    ex, _ = await stub_exec(make_exec, [RAT_PLAN])
    await ex.create_mission("Find the red block.")
    await ex.edit(1, [{"op": "set_param", "task_id": "t1", "field": "area_id", "value": "pickup_zone"}])
    rev2 = ex.mission.revisions[-1]
    assert rev2.kind == "edit" and rev2.proposal.rationale == RATIONALE
    assert ex.state()["mission"]["revisions"][-1]["proposal"]["rationale"] == RATIONALE


async def test_baseline_delivery_in_a_known_world_explains_the_dead_reckoning_drive(make_exec):
    ex = await make_exec("known")
    await ex.create_mission(DEMO)
    p = ex.mission.revisions[-1].proposal
    text = " ".join(p.rationale)
    assert 1 <= len(p.rationale) <= 4 and all(0 < len(i) <= 200 for i in p.rationale)
    assert "gripper_01" in text and "dead reckoning" in text
    assert "lidar_01" in text and "camera_01" in text  # the evidence the drive relies on
    assert not [f for f in ex.mission.revisions[-1].validation.findings if f.code == "missing_rationale"]


async def test_baseline_observation_stage_says_why_sensing_comes_first(make_exec):
    ex = await make_exec("unknown")
    await ex.create_mission(DEMO)
    p = ex.mission.revisions[-1].proposal
    text = " ".join(p.rationale)
    assert p.scope == "observation_stage"
    assert "camera_01" in text and "lidar_01" in text
    assert "gripper_01" in text and "no camera or LiDAR" in text  # why it waits for the stage-1 data
    assert not [f for f in ex.mission.revisions[-1].validation.findings if f.code == "missing_rationale"]


async def test_baseline_sensing_plans_name_the_observer_and_no_gripper(make_exec):
    ex = await make_exec()
    await ex.create_mission("Take a picture of the work area.")
    p = ex.mission.revisions[-1].proposal
    assert p.rationale and "camera_01" in " ".join(p.rationale) and "gripper_01" not in " ".join(p.rationale)


async def test_missing_rationale_shows_as_an_info_finding_for_a_moving_gripper(make_exec):
    carry = dict(PHOTO_FOR_FIND, scope="full",
                 mission_goals=[{"kind": "object_at", "object_query": {"label": "red_block"},
                                 "location_id": "dropoff_zone"}],
                 tasks=[{"task_id": "t1", "binding_id": "carrier", "preferred_robot_id": "gripper_01",
                         "depends_on": [], "action": {"kind": "navigate_to", "location_id": "pickup_zone",
                                                      "route_id": "pickup_corridor"}}])
    ex, _ = await stub_exec(make_exec, [carry], scenario="known")
    await ex.create_mission("Bring the red block to the drop-off zone.")
    rev = ex.mission.revisions[-1]
    (f,) = [f for f in rev.validation.findings if f.code == "missing_rationale"]
    assert f.severity == "info"
    assert ROBOTS["gripper_01"].sensors == ()
