"""Planner context builder.

Supplies command, capabilities, observed facts and unresolved information.
Built ONLY from the catalog, trusted robot state and the observed world;
it has no access to simulator truth.
"""

from __future__ import annotations

import json
import re
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
FEEDBACK_MAX_CHARS = 1200  # text_width of the rendered form the prompt shows (ASCII-equivalent characters)
FEEDBACK_TRUNCATED = " [truncated]"
# Token estimate: ASCII at ~3.2 chars/token (JSON-heavy English); every non-ASCII code point at NON_ASCII_TOKENS
# (Qwen's byte-level BPE: CJK and common emoji are 1-2 tokens; 2 is the conservative end of that range).
CHARS_PER_TOKEN = 3.2
NON_ASCII_TOKENS = 2
# Characters str.splitlines breaks on that json.dumps(ensure_ascii=False) leaves raw (it escapes the C0 ones),
# plus lone surrogates (not encodable as UTF-8). They are written as \uXXXX escapes, so every JSON value stays on
# its line and the prompt encodes.
_RAW_UNSAFE = re.compile("[\x85\u2028\u2029\ud800-\udfff]")


def safe_dumps(obj: Any, **kw: Any) -> str:
    """JSON with non-ASCII text kept as itself (an emoji is 1 character, not a 12-character escape pair), except
    that nothing in it can start a new prompt line."""
    return _RAW_UNSAFE.sub(lambda c: f"\\u{ord(c.group()):04x}", json.dumps(obj, ensure_ascii=False, **kw))


def render_feedback(text: str) -> str:
    """How one operator message appears in the prompt: one JSON string on one line (safe_dumps). The prompt and
    the cap both use this, so the cap measures what the model reads."""
    return safe_dumps(text)


def text_width(text: str) -> float:
    """Length in ASCII-equivalent characters: a non-ASCII code point counts as NON_ASCII_TOKENS tokens' worth."""
    wide = len(text) - len(text.encode("ascii", "ignore"))
    return len(text) + wide * (NON_ASCII_TOKENS * CHARS_PER_TOKEN - 1)


class ContextBudgetExceeded(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    """Conservative estimate: ASCII at ~3.2 chars/token (unchanged), each non-ASCII code point NON_ASCII_TOKENS.
    Not a tokenizer."""
    return int(text_width(text) / CHARS_PER_TOKEN) + 1


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
        return safe_dumps(data, separators=(",", ":"), sort_keys=True)


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


def _fit_prefix(text: str, budget: int) -> str:
    """The longest prefix of text, marked FEEDBACK_TRUNCATED, whose rendered form fits budget. Slicing a str
    cuts between code points, so an escape sequence or a surrogate pair is never split."""
    lo, hi = 0, len(text)
    while lo < hi:  # largest n with len(render(text[:n] + mark)) <= budget
        mid = (lo + hi + 1) // 2
        if text_width(render_feedback(text[:mid] + FEEDBACK_TRUNCATED)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + FEEDBACK_TRUNCATED


def cap_feedback(messages: list[str]) -> dict[str, Any]:
    """The most recent messages whose rendered form fits FEEDBACK_MAX_CHARS (at most FEEDBACK_MAX_MESSAGES),
    oldest first, plus how many older ones were left out. The newest message is always represented: whole if
    it fits, else a marked prefix that fits; older ones are kept whole or not at all. State keeps every text."""
    kept: list[str] = []
    used = 0
    for text in reversed(messages):
        size = text_width(render_feedback(text))
        if not kept and size > FEEDBACK_MAX_CHARS:
            text = _fit_prefix(text, FEEDBACK_MAX_CHARS)
            size = text_width(render_feedback(text))
        if len(kept) >= FEEDBACK_MAX_MESSAGES or used + size > FEEDBACK_MAX_CHARS:
            break
        kept.append(text)
        used += size
    kept.reverse()
    return {"operator_feedback": kept, "feedback_omitted": len(messages) - len(kept)}
