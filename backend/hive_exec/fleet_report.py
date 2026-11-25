"""A8: the optional fleet report on a FleetControl status reply (docs/instance-agent-contract.md).

An agent fleet may add a top-level ``fleet`` block and a per-robot ``capabilities`` block to its status
reply. The fake fleets send neither, and nothing changes for them. A report can only remove what the
catalog offers: the catalog stays the vocabulary, so unknown skills and ids are dropped on parsing and
nothing here ever adds a skill, place or area to a robot.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .catalog import AREAS, LOCATIONS, MAP_AREAS, ROUTES, SKILLS, RobotDescriptor

FLEET_SCHEMA = "hive.fleet-report.v1"
CAPABILITIES_SCHEMA = "hive.capabilities.v1"
AGENT_KIND = "hive_agent"
FLEET_KEYS = ("schema", "kind", "profile", "evidence", "agent_commit")
FLEET_TEXT_MAX_CHARS = 80
CODE_MAX_CHARS = 64
REASON_MAX_CHARS = 160
# The planner context's "ready" text per robot. The longest catalog-only text today is ~100 chars
# (camera_01); the cap only guards against a catalog that grows (FLEET_REPORT_CONTEXT_BUDGET).
CAPABILITY_TEXT_MAX_CHARS = 200
MALFORMED = "fleet report capabilities malformed"

# skill -> (action field, report list, noun for the finding, catalog descriptor field)
ID_LISTS: dict[str, tuple[str, str, str, str]] = {
    "inspect_area": ("area_id", "areas", "area", "observable_areas"),
    "capture_image": ("area_id", "areas", "area", "observable_areas"),
    "scan_route": ("route_id", "routes", "route", "observable_routes"),
    "navigate_to": ("location_id", "places", "place", "reachable_locations"),
    "map_area": ("area_id", "map_areas", "map area", "map_areas"),
    "place_object": ("location_id", "place_locations", "place location", "reachable_locations"),
}
REPORT_LISTS = ("areas", "routes", "places", "map_areas", "place_locations")
_CATALOG_IDS = frozenset(LOCATIONS) | frozenset(ROUTES) | frozenset(AREAS) | frozenset(MAP_AREAS)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: Any, limit: int) -> Optional[str]:
    """A plain, single-line, bounded string, or None for anything else."""
    if not isinstance(value, str):
        return None
    return _CONTROL.sub(" ", value)[:limit]


# ---------------------------------------------------------------- fleet block
def parse_fleet(raw: Any) -> Optional[dict[str, Optional[str]]]:
    """The fleet block as five bounded strings (None where absent or not a string); None without a block."""
    if not isinstance(raw, dict):
        return None
    return {k: _text(raw.get(k), FLEET_TEXT_MAX_CHARS) for k in FLEET_KEYS}


def is_agent(fleet: Optional[dict]) -> bool:
    return bool(fleet) and fleet.get("kind") == AGENT_KIND


def is_simulated(fleet: Optional[dict]) -> bool:
    """Fail safe: only a hive_agent fleet that says its evidence is real is not simulated."""
    return not (is_agent(fleet) and fleet.get("evidence") == "real")


# ---------------------------------------------------------------- per-robot capabilities
@dataclass(frozen=True)
class SkillReport:
    ready: bool
    code: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class Capabilities:
    skills: dict[str, SkillReport] = field(default_factory=dict)  # catalog skills only
    ids: dict[str, tuple[str, ...]] = field(default_factory=dict)  # REPORT_LISTS -> catalog ids, sorted
    problem: Optional[str] = None  # set when the block is malformed: it then offers nothing


def parse_capabilities(raw: Any) -> Optional[Capabilities]:
    """None when the robot sends no block; a Capabilities that offers nothing when it is malformed."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("schema") != CAPABILITIES_SCHEMA or not isinstance(raw.get("skills"), dict):
        return Capabilities(problem=MALFORMED)
    if any(k in raw and not isinstance(raw[k], list) for k in REPORT_LISTS):
        return Capabilities(problem=MALFORMED)
    skills = {}
    for name, entry in raw["skills"].items():
        if name not in SKILLS:
            continue  # a report cannot add a skill
        if isinstance(entry, dict):
            skills[name] = SkillReport(entry.get("ready") is True, _text(entry.get("code"), CODE_MAX_CHARS),
                                       _text(entry.get("reason"), REASON_MAX_CHARS))
        else:
            skills[name] = SkillReport(False, None, "malformed skill entry")
    ids = {k: tuple(sorted({x for x in raw.get(k, []) if isinstance(x, str) and x in _CATALOG_IDS}))
           for k in REPORT_LISTS}
    return Capabilities(skills=skills, ids=ids)


def report_ineligibility(caps: Optional[Capabilities], tasks: list) -> Optional[str]:
    """Why a robot's report rules out a binding's tasks (None = the report does not narrow them)."""
    if caps is None:
        return None
    if caps.problem:
        return caps.problem
    for t in tasks:
        a = t.action
        rep = caps.skills.get(a.kind)
        if rep is None:
            return f"{a.kind} not in the fleet report"
        if not rep.ready:
            why = ": ".join(x for x in (rep.code, rep.reason) if x) or "no code or reason given"
            return f"{a.kind} not ready in the fleet report ({why})"
        spec = ID_LISTS.get(a.kind)
        if spec:
            attr, key, noun, _ = spec
            value = getattr(a, attr, None)
            if value not in caps.ids.get(key, ()):
                return f"{noun} {value} not listed in the fleet report"
    return None


def capability_text(caps: Optional[Capabilities], desc: RobotDescriptor) -> Optional[str]:
    """Bounded planner-context text: ready catalog skills of this robot, each with its listed catalog ids.

    "navigate_to(pickup_zone) pick_object". None without a report; "" when the report offers nothing.
    Agent codes and reasons never reach the prompt (they are free text from the fleet).
    """
    if caps is None:
        return None
    if caps.problem:
        return ""
    parts = []
    for skill in sorted(s for s, r in caps.skills.items() if r.ready and s in desc.skills):
        spec = ID_LISTS.get(skill)
        if spec is None:
            parts.append(skill)
            continue
        _, key, _, allowed = spec
        ids = [x for x in caps.ids.get(key, ()) if x in getattr(desc, allowed)]
        if ids:  # a skill with nothing listed to do it on is not offered
            parts.append(f"{skill}({','.join(ids)})")
    text = ""
    for part in parts:
        nxt = f"{text} {part}" if text else part
        if len(nxt) > CAPABILITY_TEXT_MAX_CHARS:
            break
        text = nxt
    return text


def measured_battery(value: Any) -> Optional[float]:
    """A reported battery percentage, or None (unknown) when absent or not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)
