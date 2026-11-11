"""Baseline planner: fixed delivery/inspection/scan templates.

Development aid only. Every proposal it makes is labeled "Baseline" and it is
never substituted for Qwen automatically. It reads the same observed-world
context as Qwen (no simulator truth).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..context import PlanningContext
from ..contracts import PlanProposal, PlannerRecord
from .base import Planner, PlannerOutput

LABELS = {"red": "red_block", "blue": "blue_block", "green": "green_block"}
UNSUPPORTED = re.compile(r"\b(fly|drone|cook|paint|swim|climb|stairs|weld|charge|dance|open the door|elevator)\b")


def _p(**kw: Any) -> PlanProposal:
    base = {"schema_version": "hive.plan-proposal.v1", "mission_goals": [], "stage_goals": [], "tasks": [],
            "issues": [], "clarification_question": None, "scope": None}
    base.update(kw)
    return PlanProposal.model_validate(base)


def _task(tid, binding, action, deps=(), robot=None):
    return {"task_id": tid, "binding_id": binding, "action": action, "depends_on": list(deps),
            "preferred_robot_id": robot}


def parse_goals(text: str) -> tuple[list[dict], list[str], Optional[str]]:
    """Return (goals, issues, clarification_question) from English text."""
    t = text.lower()
    if UNSUPPORTED.search(t):
        return [], [f"unsupported request: '{UNSUPPORTED.search(t).group(0)}' has no matching skill"], None
    label = next((v for k, v in LABELS.items() if re.search(rf"\b{k}\b", t)), None)
    deliver = re.search(r"\b(bring|deliver|carry|move|take|transport|fetch)\b", t) and re.search(r"\b(block|object|it)\b", t)
    photo = re.search(r"\b(picture|photo|image|snapshot)\b", t)
    find = re.search(r"\b(find|locate|look for|search|inspect|where)\b", t)
    scan = re.search(r"\b(scan|check|survey)\b", t) and re.search(r"\b(corridor|route|path)\b", t)
    dest = ("dropoff_zone" if re.search(r"drop[- ]?off", t) else
            "pickup_zone" if re.search(r"\bpick[- ]?up (zone|area)\b", t) and not deliver else None)
    area = "pickup_zone" if re.search(r"pick[- ]?up (zone|area)", t) and (photo or find) and not deliver else "work_area"

    if deliver and not photo:
        if not label:
            return [], ["no block label given"], "Which block should I move: red, blue or green?"
        if not dest:
            return [], ["destination is not a named location"], "Where should the block go? (e.g. the drop-off area)"
        return [{"kind": "object_at", "object_query": {"label": label}, "location_id": dest}], [], None
    goals: list[dict] = []
    if photo:
        goals.append({"kind": "image_captured", "area_id": area})
    if find:
        if not label:
            return [], ["no object label given"], "Which object should I look for: red, blue or green block?"
        goals.append({"kind": "object_located", "object_query": {"label": label}, "area_id": area})
    if scan:
        goals.append({"kind": "route_observed", "route_id": "pickup_corridor"})
    if not goals:
        return [], ["no supported intent recognized (deliver, inspect, photograph, scan)"], None
    return goals, [], None


def plan_for_goals(goals: list[dict], ctx: PlanningContext) -> PlanProposal:
    objects = [o for o in ctx.observed["objects"] if o["fresh"] or o["held_by"]]
    routes = ctx.observed["routes"]
    robots = {r["robot_id"]: r for r in ctx.robots}
    absences = ctx.observed["absences"]

    deliveries = [g for g in goals if g["kind"] == "object_at"]
    if deliveries:
        g = deliveries[0]
        label, dest = g["object_query"]["label"], g["location_id"]
        carrier = robots["gripper_01"]
        held = [o for o in objects if o["label"] == label and o["held_by"]]
        free = [o for o in objects if o["label"] == label and not o["held_by"]]
        at_dest = [o for o in free if o["location_id"] == dest]
        if at_dest and not held:
            return _p(disposition="reject", summary=f"{label} is already at {dest}.", mission_goals=goals,
                      issues=["goal already satisfied"])
        if not carrier["available"]:
            return _p(disposition="reject", summary="The gripper robot is unavailable; no carrier can transport.",
                      mission_goals=goals, issues=["gripper_01 unavailable"])
        answer = (ctx.clarification or {}).get("answer", "").lower()
        chosen = [o for o in free if o["object_id"] in answer]
        if len(free) > 1 and len(chosen) == 1:
            free = chosen
        if len(free) > 1 and not held:
            ids = ", ".join(o["object_id"] for o in free)
            return _p(disposition="clarify", summary=f"Several {label} instances observed.", mission_goals=goals,
                      issues=[f"ambiguous: {ids}"], clarification_question=f"Which one should I move: {ids}?")
        if not free and not held and any(a["label"] == label for a in absences):
            return _p(disposition="reject", summary=f"No {label} was found where it was inspected.",
                      mission_goals=goals, issues=["object not found"])
        route = routes.get("pickup_corridor", "unknown")
        if route == "blocked":
            return _p(disposition="reject", summary="The pickup corridor is observed blocked.", mission_goals=goals,
                      issues=["route blocked"])
        target = held[0] if held else (free[0] if free else None)
        if target is None or route != "clear":
            tasks, stage = [], []
            if target is None:
                tasks.append(_task("t1", "observer", {"kind": "inspect_area", "area_id": "work_area",
                                                       "object_query": {"label": label}}, robot="camera_01"))
                stage.append({"kind": "object_located", "object_query": {"label": label}, "area_id": "work_area"})
            if route != "clear":
                tasks.append(_task(f"t{len(tasks) + 1}", "surveyor", {"kind": "scan_route", "route_id": "pickup_corridor"},
                                   robot="lidar_01"))
                stage.append({"kind": "route_observed", "route_id": "pickup_corridor"})
            return _p(disposition="plan", scope="observation_stage",
                      summary="Gather missing evidence before transport.", mission_goals=goals, stage_goals=stage,
                      tasks=tasks)
        oid = target["object_id"]
        tasks, loc, n = [], carrier["location_id"], 0

        def add(action):
            nonlocal n
            n += 1
            tasks.append(_task(f"t{n}", "carrier", action, [f"t{n - 1}"] if n > 1 else [], "gripper_01"))

        if not held:
            if loc != target["location_id"]:
                add({"kind": "navigate_to", "location_id": target["location_id"], "route_id": "pickup_corridor"})
                loc = target["location_id"]
            add({"kind": "pick_object", "object_id": oid})
        if loc != dest:
            add({"kind": "navigate_to", "location_id": dest, "route_id": "pickup_corridor"})
        add({"kind": "place_object", "object_id": oid, "location_id": dest})
        return _p(disposition="plan", scope="full", summary=f"Carry {oid} to {dest}.", mission_goals=goals,
                  tasks=tasks)

    tasks, n = [], 0
    for g in goals:
        n += 1
        if g["kind"] == "image_captured":
            tasks.append(_task(f"t{n}", f"observer{n}" if n > 1 else "observer",
                               {"kind": "capture_image", "area_id": g["area_id"]}, robot="camera_01"))
        elif g["kind"] == "object_located":
            tasks.append(_task(f"t{n}", "observer", {"kind": "inspect_area", "area_id": g["area_id"],
                                                      "object_query": g["object_query"]}, robot="camera_01"))
        elif g["kind"] == "route_observed":
            tasks.append(_task(f"t{n}", "surveyor", {"kind": "scan_route", "route_id": g["route_id"]}, robot="lidar_01"))
    # camera tasks share one robot: chain them
    cam = [t for t in tasks if t["preferred_robot_id"] == "camera_01"]
    for prev, cur in zip(cam, cam[1:]):
        cur["binding_id"] = prev["binding_id"]
        cur["depends_on"] = [prev["task_id"]]
    return _p(disposition="plan", scope="full", summary="Run the requested observations.", mission_goals=goals,
              tasks=tasks)


class BaselinePlanner(Planner):
    kind = "baseline"

    def status(self) -> dict:
        return {"kind": "baseline", "label": "Baseline (templates, not Qwen)", "live_enabled": True}

    async def propose(self, ctx: PlanningContext, repair_feedback=None, previous_output=None) -> PlannerOutput:
        record = PlannerRecord(kind="baseline", label="Baseline (templates, not Qwen)", latency_ms=0.0)
        if ctx.canonical_goals and ctx.phase in ("continuation", "recovery"):
            goals = ctx.canonical_goals
        else:
            text = ctx.command
            if ctx.clarification:
                text += " " + ctx.clarification.get("answer", "")
            goals, issues, question = parse_goals(text)
            if question:
                return PlannerOutput(_p(disposition="clarify", summary="The request is ambiguous.", issues=issues,
                                        clarification_question=question), record)
            if not goals:
                return PlannerOutput(_p(disposition="reject", summary="The fleet cannot perform this request.",
                                        issues=issues), record)
        return PlannerOutput(plan_for_goals(goals, ctx), record)
