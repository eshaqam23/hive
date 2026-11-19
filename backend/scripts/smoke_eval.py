"""Smoke evaluation: ~10 labeled commands, each run twice (spec 11).

    cd backend
    .venv/Scripts/python scripts/smoke_eval.py               # live Qwen from .env (QWEN_BASE_URL)
    .venv/Scripts/python scripts/smoke_eval.py --planner baseline   # dry run of the harness (labeled Baseline)
    .venv/Scripts/python scripts/smoke_eval.py --execute     # also auto-approve valid revisions on the fake fleet

This is a smoke evaluation, not a reliability benchmark. Results are labeled with
the planner that actually produced them; a Baseline run is never reported as Qwen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hive_exec.config import ExecSettings  # noqa: E402
from hive_exec.executive import Executive  # noqa: E402
from hive_exec.gateway.fake import FakeGateway  # noqa: E402
from hive_exec.planner.baseline import BaselinePlanner  # noqa: E402
from hive_exec.planner.qwen import QwenPlanner  # noqa: E402

DEMO = ("Find the red block in the work area, check the pickup corridor, "
        "and bring the block to the drop-off area.")

# (label, scenario, faults, command, acceptable initial outcomes "disposition[/scope]")
CASES = [
    ("demo-unknown", "unknown", [], DEMO, {"plan/observation_stage"}),
    ("demo-known", "known", [], DEMO, {"plan/full"}),
    ("camera-photo", "unknown", [], "Take a picture of the work area.", {"plan/full"}),
    ("camera-find", "unknown", [], "Find the red block in the work area.", {"plan/full", "plan/observation_stage"}),
    ("lidar-scan", "unknown", [], "Check whether the pickup corridor is clear.", {"plan/full", "plan/observation_stage"}),
    ("ambiguous-block", "unknown", [], "Bring the block over here.", {"clarify"}),
    ("unsupported-fly", "unknown", [], "Fly the drone over the warehouse.", {"reject"}),
    ("unsupported-paint", "unknown", [], "Paint the work area red.", {"reject"}),
    ("blue-delivery", "unknown", [], "Bring the blue block to the drop-off area.", {"plan/observation_stage"}),
    ("gripper-down", "known", ["gripper_unavailable"], DEMO, {"reject", "clarify", "plan/full"}),
]


async def wait_settle(ex: Executive, timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if ex.mission.status not in ("executing", "evaluating", "planning", "recovering", "canceling"):
            return
        await asyncio.sleep(0.02)


async def run_case(ex: Executive, planner_kind: str, case, execute: bool) -> dict:
    label, scenario, faults, command, expected = case
    await ex.reset(scenario, faults)
    t0 = time.perf_counter()
    await ex.create_mission(command, planner_kind)
    m = ex.mission
    first = m.planning_log[0] if m.planning_log else {}
    rec = (first.get("planner") or {})
    if m.status == "planner_error":
        outcome = "planner_error"
    elif m.revisions:
        outcome = f"plan/{m.revisions[0].proposal.scope}"
    else:
        outcome = (m.last_proposal or {}).get("disposition", m.status)
    row = {
        "case": label, "command": command, "scenario": scenario, "faults": faults,
        "outcome": outcome, "expected": sorted(expected), "match": outcome in expected,
        "valid": bool(m.revisions and m.revisions[0].validation.ok) if m.revisions else None,
        "first_pass_ok": rec.get("first_pass_ok"), "repaired": rec.get("repaired"),
        "attempts": rec.get("attempts"), "latency_ms": rec.get("latency_ms"),
        "planner_label": rec.get("label"), "model": rec.get("model"),
        "error": (m.outcome or {}).get("reason") if m.status == "planner_error" else None,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    if execute:
        for _ in range(4):
            if m.status != "awaiting_approval" or not m.revisions[-1].validation.ok:
                break
            p = m.revisions[-1]
            await ex.approve(p.revision, p.proposal_hash)  # auto-approved for evaluation only
            await wait_settle(ex)
        row["final_status"] = m.status
        row["revisions"] = len(m.revisions)
        row["dispatched_robots"] = sorted({a.robot_id for a in ex.attempts.values()})
    return row


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--planner", choices=["qwen", "baseline"], default="qwen")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "reports"))
    args = ap.parse_args()

    settings = ExecSettings.from_env()
    planners = {"qwen": QwenPlanner(settings.qwen), "baseline": BaselinePlanner()}
    ex = Executive(FakeGateway(time_scale=0.01), planners, default_planner=args.planner)
    await ex.gateway.start(ex.handle_event)
    if args.planner == "qwen":
        probe = await planners["qwen"].probe()
        if not probe.ok:
            print(f"Qwen probe failed: {probe.error}\nNo live evaluation was run.")
            sys.exit(2)
    rows = []
    for rep in range(args.repeats):
        for case in CASES:
            row = await run_case(ex, args.planner, case, args.execute)
            row["repeat"] = rep + 1
            rows.append(row)
            print(f"[{rep + 1}] {row['case']:<18} {row['outcome']:<24} match={row['match']!s:<5} "
                  f"first_pass={row['first_pass_ok']} repaired={row['repaired']} latency={row['latency_ms']}ms"
                  + (f" final={row.get('final_status')}" if args.execute else ""))
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    summary = {
        "planner": args.planner, "label": rows[0]["planner_label"] if rows else None,
        "model": settings.qwen.model if args.planner == "qwen" else None,
        "endpoint": settings.qwen.base_url if args.planner == "qwen" else None,
        "settings": planners["qwen"].settings_record() if args.planner == "qwen" else None,
        "runs": len(rows), "matches": sum(r["match"] for r in rows),
        "planner_errors": sum(r["outcome"] == "planner_error" for r in rows),
        "first_pass_ok": sum(bool(r["first_pass_ok"]) for r in rows),
        "repaired": sum(bool(r["repaired"]) for r in rows),
        "latency_ms_median": statistics.median(lat) if lat else None,
        "latency_ms_max": max(lat) if lat else None,
        "note": "Smoke evaluation, not a reliability benchmark.",
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"smoke-{args.planner}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"wrote {path}")
    await ex.gateway.stop()


if __name__ == "__main__":
    asyncio.run(main())
