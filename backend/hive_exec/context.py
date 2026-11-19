"""Planner context builder.

Supplies command, capabilities, observed facts and unresolved information.
Built ONLY from the catalog, trusted robot state and the observed world;
it has no access to simulator truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .catalog import OBJECT_PROFILES, ROBOTS, ROUTES
from .contracts import Goal
from .world import ObservedWorld, RobotState

# Budget policy (spec 7): 12,288 total, 3,072 reserved output => 9,216 input max.
TOTAL_WINDOW_TOKENS = 12_288
RESERVED_OUTPUT_TOKENS = 3_072
MAX_INPUT_TOKENS = TOTAL_WINDOW_TOKENS - RESERVED_OUTPUT_TOKENS


class ContextBudgetExceeded(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    """Conservative estimate (~3.2 chars/token for JSON-heavy English). Not a tokenizer."""
    return int(len(text) / 3.2) + 1


@dataclass
class PlanningContext:
    phase: str  # initial | continuation | recovery | clarification_answer
    command: str
    robots: list[dict[str, Any]]
    observed: dict[str, Any]
    unresolved: list[str]
    canonical_goals: Optional[list[dict]] = None
    stage_history: list[str] = field(default_factory=list)
    failure: Optional[dict[str, Any]] = None
    clarification: Optional[dict[str, str]] = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, separators=(",", ":"), sort_keys=True)


def build_context(
    *,
    phase: str,
    command: str,
    robots: dict[str, RobotState],
    world: ObservedWorld,
    canonical_goals: Optional[list[Goal]] = None,
    stage_history: Optional[list[str]] = None,
    failure: Optional[dict[str, Any]] = None,
    clarification: Optional[dict[str, str]] = None,
) -> PlanningContext:
    robot_rows = []
    for rid, s in sorted(robots.items()):
        robot_rows.append({
            "robot_id": rid,
            "location_id": s.location_id,
            "battery_pct": round(s.battery_pct, 1),
            "available": s.available and s.health == "ok",
            "health": s.health,
            "carrying": s.carried_object,
            "busy": s.current_attempt_id is not None,
        })

    objects = []
    for b in world.objects.values():
        objects.append({
            "object_id": b.object_id, "label": b.label, "location_id": b.location_id,
            "held_by": b.held_by, "source": b.source, "fresh": world.is_fresh(b.fresh_until),
        })
    routes = {rid: (world.route_status(rid) or "unknown") for rid in ROUTES}
    absences = [{"area_id": r.area_id, "label": r.label, "source": r.source}
                for r in world.absences.values() if world.is_fresh(r.fresh_until)]

    unresolved: list[str] = []
    goal_labels = {g.object_query.label for g in (canonical_goals or []) if hasattr(g, "object_query")}
    for label in sorted(goal_labels or OBJECT_PROFILES):
        n = len(world.instances(label))
        if n == 0 and not any(a["label"] == label for a in absences):
            unresolved.append(f"location of {label} unknown (no fresh observation)")
        elif n > 1:
            unresolved.append(f"{n} instances of {label} observed: ambiguous")
    for rid, status in routes.items():
        if status == "unknown":
            unresolved.append(f"clearance of {rid} unknown (no fresh scan)")

    return PlanningContext(
        phase=phase,
        command=command,
        robots=robot_rows,
        observed={"world_version": world.version, "objects": objects, "routes": routes,
                  "absences": absences, "images": [r.area_id for r in world.images]},
        unresolved=unresolved,
        canonical_goals=[g.model_dump() for g in canonical_goals] if canonical_goals else None,
        stage_history=list(stage_history or []),
        failure=failure,
        clarification=clarification,
    )
