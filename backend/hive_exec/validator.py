"""Deterministic plan validation and effect projection.

Projection follows dependencies: a pick can establish possession for later
tasks; an unexecuted scan can never establish route clearance (only accepted
observations in the observed world can).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .allocator import Allocation, binding_tasks
from .catalog import BATTERY_RESERVE_PCT, OBJECT_PROFILES, ROBOTS, SKILLS, route_between
from .contracts import (
    AllocatedTask,
    Goal,
    PlanProposal,
    TaskSpec,
    ValidationFinding,
    ValidationReport,
)
from .world import ObservedWorld, RobotState


@dataclass
class ValidationResult:
    report: ValidationReport
    tasks: list[AllocatedTask]


def _closure(tasks: dict[str, TaskSpec]) -> dict[str, set[str]]:
    memo: dict[str, set[str]] = {}

    def visit(tid: str, stack: frozenset) -> set[str]:
        if tid in memo:
            return memo[tid]
        out: set[str] = set()
        for d in tasks[tid].depends_on:
            if d in tasks and d not in stack:
                out.add(d)
                out |= visit(d, stack | {d})
        memo[tid] = out
        return out

    return {tid: visit(tid, frozenset({tid})) for tid in tasks}


def topo_order(proposal: PlanProposal) -> Optional[list[TaskSpec]]:
    """Kahn's algorithm with list order as tie-break. None if there is a cycle."""
    tasks = {t.task_id: t for t in proposal.tasks}
    order_index = {t.task_id: i for i, t in enumerate(proposal.tasks)}
    indeg = {tid: sum(1 for d in t.depends_on if d in tasks) for tid, t in tasks.items()}
    ready = sorted([tid for tid, n in indeg.items() if n == 0], key=order_index.get)
    out: list[TaskSpec] = []
    while ready:
        tid = ready.pop(0)
        out.append(tasks[tid])
        for other in proposal.tasks:
            if tid in other.depends_on:
                indeg[other.task_id] -= 1
                if indeg[other.task_id] == 0:
                    ready.append(other.task_id)
                    ready.sort(key=order_index.get)
    return out if len(out) == len(tasks) else None


def goals_equal(a: list[Goal], b: list[Goal]) -> bool:
    return [g.model_dump() for g in a] == [g.model_dump() for g in b]


def validate(
    proposal: PlanProposal,
    allocation: Allocation,
    robots: dict[str, RobotState],
    world: ObservedWorld,
    canonical_goals: Optional[list[Goal]] = None,
    operator_constraints: Optional[dict[str, str]] = None,
) -> ValidationResult:
    findings: list[ValidationFinding] = []
    operator_constraints = operator_constraints or {}

    def err(code, msg, task_id=None, binding_id=None, severity="error"):
        findings.append(ValidationFinding(code=code, severity=severity, message=msg,
                                          task_id=task_id, binding_id=binding_id))

    tasks = {t.task_id: t for t in proposal.tasks}

    # --- goal preservation
    if canonical_goals is not None and not goals_equal(proposal.mission_goals, canonical_goals):
        err("mission_goals_changed",
            "Proposal changed the approved mission goals; the canonical goals are kept. "
            "Changing a goal requires an operator edit.", severity="warning")

    # --- dependency structure
    for t in proposal.tasks:
        for d in t.depends_on:
            if d not in tasks:
                err("missing_dependency", f"{t.task_id} depends on unknown task {d}", t.task_id)
    order = topo_order(proposal)
    if order is None:
        err("dependency_cycle", "Task dependencies contain a cycle.")
        order = list(proposal.tasks)
    closure = _closure(tasks)

    # --- allocation and robot checks
    for binding, btasks in binding_tasks(proposal).items():
        rid = allocation.robots.get(binding)
        if rid is None:
            why = "; ".join(f"{r}: {w}" for r, w in allocation.ineligible.get(binding, {}).items())
            err("no_eligible_robot", f"No eligible robot for binding '{binding}' ({why}).", binding_id=binding)
            continue
        if rid not in ROBOTS:
            err("unknown_robot", f"Unknown robot {rid}", binding_id=binding)
            continue
        inel = allocation.ineligible.get(binding, {}).get(rid)
        if inel:
            err("unsupported_assignment", f"{rid} cannot perform binding '{binding}': {inel}", binding_id=binding)
        state = robots.get(rid)
        if state and (not state.available or state.health != "ok"):
            err("robot_unavailable", f"{rid} is unavailable (health={state.health})", binding_id=binding)
        prefs = {t.preferred_robot_id for t in btasks if t.preferred_robot_id}
        if prefs and rid not in prefs and binding not in operator_constraints:
            err("preference_overridden", f"Model preferred {sorted(prefs)} for '{binding}'; allocated {rid} "
                f"({allocation.reasons.get(binding, '')}).", binding_id=binding, severity="info")
        # movement order: each task of a binding must follow the previous one
        for prev, cur in zip(btasks, btasks[1:]):
            if prev.task_id not in closure.get(cur.task_id, set()):
                err("movement_order", f"{cur.task_id} must depend on {prev.task_id} (same binding '{binding}').",
                    cur.task_id, binding)

    # one active task per robot: tasks sharing a robot must be ordered
    by_robot: dict[str, list[TaskSpec]] = {}
    for t in proposal.tasks:
        rid = allocation.robots.get(t.binding_id)
        if rid:
            by_robot.setdefault(rid, []).append(t)
    for rid, rtasks in by_robot.items():
        for i, a in enumerate(rtasks):
            for b in rtasks[i + 1:]:
                if a.task_id not in closure.get(b.task_id, set()) and b.task_id not in closure.get(a.task_id, set()):
                    err("resource_conflict", f"{a.task_id} and {b.task_id} both use {rid} without an ordering dependency.",
                        b.task_id)

    # --- projection
    proj_loc = {rid: s.location_id for rid, s in robots.items()}
    proj_carry = {rid: s.carried_object for rid, s in robots.items()}
    proj_obj = {oid: {"location_id": b.location_id, "held_by": b.held_by, "label": b.label,
                      "fresh": world.is_fresh(b.fresh_until)} for oid, b in world.objects.items()}
    energy: dict[str, float] = {}
    resolved: dict[str, AllocatedTask] = {}

    def resolve(task: TaskSpec, allow_held: bool) -> Optional[str]:
        a = task.action
        if a.object_id is not None:
            if a.object_id not in proj_obj:
                err("unknown_object", f"{a.object_id} is not in the observed world.", task.task_id)
                return None
            return a.object_id
        label = a.object_query.label
        cands = [oid for oid, o in proj_obj.items()
                 if o["label"] == label and (o["fresh"] or o["held_by"]) and (allow_held or o["held_by"] is None)]
        if allow_held:
            held = [oid for oid in cands if proj_obj[oid]["held_by"]]
            cands = held or cands
        if not cands:
            err("object_not_located", f"No fresh observation locates a {label}.", task.task_id)
            return None
        if len(cands) > 1:
            err("ambiguous_object", f"{len(cands)} observed instances match {label}: {sorted(cands)}.", task.task_id)
            return None
        return cands[0]

    for t in order:
        rid = allocation.robots.get(t.binding_id)
        a = t.action
        action = a
        if rid is None or rid not in ROBOTS:
            resolved[t.task_id] = AllocatedTask(task_id=t.task_id, binding_id=t.binding_id, robot_id=rid,
                                                action=a, depends_on=list(t.depends_on), skill=a.kind)
            continue
        energy[rid] = energy.get(rid, 0.0) + SKILLS[a.kind].energy_pct
        desc = ROBOTS[rid]
        if a.kind == "navigate_to":
            here = proj_loc.get(rid)
            if a.location_id != here:
                route = route_between(here, a.location_id)
                if route is None:
                    err("no_route", f"No route from {here} to {a.location_id}.", t.task_id)
                else:
                    if a.route_id != route.route_id:
                        err("wrong_route", f"Travel {here}->{a.location_id} must use route {route.route_id}.", t.task_id)
                    status = world.route_status(route.route_id)
                    if status == "blocked":
                        err("route_blocked", f"Latest observation reports {route.route_id} blocked.", t.task_id)
                    elif status != "clear":
                        err("route_not_observed_clear",
                            f"No fresh observation shows {route.route_id} clear; scan it first (a planned scan "
                            "cannot establish clearance).", t.task_id)
                proj_loc[rid] = a.location_id
                if proj_carry.get(rid):
                    proj_obj[proj_carry[rid]]["location_id"] = a.location_id
        elif a.kind == "pick_object":
            oid = resolve(t, allow_held=False)
            if oid:
                action = a.model_copy(update={"object_id": oid, "object_query": None})
                o = proj_obj[oid]
                if o["held_by"]:
                    err("object_held", f"{oid} is already held by {o['held_by']}.", t.task_id)
                elif o["location_id"] != proj_loc.get(rid):
                    err("pick_location_mismatch",
                        f"{rid} would be at {proj_loc.get(rid)} but {oid} is observed at {o['location_id']}.", t.task_id)
                if proj_carry.get(rid):
                    err("capacity", f"{rid} would already hold {proj_carry[rid]} (capacity {desc.capacity}).", t.task_id)
                profile = OBJECT_PROFILES.get(o["label"])
                if profile is None:
                    err("unknown_profile", f"No trusted profile for {o['label']}.", t.task_id)
                elif profile.mass_kg > desc.payload_limit_kg:
                    err("over_payload", f"{o['label']} {profile.mass_kg} kg exceeds {desc.payload_limit_kg} kg.", t.task_id)
                proj_carry[rid] = oid
                o["held_by"] = rid
                o["location_id"] = proj_loc.get(rid)
        elif a.kind == "place_object":
            oid = resolve(t, allow_held=True)
            if oid:
                action = a.model_copy(update={"object_id": oid, "object_query": None})
                if proj_carry.get(rid) != oid:
                    err("possession_order", f"{rid} would not hold {oid} when placing (pick must come first).", t.task_id)
                if proj_loc.get(rid) != a.location_id:
                    err("place_location_mismatch", f"{rid} would be at {proj_loc.get(rid)}, not {a.location_id}.", t.task_id)
                proj_carry[rid] = None
                proj_obj[oid].update({"held_by": None, "location_id": a.location_id})
        resolved[t.task_id] = AllocatedTask(task_id=t.task_id, binding_id=t.binding_id, robot_id=rid,
                                            action=action, depends_on=list(t.depends_on), skill=a.kind)

    # --- battery
    for rid, need in energy.items():
        state = robots.get(rid)
        if state and state.battery_pct - need < BATTERY_RESERVE_PCT:
            err("insufficient_battery",
                f"{rid} has {state.battery_pct:.1f}% but needs {need:.1f}% + {BATTERY_RESERVE_PCT:.0f}% reserve.")

    # --- goal coverage
    if proposal.disposition == "plan":
        goals = canonical_goals if canonical_goals is not None else proposal.mission_goals
        if proposal.scope == "full":
            for g in goals:
                if g.kind == "object_at":
                    ok = any(o["label"] == g.object_query.label and o["location_id"] == g.location_id
                             and o["held_by"] is None for o in proj_obj.values())
                    if not ok:
                        err("goal_not_projected",
                            f"Plan does not project {g.object_query.label} at {g.location_id}.")
        kinds = [t.action for t in proposal.tasks]
        for g in proposal.stage_goals + (goals if proposal.scope == "full" else []):
            if g.kind == "object_located" and not any(
                    k.kind == "inspect_area" and k.object_query.label == g.object_query.label for k in kinds):
                err("stage_goal_uncovered", f"No inspect_area task addresses {g.object_query.label}.", severity="warning")
            if g.kind == "route_observed" and not any(k.kind == "scan_route" and k.route_id == g.route_id for k in kinds):
                err("stage_goal_uncovered", f"No scan_route task observes {g.route_id}.", severity="warning")
            if g.kind == "image_captured" and not any(
                    k.kind == "capture_image" and k.area_id == g.area_id for k in kinds):
                err("stage_goal_uncovered", f"No capture_image task covers {g.area_id}.", severity="warning")

    ok = not any(f.severity == "error" for f in findings)
    ordered = [resolved[t.task_id] for t in proposal.tasks if t.task_id in resolved]
    return ValidationResult(
        ValidationReport(ok=ok, findings=findings, world_version=world.version,
                         projected_energy_pct={k: round(v, 2) for k, v in energy.items()}),
        ordered,
    )
