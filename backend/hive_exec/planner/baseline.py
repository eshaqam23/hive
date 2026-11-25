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
from ..contracts import NextStagePreview, PlanProposal, PlannerRecord
from .base import Planner, PlannerOutput
from .preview import compute_preview_diff, preview_change_lines

LABELS = {"red": "red_block", "blue": "blue_block", "green": "green_block"}  # bare colour words (fallback)
# Every profile label and alias as a phrase ("rubber duck" -> duck), longest first for ties.
LABEL_PHRASES = sorted({name.replace("_", " "): p.label for p in OBJECT_PROFILES.values()
                        for name in (p.label, *p.aliases)}.items(), key=lambda kv: -len(kv[0]))


# Open vocabulary: the noun phrase after an action verb ("find the mystery crate" -> mystery_crate).
OBJECT_VERBS = re.compile(r"\b(?:find|locate|look for|search for|search|check for|check on|where is|where's|"
                          r"inspect|bring|fetch|get|grab|deliver|carry|move|take|transport|pick up|grip|secure|"
                          r"acquire)\s+"
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
# A8: "map (out) (the) arena|room|field" and "explore" ask for a map of the arena (map_area on lidar_01).
MAP = re.compile(r"\b(?:map(?:ping)?\s+(?:out\s+)?(?:the\s+)?(?:arena|room|field)|explor(?:e|ing))\b")
ACTION_WORDS = re.compile(r"\b(bring|deliver|carry|move|transport|fetch|get|grab|find|locate|look for|search|"
                          r"picture|photo|image|snapshot|scan|inspect|survey|pick up|grip|secure|acquire|"
                          r"map(?:ping)?\s+(?:out\s+)?(?:the\s+)?(?:arena|room|field)|explor(?:e|ing))\b")
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


# A8: pick up / grab / grip / secure / acquire an object with no destination: object_held (ends at pick_object).
HOLD_VERBS = re.compile(r"\b(?:pick(?:ing)?\s+up|grab|grip|secure|acquire)\b(?!\s+(?:zone|area)\b)")
CARRY_VERBS = re.compile(r"\b(?:bring|deliver|carry|move|take|transport|fetch|get)\b")
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
ASK_HOLD = "Which object should I pick up? (e.g. the red block)"
QUESTION_OPTIONS = {
    ASK_MOVE: ["the red block", "the blue block", "the green block"],
    ASK_HOLD: ["the red block", "the blue block", "the green block"],
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

    # "pick up the red block (from the pickup zone)": held, no place. A drop-off destination or another carry
    # verb ("grab it and bring it to the drop-off zone") keeps it a delivery.
    if HOLD_VERBS.search(t) and not photo and dest != "dropoff_zone" and not CARRY_VERBS.search(t):
        if not label:
            return [], ["no object label given"], ASK_HOLD
        return [{"kind": "object_held", "object_query": {"label": label}}], [], None
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
    if MAP.search(t):
        goals.append({"kind": "area_mapped", "area_id": "arena"})
    if not goals:
        return [], ["no supported intent recognized (deliver, inspect, photograph, scan, map)"], None
    return goals, [], None


def _why_observers(tasks: list[dict]) -> list[str]:
    """Deterministic reasons for the sensing tasks: only these robots observe these places."""
    out = []
    if any(t["preferred_robot_id"] == "camera_01" for t in tasks):
        out.append("camera_01 is the only car with a camera, so it does the looking.")
    if any(t["action"]["kind"] == "scan_route" for t in tasks):
        out.append("lidar_01 is the only car that scans pickup_corridor, so it checks the route.")
    if any(t["action"]["kind"] == "map_area" for t in tasks):
        out.append("lidar_01 is the only car that maps the arena (LiDAR SLAM), so it builds the map.")
    return out


def _carry_chain(oid: Optional[str], label: str, loc: str, pick_loc: str, dest: Optional[str]) -> list[dict]:
    """navigate -> pick -> navigate -> place for one carrier, as the baseline plans it once the evidence is in.

    ``oid`` None means the object is not located yet: the chain names it by label (a preview).
    ``dest`` None (an object_held goal) ends the chain at pick_object."""
    ref = {"object_id": oid} if oid else {"object_query": {"label": label}}
    steps: list[dict] = []
    if loc != pick_loc:
        steps.append({"kind": "navigate_to", "location_id": pick_loc, "route_id": "pickup_corridor"})
    steps.append({"kind": "pick_object", **ref})
    if dest is None:
        return [_task(f"t{i + 1}", "carrier", a, [f"t{i}"] if i else [], "gripper_01") for i, a in enumerate(steps)]
    if pick_loc != dest:
        steps.append({"kind": "navigate_to", "location_id": dest, "route_id": "pickup_corridor"})
    steps.append({"kind": "place_object", **ref, "location_id": dest})
    return [_task(f"t{i + 1}", "carrier", a, [f"t{i}"] if i else [], "gripper_01") for i, a in enumerate(steps)]


def _next_stage_preview(target: Optional[dict], label: str, dest: Optional[str], carrier_loc: str,
                        route: str) -> dict:
    """The likely next stage of a staged fetch: the carry itself, waiting on the evidence still missing."""
    waits = []
    if target is None:
        waits.append(f"camera_01 sighting of {label} in work_area")
    if route != "clear":
        waits.append("lidar_01 scan: pickup_corridor clear")
    # An unlocated object is assumed to turn up in the pickup zone (the gripper's only zone besides drop-off).
    tasks = _carry_chain(target["object_id"] if target else None, label, carrier_loc,
                         target["location_id"] if target else "pickup_zone", dest)
    for t in tasks:
        t["preferred_robot_id"] = None  # who carries is decided when the stage is planned for real
    summary = f"Carry {label} to {dest} once the evidence is in." if dest else \
        f"Pick up {label} once the evidence is in."
    return {"summary": summary, "tasks": tasks, "waits_for": waits}


def plan_for_goals(goals: list[dict], ctx: PlanningContext) -> PlanProposal:
    objects = [o for o in ctx.observed["objects"] if o["fresh"] or o["held_by"]]
    routes = ctx.observed["routes"]
    robots = {r["robot_id"]: r for r in ctx.robots}
    absences = ctx.observed["absences"]

    # object_at (deliver) and object_held (pick up, no place, A8) share the fetch logic; dest None = held.
    deliveries = [g for g in goals if g["kind"] in ("object_at", "object_held")]
    if deliveries:
        g = deliveries[0]
        label, dest = g["object_query"]["label"], g.get("location_id")
        carrier = robots["gripper_01"]
        held = [o for o in objects if o["label"] == label and o["held_by"]]
        free = [o for o in objects if o["label"] == label and not o["held_by"]]
        at_dest = [o for o in free if o["location_id"] == dest]
        if at_dest and not held:
            return _p(disposition="reject", summary=f"{label} is already at {dest}.", mission_goals=goals,
                      issues=["goal already satisfied"])
        if dest is None and held:
            return _p(disposition="reject", summary=f"{held[0]['object_id']} is already held by {held[0]['held_by']}.",
                      mission_goals=goals, issues=["goal already satisfied"])
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
        route_id = "pickup_corridor"
        route = routes.get(route_id, "unknown")
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
            why = _why_observers(tasks) + [
                "gripper_01 has no camera or LiDAR, so it waits; transport is planned once these results arrive."]
            return _p(disposition="plan", scope="observation_stage",
                      summary="Gather missing evidence before transport.", mission_goals=goals, stage_goals=stage,
                      tasks=tasks, rationale=why,
                      next_stage_preview=_next_stage_preview(target, label, dest, carrier["location_id"], route))
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
        if dest is None:
            why = [f"gripper_01 is the only car that picks, so it picks up {oid}; no destination was given, "
                   "so it keeps holding it (no place_object).",
                   "It has no camera or LiDAR and drives by dead reckoning (simulation); it relies on the earlier "
                   f"camera_01 sighting and the lidar_01 clear scan of {route_id}."]
            return _p(disposition="plan", scope="full", summary=f"Pick up {oid} and hold it.", mission_goals=goals,
                      tasks=tasks, rationale=why)
        if loc != dest:
            add({"kind": "navigate_to", "location_id": dest, "route_id": "pickup_corridor"})
        add({"kind": "place_object", "object_id": oid, "location_id": dest})
        why = [f"gripper_01 is the only car that picks and places, so it carries {oid}.",
               "It has no camera or LiDAR and drives by dead reckoning (simulation); it relies on the earlier "
               f"camera_01 sighting and the lidar_01 clear scan of {route_id}."]
        return _p(disposition="plan", scope="full", summary=f"Carry {oid} to {dest}.", mission_goals=goals,
                  tasks=tasks, rationale=why)

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
        elif g["kind"] == "area_mapped":
            tasks.append(_task(f"t{n}", "mapper", {"kind": "map_area", "area_id": g["area_id"]}, robot="lidar_01"))
    # tasks for one robot share it: chain them (camera tasks; a lidar scan and a map)
    for rid in ("camera_01", "lidar_01"):
        same = [t for t in tasks if t["preferred_robot_id"] == rid]
        for prev, cur in zip(same, same[1:]):
            cur["binding_id"] = prev["binding_id"]
            cur["depends_on"] = [prev["task_id"]]
    return _p(disposition="plan", scope="full", summary="Run the requested observations.", mission_goals=goals,
              tasks=tasks, rationale=_why_observers(tasks))


# ---- operator feedback (phase "feedback"), by keyword rules only ---------------------------------------
# Limits (documented in DECISIONS.md): the baseline understands two kinds of feedback, "sensing only" (find,
# do not carry) and "camera only" (no LiDAR scan). Other feedback is re-parsed together with the command, so
# it changes the plan only when parse_goals reads a new intent from it. Approved mission goals never change,
# and continuation/recovery planning ignores feedback.
_NOT = r"(?:don['’]?t|do not|never|no|without)"
NEGATED = re.compile(rf"\b{_NOT}\s+(?:[a-z_'’-]+\s+){{0,2}}[a-z_'’-]+")
SENSING_ONLY = re.compile(
    rf"\b(?:(?:only|just)\s+(?:find|locate|look|search|inspect|check)|find\s+(?:it\s+)?only|leave it|"
    rf"{_NOT}\s+(?:move|moving|bring|bringing|carry|carrying|pick|fetch|transport|grab|touch|deliver)|"
    rf"no\s+(?:transport|carry|delivery|pickup|pick-up))\b")
CAMERA_ONLY = re.compile(rf"\b(?:camera only|only (?:use )?the camera|use only the camera|just the camera|"
                         rf"{_NOT}\s+(?:the\s+)?(?:lidar|scan)|skip the (?:lidar|scan))\b")
DELIVER_VERBS = re.compile(r"\b(?:bring|deliver|carry|move|take|transport|fetch|get|grab)\b")


def feedback_directives(feedback: list[str]) -> dict[str, bool]:
    t = " ".join(feedback).lower()
    camera_only = bool(CAMERA_ONLY.search(t))
    return {"camera_only": camera_only, "sensing_only": camera_only or bool(SENSING_ONLY.search(t))}


def _apply_feedback_text(text: str, feedback: list[str], directives: dict[str, bool]) -> tuple[str, list[str]]:
    """The text parse_goals reads for a feedback replan: command + answers + feedback, with negated phrases
    ("don't move the gripper") removed and, for sensing-only feedback, every carry verb removed so only the
    find / photograph / scan intents remain."""
    text = NEGATED.sub(" ", ". ".join([text, *feedback]).lower())
    applied = []
    if directives["sensing_only"]:
        text = HOLD_VERBS.sub(" ", DELIVER_VERBS.sub(" ", text))
        applied.append("find only, no transport")
    if directives["camera_only"]:
        applied.append("camera only, no LiDAR scan")
    note = ("Operator feedback applied by keyword rules: " + "; ".join(applied) + "." if applied else
            "Operator feedback re-read with the command; the baseline found no find-only or camera-only rule in it.")
    return text, [note]


BASELINE_LABEL = "Baseline (no LLM)"


class BaselinePlanner(Planner):
    kind = "baseline"

    def status(self) -> dict:
        return {"kind": "baseline", "label": BASELINE_LABEL, "live_enabled": True}

    async def propose(self, ctx: PlanningContext, repair_feedback=None, previous_output=None) -> PlannerOutput:
        record = PlannerRecord(kind="baseline", label=BASELINE_LABEL, latency_ms=0.0)
        notes: list[str] = []
        directives = feedback_directives(ctx.operator_feedback) if ctx.phase == "feedback" else None
        if ctx.canonical_goals and ctx.phase in ("continuation", "recovery", "feedback"):
            goals = ctx.canonical_goals
            if directives is not None:
                notes.append("Operator feedback noted; the baseline cannot change approved mission goals, so it "
                             "re-planned the same goals (an LLM planner can follow the feedback).")
        else:
            text = ctx.command
            answers = [c.get("answer") or "" for c in ctx.clarifications] or \
                [(ctx.clarification or {}).get("answer", "")]
            text = ". ".join(x for x in [text, *answers] if x.strip())
            if directives is not None:
                text, notes = _apply_feedback_text(text, ctx.operator_feedback, directives)
            if is_fleet_question(ctx.command) and not ctx.clarifications:
                return PlannerOutput(_p(disposition="answer", summary="Fleet question answered from the catalog.",
                                        answer=fleet_answer(ctx.command, ctx.robots)), record)
            goals, issues, question = parse_goals(text)
            if question:
                return PlannerOutput(_p(disposition="clarify", summary="The request is ambiguous.", issues=issues,
                                        clarification_question=question,
                                        clarification_options=QUESTION_OPTIONS.get(question, [])), record)
            if directives is not None and directives["camera_only"]:
                goals = [g for g in goals if g["kind"] != "route_observed"]  # the scan is lidar_01's
            if not goals:
                return PlannerOutput(_p(disposition="reject", summary="The fleet cannot perform this request.",
                                        issues=issues), record)
        proposal = plan_for_goals(goals, ctx)
        if notes:
            proposal = proposal.model_copy(update={"issues": [*proposal.issues, *notes][:8]})
        if ctx.phase in ("continuation", "feedback") and ctx.previous_preview and proposal.disposition == "plan":
            # Say, deterministically, what the new evidence changed against the preview the operator saw.
            preview = NextStagePreview.model_validate({k: v for k, v in ctx.previous_preview.items()
                                                       if k != "from_revision"})
            diff = compute_preview_diff(ctx.previous_preview.get("from_revision", 0), preview, proposal.tasks)
            proposal = proposal.model_copy(update={"preview_changes": preview_change_lines(diff)})
        return PlannerOutput(proposal, record)
