"""Delivery B end-to-end: Executive -> Synapse bridge (@corebrum/core) -> rosbridge -> ROS 2 fake servers.

Prerequisites (Ubuntu 24.04/WSL): ros/run_fake_fleet.sh and node bridge/server.mjs running.

    cd backend && .venv/Scripts/python scripts/b_e2e.py [--bridge ws://127.0.0.1:8790] [--planner baseline]

Planner provenance is recorded per revision. With --planner baseline this verifies the
transport path only, NOT live Qwen.
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
from hive_exec.planner.qwen import QwenPlanner  # noqa: E402

DEMO = ("Find the red block in the work area, check the pickup corridor, "
        "and bring the block to the drop-off area.")
DONE = {"completed", "blocked", "rejected", "canceled", "planner_error"}


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


async def story_uncertain(ex):
    await ex.reset("known", ["uncertain_place"])
    await ex.create_mission(DEMO)
    await approve(ex)
    return summarize(ex, "uncertain place blocks without retry", "blocked", await wait(ex, DONE))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default=ExecSettings.from_env().bridge_url)
    ap.add_argument("--planner", choices=["baseline", "qwen"], default="baseline")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "docs" / "evidence" / "b-e2e.json"))
    args = ap.parse_args()
    settings = ExecSettings.from_env()
    gw = SynapseGateway(args.bridge)
    planners = {"baseline": BaselinePlanner(), "qwen": QwenPlanner(settings.qwen)}
    ex = Executive(gw, planners, default_planner=args.planner)
    await ex.start()
    if not gw.connected:
        print(f"Synapse bridge not connected at {args.bridge}")
        sys.exit(2)
    print("gateway:", vars(gw.info()))
    rows = []
    for story in (story_unknown, story_known, story_camera, story_ambiguous, story_failure_recovery,
                  story_failure_block, story_cancel, story_uncertain):
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
