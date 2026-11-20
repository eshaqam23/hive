"""Baseline planner: fixed delivery/inspection/scan templates.

Development aid only. Every proposal it makes is labeled "Baseline (no LLM)" and
it is never substituted for the LLM planner automatically. It reads the same
observed-world context as the LLM (no simulator truth).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..catalog import LABEL_PATTERN, LOCATIONS, OBJECT_PROFILES, ROBOTS, canonical_label
from ..context import PlanningContext
from ..contracts import PlanProposal, PlannerRecord
from .base import Planner, PlannerOutput

LABELS = {"red": "red_block", "blue": "blue_block", "green": "green_block"}  # bare colour words (fallback)
# Every profile label and alias as a phrase ("rubber duck" -> duck), longest first for ties.
LABEL_PHRASES = sorted({name.replace("_", " "): p.label for p in OBJECT_PROFILES.values()
                        for name in (p.label, *p.aliases)}.items(), key=lambda kv: -len(kv[0]))


# Open vocabulary: the noun phrase after an action verb ("find the mystery crate" -> mystery_crate).
OBJECT_VERBS = re.compile(r"\b(?:find|locate|look for|search for|search|check for|check on|where is|where's|"
                          r"inspect|bring|fetch|get|grab|deliver|carry|move|take|transport|pick up)\s+"
                          r"(?:(?:me|us)\s+)?(?:(?:the|a|an|my|that|this|some|any)\s+)?")
NOUN_STOP = {"in", "at", "on", "to", "from", "and", "near", "back", "over", "into", "onto", "inside", "around",
             "by", "for", "with", "then", "please", "here", "there", "of", "is", "off", "up", "down", "out",
             "away", "next", "behind", "under", "beside", "or", "if", "so", "the", "a", "an"}
VAGUE_NOUNS = {"it", "block", "blocks", "object", "objects", "thing", "things", "something", "anything", "one",
               "stuff", "item", "items", "them", "that", "this", "those", "these"}
PLACE_WORDS = {"area", "zone", "field", "feild", "corridor", "route", "path", "post", "base", "home", "room",
               "dropoff", "drop-off", "pickup"} | {w for loc in LOCATIONS for w in loc.split("_")}
NOT_OBJECTS = {"picture", "photo", "image", "snapshot", "look", "scan", "survey"}


def _profile_in(t: str) -> Optional[str]:
    t = re.sub(r"[_-]+", " ", t)
    hits = [(m.start(), -len(phrase), label) for phrase, label in LABEL_PHRASES
            for m in [re.search(rf"\b{re.escape(phrase)}\b", t)] if m]
    return min(hits)[2] if hits else None


def _colour_in(t: str) -> Optional[str]:
    return next((v for k, v in LABELS.items() if re.search(rf"\b{k}\b", t)), None)


def _words_after(t: str, start: int) -> list[str]:
    """Up to three words from ``start``, stopping at a preposition, article or punctuation."""
    words: list[str] = []
    for w in re.match(r"[a-z0-9_\- ]*", t[start:]).group(0).split():
        if w in NOUN_STOP or len(words) == 3:
            break
        words.append(w)
    return words


def _label_from(words: list[str]) -> Optional[str]:
    if not words or words[-1] in PLACE_WORDS or set(words) & NOT_OBJECTS:
        return None
    if words[-1] in VAGUE_NOUNS or all(w in VAGUE_NOUNS or w in LABELS for w in words):
        return _colour_in(" ".join(words))  # "the red one" -> red_block; "the block" -> None
    label = canonical_label("_".join(words))
    if label in OBJECT_PROFILES:
        return label
    inner = _profile_in(" ".join(words))  # "big red block" -> red_block
    if inner:
        return inner
    return label if re.fullmatch(LABEL_PATTERN, label) else None


def find_label(text: str) -> Optional[str]:
    """The object the operator named, as a canonical snake_case label (open vocabulary).

    The noun phrase after the first action verb wins ("find the mystery crate" -> mystery_crate),
    never a different object mentioned elsewhere. A vague noun ("the block", "the thing") gives
    None. Sentences without a verb (clarification answers such as "the red block") are read as a
    bare noun phrase. Text with no verb phrase at all falls back to any profiled object or colour.
    """
    t = text.lower()
    sentences = [s.strip() for s in re.split(r"[.;!?\n]+", t) if s.strip()]
    verb_hits = [(s, m) for s in sentences for m in OBJECT_VERBS.finditer(s)]
    for s, m in verb_hits:
        label = _label_from(_words_after(s, m.end()))
        if label:
            return label
    for s in sentences:
        if OBJECT_VERBS.search(s):
            continue
        bare = re.sub(r"^(?:(?:the|a|an|my|that|this|some|any)\s+)", "", s)
        label = _label_from(_words_after(bare, 0))
        if label:
            return label
    return None if verb_hits else (_profile_in(t) or _colour_in(t))


QUESTION_START = re.compile(r"^\s*(what|which|where|how|can|could|is|are|does|do|who|why|tell me)\b")
FLEET_WORDS = re.compile(r"\b(cars?|robots?|fleet|gripper|camera|lidar|rovers?|you|battery|batteries|skills?|"
                         r"capabilit\w*)\b")
ACTION_WORDS = re.compile(r"\b(bring|deliver|carry|move|transport|fetch|get|grab|find|locate|look for|search|"
                          r"picture|photo|image|snapshot|scan|inspect|survey|pick up)\b")
ROBOT_WORDS = {"gripper": "gripper_01", "camera": "camera_01", "lidar": "lidar_01"}


def is_fleet_question(text: str) -> bool:
    """A question about the fleet itself (capabilities or state), not a request to act."""
    t = text.lower()
    return bool((QUESTION_START.search(t) or t.strip().endswith("?")) and FLEET_WORDS.search(t)
                and not ACTION_WORDS.search(t))


def fleet_answer(text: str, robots: list[dict]) -> str:
    """Capability summary from the catalog plus each car's reported state."""
    t = text.lower()
    named = [rid for word, rid in ROBOT_WORDS.items() if re.search(rf"\b{word}\b", t)] or list(ROBOTS)
    state = {r["robot_id"]: r for r in robots}
    parts = []
    for rid in named:
        d, st = ROBOTS[rid], state.get(rid, {})
        line = f"{d.display_name} ({rid}): {', '.join(d.skills)}; reaches {', '.join(d.reachable_locations)}"
        if d.observable_areas:
            line += f"; sees {', '.join(d.observable_areas)}"
        if d.observable_routes:
            line += f"; scans {', '.join(d.observable_routes)}"
        if d.payload_limit_kg:
            line += f"; carries up to {d.payload_limit_kg:g} kg"
        if st:
            line += f"; now at {st.get('location_id')}, battery {st.get('battery_pct')}%"
        parts.append(line + ".")
    return " ".join(parts)[:600]


UNSUPPORTED = re.compile(r"\b(fly|drone|cook|paint|swim|climb|stairs|weld|charge|dance|open the door|elevator)\b")


def _p(**kw: Any) -> PlanProposal:
    base = {"schema_version": "hive.plan-proposal.v1", "mission_goals": [], "stage_goals": [], "tasks": [],
            "issues": [], "clarification_question": None, "scope": None}
    base.update(kw)
    return PlanProposal.model_validate(base)


def _task(tid, binding, action, deps=(), robot=None):
    return {"task_id": tid, "binding_id": binding, "action": action, "depends_on": list(deps),
            "preferred_robot_id": robot}


ASK_MOVE = "Which object should I move? (e.g. the red block)"
ASK_FIND = "Which object should I look for? (e.g. the red block)"
ASK_DEST = "Where should it go? The gripper car delivers to the drop-off zone (dropoff_zone)."
QUESTION_OPTIONS = {
    ASK_MOVE: ["the red block", "the blue block", "the green block"],
    ASK_FIND: ["the red block", "the duck", "the traffic cone"],
    ASK_DEST: ["the drop-off zone"],
}


def parse_goals(text: str) -> tuple[list[dict], list[str], Optional[str]]:
    """Return (goals, issues, clarification_question) from English text."""
    t = text.lower()
    if UNSUPPORTED.search(t):
        return [], [f"unsupported request: '{UNSUPPORTED.search(t).group(0)}' has no matching skill"], None
    label = find_label(t)
    deliver = re.search(r"\b(bring|deliver|carry|move|take|transport|fetch|get|grab)\b", t) and (
        label or re.search(r"\b(block|object|it|thing|one)\b", t))
    photo = re.search(r"\b(picture|photo|image|snapshot)\b", t)
    find = re.search(r"\b(find|locate|look for|search|inspect|where|check for|check on)\b", t)
    scan = re.search(r"\b(scan|check|survey)\b", t) and re.search(r"\b(corridor|route|path)\b", t)
    dest = ("dropoff_zone" if re.search(r"drop[- ]?off", t) else
            "pickup_zone" if re.search(r"\bpick[- ]?up (zone|area)\b", t) and not deliver else None)
    area = "pickup_zone" if re.search(r"pick[- ]?up (zone|area)", t) and (photo or find) and not deliver else "work_area"

    if deliver and not photo:
        if not label:
            return [], ["no object label given"], ASK_MOVE
        if not dest:
            return [], ["destination is not a named location"], ASK_DEST
        return [{"kind": "object_at", "object_query": {"label": label}, "location_id": dest}], [], None
    goals: list[dict] = []
    if photo:
        goals.append({"kind": "image_captured", "area_id": area})
    if find:
        if not label:
            return [], ["no object label given"], ASK_FIND
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
                      issues=[f"ambiguous: {ids}"], clarification_question=f"Which one should I move: {ids}?",
                      clarification_options=[o["object_id"] for o in free][:4])
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


BASELINE_LABEL = "Baseline (no LLM)"


class BaselinePlanner(Planner):
    kind = "baseline"

    def status(self) -> dict:
        return {"kind": "baseline", "label": BASELINE_LABEL, "live_enabled": True}

    async def propose(self, ctx: PlanningContext, repair_feedback=None, previous_output=None) -> PlannerOutput:
        record = PlannerRecord(kind="baseline", label=BASELINE_LABEL, latency_ms=0.0)
        if ctx.canonical_goals and ctx.phase in ("continuation", "recovery"):
            goals = ctx.canonical_goals
        else:
            text = ctx.command
            answers = [c.get("answer") or "" for c in ctx.clarifications] or \
                [(ctx.clarification or {}).get("answer", "")]
            text = ". ".join(x for x in [text, *answers] if x.strip())
            if is_fleet_question(ctx.command) and not ctx.clarifications:
                return PlannerOutput(_p(disposition="answer", summary="Fleet question answered from the catalog.",
                                        answer=fleet_answer(ctx.command, ctx.robots)), record)
            goals, issues, question = parse_goals(text)
            if question:
                return PlannerOutput(_p(disposition="clarify", summary="The request is ambiguous.", issues=issues,
                                        clarification_question=question,
                                        clarification_options=QUESTION_OPTIONS.get(question, [])), record)
            if not goals:
                return PlannerOutput(_p(disposition="reject", summary="The fleet cannot perform this request.",
                                        issues=issues), record)
        return PlannerOutput(plan_for_goals(goals, ctx), record)
