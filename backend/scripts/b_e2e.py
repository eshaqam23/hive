"""Delivery B end-to-end: Executive -> Synapse bridge (@corebrum/core) -> rosbridge -> ROS 2 fake servers.

Prerequisites (Ubuntu 24.04/WSL): ros/run_fake_fleet.sh and node bridge/server.mjs running.

    cd backend && .venv/Scripts/python scripts/b_e2e.py [--bridge ws://127.0.0.1:8790] [--planner baseline]

Planner provenance is recorded per revision. With --planner baseline this verifies the
transport path only, NOT a live LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hive_exec.config import ExecSettings  # noqa: E402
from hive_exec.executive import Executive  # noqa: E402
from hive_exec.gateway.synapse import SynapseGateway  # noqa: E402
from hive_exec.planner.baseline import BaselinePlanner  # noqa: E402
from hive_exec.llm.router import Router  # noqa: E402
from hive_exec.planner.llm import LLMPlanner  # noqa: E402

DEMO = ("Find the red block in the work area, check the pickup corridor, "
        "and bring the block to the drop-off area.")
DONE = {"completed", "blocked", "rejected", "canceled", "planner_error", "answered"}


async def wait(ex, statuses, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if ex.mission and ex.mission.status in statuses:
            return ex.mission.status
        await asyncio.sleep(0.05)
    return f"TIMEOUT({ex.mission.status if ex.mission else None})"


async def approve(ex):
    p = ex.mission.revisions[-1]
    await ex.approve(p.revision, p.proposal_hash)


def summarize(ex, name, expected, final, notes=""):
    m = ex.mission
    attempts = list(ex.attempts.values())
    return {
        "story": name, "expected": expected, "final": final, "pass": final == expected,
        "run_id": ex.run_id, "revisions": [
            {"rev": r.revision, "kind": r.kind, "scope": r.proposal.scope, "planner": r.planner.label,
             "status": r.status, "tasks": [f"{t.task_id}:{t.robot_id}:{t.skill}" for t in r.tasks]}
            for r in m.revisions],
        "attempts": [{"attempt": a.attempt_id, "robot": a.robot_id, "skill": a.envelope["skill"], "status": a.status}
                     for a in attempts],
        "robots_dispatched": sorted({a.robot_id for a in attempts}),
        "outcome": m.outcome, "carried": {r: s.carried_object for r, s in ex.robots.items() if s.carried_object},
        "events_received": len(ex.raw_events), "notes": notes,
        "gateway": vars(ex.gateway.info()),
    }


async def story_unknown(ex):
    await ex.reset("unknown")
    await ex.create_mission(DEMO)
    await approve(ex)
    await wait(ex, {"awaiting_approval"} | DONE)
    if ex.mission.status == "awaiting_approval":
        await approve(ex)
    return summarize(ex, "unknown object/route (two stages)", "completed", await wait(ex, DONE))


async def story_known(ex):
    await ex.reset("known")
    await ex.create_mission(DEMO)
    await approve(ex)
    return summarize(ex, "known-object delivery", "completed", await wait(ex, DONE))


async def story_camera(ex):
    await ex.reset("unknown")
    await ex.create_mission("Take a picture of the work area.")
    await approve(ex)
    return summarize(ex, "camera-only task", "completed", await wait(ex, DONE))


async def story_ambiguous(ex):
    await ex.reset("unknown")
    await ex.create_mission("Bring the block over here.")
    return summarize(ex, "ambiguous request (no dispatch)", "clarification", ex.mission.status)


async def story_failure_recovery(ex):
    await ex.reset("known", ["nav_fault_after_pickup"])
    await ex.create_mission(DEMO)
    await approve(ex)
    await wait(ex, {"awaiting_approval"} | DONE)
    held = ex.robots["gripper_01"].carried_object
    if ex.mission.status == "awaiting_approval":
        await approve(ex)
    return summarize(ex, "failure after pickup -> approved recovery", "completed", await wait(ex, DONE),
                     notes=f"possession before recovery: {held}")


async def story_failure_block(ex):
    await ex.reset("known", ["gripper_fault_after_pickup"])
    await ex.create_mission(DEMO)
    await approve(ex)
    return summarize(ex, "failure after pickup -> honest blocked outcome", "blocked", await wait(ex, DONE))


async def story_cancel(ex):
    await ex.reset("known", ["hang_navigate"])
    await ex.create_mission(DEMO)
    await approve(ex)
    for _ in range(100):
        if any(a.status in ("accepted", "running") for a in ex.attempts.values()):
            break
        await asyncio.sleep(0.05)
    await ex.cancel()
    shown_before_confirm = ex.mission.status
    final = await wait(ex, DONE, 20)
    return summarize(ex, "targeted cancellation confirmed by ROS", "canceled", final,
                     notes=f"status right after cancel request: {shown_before_confirm}")


async def story_task_stop(ex):
    """Stop one task (the hung route scan) while the camera keeps working; recovery is proposed."""
    await ex.reset("unknown", ["hang_scan"])
    await ex.create_mission(DEMO)
    rev = ex.mission.revisions[-1]
    scan_task = next(t.task_id for t in rev.tasks if t.skill == "scan_route")
    await approve(ex)
    for _ in range(200):
        live = {a.robot_id: a for a in ex.attempts.values() if a.status in ("accepted", "running")}
        if "lidar_01" in live and "camera_01" in live:
            break
        await asyncio.sleep(0.01)
    rt = await ex.cancel_task(scan_task)
    shown = rt.state
    scan = next(a for a in ex.attempts.values() if a.robot_id == "lidar_01")
    camera = next(a for a in ex.attempts.values() if a.robot_id == "camera_01")
    for _ in range(400):
        if scan.status in ("canceled", "failed", "uncertain", "succeeded"):
            break
        await asyncio.sleep(0.01)
    camera_when_confirmed = camera.status
    final = await wait(ex, {"awaiting_approval"} | DONE, 30)
    ok = (shown == "canceling" and scan.status == "canceled" and camera.status == "succeeded"
          and ex.mission.recovery_used and ex.mission.revisions[-1].kind == "recovery")
    row = summarize(ex, "stop one task; others keep running; recovery proposed",
                    "awaiting_approval", final if ok else f"{final} (checks failed)",
                    notes=f"{scan_task} right after stop: {shown}; scan confirmed {scan.status} while camera was "
                          f"{camera_when_confirmed}; camera finished {camera.status}; "
                          f"command files: {[a.command_file for a in ex.attempts.values()]}")
    await ex.cancel()  # leave nothing awaiting approval
    return row


async def story_uncertain(ex):
    await ex.reset("known", ["uncertain_place"])
    await ex.create_mission(DEMO)
    await approve(ex)
    return summarize(ex, "uncertain place blocks without retry", "blocked", await wait(ex, DONE))


async def story_map(ex):
    """A8: lidar_01 maps the arena; the run keeps one map record (fake fleet: synthetic map pair)."""
    await ex.reset("unknown")
    await ex.create_mission("Map the arena.")
    await approve(ex)
    final = await wait(ex, DONE, 120)
    rec = ex.world.latest_map("arena")
    ok = rec is not None and rec.robot_id == "lidar_01" and rec.map_ref.get("sha256")
    return summarize(ex, "map the arena (map_area, one map record)", "completed",
                     final if ok else f"{final} (no map record)",
                     notes=f"map record: {None if rec is None else {'coverage': rec.coverage, 'map_ref': rec.map_ref}}")


async def story_map_incomplete(ex):
    """A8: COVERAGE_INCOMPLETE twice (one recovery) blocks honestly and leaves no map record."""
    await ex.reset("unknown", ["map_incomplete"])
    await ex.create_mission("Map the arena.")
    await approve(ex)
    await wait(ex, {"awaiting_approval"} | DONE, 120)
    if ex.mission.status == "awaiting_approval":
        await approve(ex)
    final = await wait(ex, DONE, 120)
    codes = [(rt.error or {}).get("code") for rt in ex.mission.tasks.values()]
    ok = ex.world.latest_map("arena") is None and codes == ["COVERAGE_INCOMPLETE", "COVERAGE_INCOMPLETE"]
    return summarize(ex, "partial map -> recovery -> honest blocked outcome", "blocked",
                     final if ok else f"{final} (checks failed)", notes=f"error codes: {codes}")


async def story_object_held(ex):
    """A8: "Pick up the red block." finds it, then the gripper picks it and keeps it (no place)."""
    await ex.reset("unknown")
    await ex.create_mission("Pick up the red block.")
    await approve(ex)
    await wait(ex, {"awaiting_approval"} | DONE)
    if ex.mission.status == "awaiting_approval":
        await approve(ex)
    final = await wait(ex, DONE)
    skills = [a.envelope["skill"] for a in ex.attempts.values()]
    held = ex.robots["gripper_01"].carried_object
    ok = skills[-1:] == ["pick_object"] and "place_object" not in skills and held is not None
    return summarize(ex, "pick up (object_held: find, then pick, no place)", "completed",
                     final if ok else f"{final} (checks failed)", notes=f"held by gripper_01: {held}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default=ExecSettings.from_env().bridge_url)
    ap.add_argument("--planner", choices=["baseline", "llm", "qwen"], default="baseline")  # qwen = llm
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "docs" / "evidence" / "b-e2e.json"))
    args = ap.parse_args()
    settings = ExecSettings.from_env()
    gw = SynapseGateway(args.bridge)
    planners = {"baseline": BaselinePlanner(), "llm": LLMPlanner(Router(legacy=settings.qwen))}
    ex = Executive(gw, planners, default_planner=args.planner, runs_dir=settings.runs_dir)
    await ex.start()
    if not gw.connected:
        print(f"Synapse bridge not connected at {args.bridge}")
        sys.exit(2)
    print("gateway:", vars(gw.info()))
    rows = []
    for story in (story_unknown, story_known, story_camera, story_ambiguous, story_failure_recovery,
                  story_failure_block, story_cancel, story_task_stop, story_uncertain,
                  story_map, story_map_incomplete, story_object_held):
        row = await story(ex)
        rows.append(row)
        print(f"{'PASS' if row['pass'] else 'FAIL'}  {row['story']}: {row['final']}  "
              f"robots={row['robots_dispatched']} events={row['events_received']}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                          "planner": args.planner, "results": rows}, indent=2, default=str))
    print(f"{sum(r['pass'] for r in rows)}/{len(rows)} passed; wrote {args.out}")
    await gw.stop()


if __name__ == "__main__":
    asyncio.run(main())
