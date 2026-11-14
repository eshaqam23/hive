"""Five acceptance stories (Baseline planner, fake fleet) plus robustness checks.

These prove the deterministic pipeline. They do NOT count as live-Qwen acceptance.
"""

import json

import pytest

from hive_exec.context import build_context
from hive_exec.contracts import TaskEvent
from hive_exec.executive import ExecError

from .conftest import DEMO, approve_latest, wait_for

DONE = {"completed", "blocked", "rejected", "canceled", "planner_error"}


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
