"""Prompt evaluation: ten labelled operator commands against the real LLM planner (WS2).

    cd backend
    HIVE_PROVIDERS_FILE=/path/to/providers.yaml .venv/bin/python scripts/prompt_eval.py --runs 3 --seed 1234

The provider store must be named explicitly (``--providers`` or ``$HIVE_PROVIDERS_FILE``); the
``executive`` role's chain is used, as in the app. Each command runs through the Executive on the
fake fleet (validation and the one schema repair included, nothing approved or dispatched). Run r
uses sampling seed ``--seed + r - 1`` so repeated runs sample differently but reproducibly.

Prints one table row per (run, case) and a summary line; exits 0 when every row passes, 1 when
any fails, 2 when the store or the provider is unusable. API keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hive_exec.executive import Executive  # noqa: E402
from hive_exec.gateway.fake import FakeGateway  # noqa: E402
from hive_exec.llm.router import Candidate, Router  # noqa: E402
from hive_exec.planner.baseline import BaselinePlanner  # noqa: E402
from hive_exec.planner.llm import LLMPlanner  # noqa: E402
from hive_exec.planner.prompt import PROMPT_VERSION  # noqa: E402

ROLE = "executive"
GRIPPER_ACTIONS = {"pick_object", "place_object"}

# goals: (kind, label or None, area/location or None) that mission_goals must contain.
CASES: list[dict[str, Any]] = [
    {"id": "find-red-box", "command": "Find a red box in the feild", "disposition": "plan",
     "goals": [("object_located", "red_block", None)], "no_gripper": True, "no_scan": True},
    {"id": "find-duck", "command": "find me the duck in the feild", "disposition": "plan",
     "goals": [("object_located", "duck", None)]},
    {"id": "fetch-to-base", "command": "find the red box and bring it back to base", "disposition": "clarify"},
    {"id": "fetch-to-dropoff", "command": "find the red box and bring it to the drop-off zone",
     "disposition": "plan", "scope": "observation_stage", "goals": [("object_at", "red_block", "dropoff_zone")]},
    {"id": "fleet-question", "command": "What can the gripper car do?", "disposition": "answer"},
    {"id": "photo", "command": "Take a picture of the work area", "disposition": "plan",
     "goals": [("image_captured", None, "work_area")]},
    {"id": "block-over-here", "command": "Bring the block over here", "disposition": "clarify"},
    {"id": "fly-drone", "command": "Fly the drone over the warehouse", "disposition": "reject"},
    {"id": "get-the-block", "command": "get the block", "disposition": "clarify"},
    {"id": "check-the-thing", "command": "check on the thing", "disposition": "clarify"},
]


class SeededRouter(Router):
    """The app's router with the sampling seed of every candidate row overridden."""

    def __init__(self, *args: Any, seed: Optional[int] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.seed = seed

    def chain(self, role: str) -> list:
        cands = super().chain(role)
        if self.seed is None:
            return cands
        return [Candidate(c.row.model_copy(update={"sampling": c.row.sampling.model_copy(
            update={"seed": self.seed})}), c.key) for c in cands]


def _goal_matches(goal: dict, kind: str, label: Optional[str], place: Optional[str]) -> bool:
    if goal.get("kind") != kind:
        return False
    if label is not None and (goal.get("object_query") or {}).get("label") != label:
        return False
    return place is None or place in (goal.get("area_id"), goal.get("location_id"))


def check(case: dict, proposal: dict, valid: bool) -> list[str]:
    """Every broken expectation of ``case`` in ``proposal`` (empty: pass)."""
    disposition = proposal.get("disposition")
    if disposition != case["disposition"]:
        return [f"expected {case['disposition']}, got {disposition}"]
    notes = []
    if disposition == "plan" and not valid:
        notes.append("plan failed backend validation")
    if "scope" in case and proposal.get("scope") != case["scope"]:
        notes.append(f"expected scope {case['scope']}, got {proposal.get('scope')}")
    goals = proposal.get("mission_goals") or []
    for kind, label, place in case.get("goals", []):
        if not any(_goal_matches(g, kind, label, place) for g in goals):
            notes.append(" ".join(["missing mission goal", kind] + [x for x in (label, place) if x]))
    tasks = proposal.get("tasks") or []
    if case.get("no_gripper") and any((t.get("action") or {}).get("kind") in GRIPPER_ACTIONS
                                      or t.get("preferred_robot_id") == "gripper_01" for t in tasks):
        notes.append("gripper task in a find-only plan")
    if case.get("no_scan") and any((t.get("action") or {}).get("kind") == "scan_route" for t in tasks):
        notes.append("scan_route in a find-only plan")
    return notes


async def run_case(ex: Executive, case: dict, run: int) -> dict:
    await ex.reset("unknown", [])
    t0 = time.perf_counter()
    await ex.create_mission(case["command"], "llm")
    m = ex.mission
    proposal = m.last_proposal or {}
    rec = (m.planning_log[0].get("planner") or {}) if m.planning_log else {}
    if m.status == "planner_error":
        outcome, notes = "planner_error", [f"planner_error: {(m.outcome or {}).get('reason', '')}"]
    else:
        valid = bool(m.revisions and m.revisions[0].validation.ok)
        scope = proposal.get("scope")
        outcome = f"{proposal.get('disposition')}" + (f"/{scope}" if scope else "")
        notes = check(case, proposal, valid)
    return {"case": case["id"], "command": case["command"], "run": run, "outcome": outcome,
            "ok": not notes, "note": "; ".join(notes)[:120], "latency_ms": rec.get("latency_ms"),
            "repaired": rec.get("repaired"), "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def evaluate(planner: Any, cases: list[dict] = CASES, runs: int = 1,
                   before_run: Optional[Callable[[int], None]] = None) -> list[dict]:
    """Each case ``runs`` times through an Executive whose "llm" planner is ``planner``."""
    ex = Executive(FakeGateway(time_scale=0.01), {"llm": planner, "baseline": BaselinePlanner()},
                   default_planner="llm")
    await ex.gateway.start(ex.handle_event)
    rows = []
    try:
        for run in range(1, runs + 1):
            if before_run:
                before_run(run)
            for case in cases:
                rows.append(await run_case(ex, case, run))
    finally:
        await ex.gateway.stop()
    return rows


def table(rows: list[dict]) -> str:
    lines = [f"{'case':<18} {'run':>3}  {'outcome':<26} {'result':<6} {'latency_ms':>10}  note"]
    for r in rows:
        lat = "" if r.get("latency_ms") is None else f"{r['latency_ms']:.0f}"
        lines.append(f"{r['case']:<18} {r['run']:>3}  {r['outcome']:<26} {'PASS' if r['ok'] else 'FAIL':<6} "
                     f"{lat:>10}  {r['note']}")
    passed = sum(r["ok"] for r in rows)
    lines.append(f"prompt_eval {PROMPT_VERSION}: {passed}/{len(rows)} passed")
    return "\n".join(lines)


def _scrub(text: str, router: Router) -> str:
    """Defence in depth: no candidate key reaches stdout even if a provider echoes it."""
    try:
        keys = [c.key for c in router.chain(ROLE) if c.key]
    except Exception:  # noqa: BLE001 - an unreadable store has no keys to hide
        keys = []
    for key in keys:
        text = text.replace(key, "***")
    return text


async def run(argv: Optional[list[str]] = None, transport: Any = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate the planner prompt against the live executive role.")
    ap.add_argument("--providers", help="provider store (default: $HIVE_PROVIDERS_FILE)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None, help="sampling seed of run 1 (default: the row's own)")
    args = ap.parse_args(argv)
    path = args.providers or os.environ.get("HIVE_PROVIDERS_FILE")
    if not path:
        print("No provider store: pass --providers or set HIVE_PROVIDERS_FILE (role 'executive').")
        return 2
    router = SeededRouter(path=path, transport=transport, seed=args.seed)
    planner = LLMPlanner(router, role=ROLE)
    probe = await planner.probe()
    if not probe["ok"]:
        print(_scrub(f"LLM probe failed: {probe['error']}\nNo evaluation was run.", router))
        return 2

    def before_run(run_no: int) -> None:
        if args.seed is not None:
            router.seed = args.seed + run_no - 1

    rows = await evaluate(planner, CASES, max(1, args.runs), before_run)
    status = planner.status()
    head = (f"provider {status.get('provider')} model {status.get('model')} runs {max(1, args.runs)} "
            f"seed {args.seed if args.seed is not None else 'row default'}")
    print(_scrub(head + "\n" + table(rows), router))
    return 0 if all(r["ok"] for r in rows) else 1


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    sys.exit(main())
