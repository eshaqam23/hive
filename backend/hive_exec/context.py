"""Planner context builder.

Supplies command, capabilities, observed facts and unresolved information.
Built ONLY from the catalog, trusted robot state and the observed world;
it has no access to simulator truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .catalog import ROBOTS, ROUTES
from .contracts import Goal
from .world import ObservedWorld, RobotState

# Budget policy (spec 7): 12,288 total, 3,072 reserved output => 9,216 input max.
TOTAL_WINDOW_TOKENS = 12_288
RESERVED_OUTPUT_TOKENS = 3_072
MAX_INPUT_TOKENS = TOTAL_WINDOW_TOKENS - RESERVED_OUTPUT_TOKENS
# Operator feedback in the prompt (WS5): the most recent messages only; state/export keep every one.
FEEDBACK_MAX_MESSAGES = 4
FEEDBACK_MAX_CHARS = 1200


class ContextBudgetExceeded(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    """Conservative estimate (~3.2 chars/token for JSON-heavy English). Not a tokenizer."""
    return int(len(text) / 3.2) + 1


@dataclass
class PlanningContext:
    phase: str  # initial | continuation | recovery | clarification_answer | feedback
    command: str
    robots: list[dict[str, Any]]
    observed: dict[str, Any]
    unresolved: list[str]
    canonical_goals: Optional[list[dict]] = None
    stage_history: list[str] = field(default_factory=list)
    failure: Optional[dict[str, Any]] = None
    clarification: Optional[dict[str, str]] = None
    # Every answered question of this mission, oldest first: {question, options, answer, phase}.
    clarifications: list[dict[str, Any]] = field(default_factory=list)
    # Continuation only: the likely next stage the previous observation stage showed the operator
    # ({from_revision, summary, tasks, waits_for}), for the planner to keep or change given the new evidence.
    previous_preview: Optional[dict[str, Any]] = None
    # Operator messages delivered to the planner, oldest first, capped (see cap_feedback). Untrusted text:
    # never part of the INPUT data; the prompt renders it only inside its delimited OPERATOR FEEDBACK block.
    operator_feedback: list[str] = field(default_factory=list)
    feedback_omitted: int = 0  # older delivered messages left out by the cap
    # Feedback phase only: the proposal the operator's message revises ({revision, scope, summary, tasks}).
    previous_proposal: Optional[dict[str, Any]] = None

    def to_json(self) -> str:
        data = dict(self.__dict__)
        data.pop("operator_feedback", None)
        data.pop("feedback_omitted", None)
        for key in ("previous_preview", "previous_proposal"):
            if data.get(key) is None:
                data.pop(key, None)  # keeps every other phase's prompt as it was
        return json.dumps(data, separators=(",", ":"), sort_keys=True)


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
    clarifications: Optional[list[dict[str, Any]]] = None,
    previous_preview: Optional[dict[str, Any]] = None,
    operator_feedback: Optional[list[str]] = None,
    previous_proposal: Optional[dict[str, Any]] = None,
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
    # Open vocabulary: only labels the mission asks about (or, without goals, labels already
    # observed) are worth flagging; listing every profile would imply a closed world.
    seen_labels = {o["label"] for o in objects}
    for label in sorted(goal_labels or seen_labels):
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
        clarifications=[dict(c) for c in (clarifications or [])],
        previous_preview=previous_preview,
        **cap_feedback(operator_feedback or []),
        previous_proposal=previous_proposal,
    )


def cap_feedback(messages: list[str]) -> dict[str, Any]:
    """The most recent messages that fit FEEDBACK_MAX_MESSAGES and FEEDBACK_MAX_CHARS, oldest first, plus
    how many older ones were left out. A message is kept whole or not at all (each is at most 500 chars)."""
    kept: list[str] = []
    used = 0
    for text in reversed(messages):
        if len(kept) >= FEEDBACK_MAX_MESSAGES or used + len(text) > FEEDBACK_MAX_CHARS:
            break
        kept.append(text)
        used += len(text)
    kept.reverse()
    return {"operator_feedback": kept, "feedback_omitted": len(messages) - len(kept)}
