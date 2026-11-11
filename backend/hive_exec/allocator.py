"""Allocator interface and deterministic capability matching (Delivery A).

A binding (e.g. the navigate/pick/navigate/place carrier chain) is allocated to
exactly one robot. Required capabilities are derived from the catalog. An
OR-Tools allocator is a scheduled follow-up behind the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from .catalog import ROBOTS, RobotDescriptor
from .contracts import PlanProposal, TaskSpec
from .world import RobotState


@dataclass
class Allocation:
    robots: dict[str, Optional[str]]  # binding_id -> robot_id (None if no eligible robot)
    reasons: dict[str, str] = field(default_factory=dict)
    ineligible: dict[str, dict[str, str]] = field(default_factory=dict)  # binding -> robot -> why


class Allocator(ABC):
    @abstractmethod
    def allocate(self, proposal: PlanProposal, robots: dict[str, RobotState],
                 operator_constraints: dict[str, str]) -> Allocation: ...


def binding_tasks(proposal: PlanProposal) -> dict[str, list[TaskSpec]]:
    out: dict[str, list[TaskSpec]] = {}
    for t in proposal.tasks:
        out.setdefault(t.binding_id, []).append(t)
    return out


def static_ineligibility(desc: RobotDescriptor, tasks: list[TaskSpec]) -> Optional[str]:
    """Why a robot's descriptor cannot perform every task of a binding (None = eligible)."""
    for t in tasks:
        a = t.action
        if a.kind not in desc.skills:
            return f"lacks skill {a.kind}"
        if a.kind == "navigate_to" and a.location_id not in desc.reachable_locations:
            return f"cannot reach {a.location_id}"
        if a.kind in ("inspect_area", "capture_image") and a.area_id not in desc.observable_areas:
            return f"cannot observe {a.area_id}"
        if a.kind == "scan_route" and a.route_id not in desc.observable_routes:
            return f"cannot scan {a.route_id}"
        if a.kind == "place_object" and a.location_id not in desc.reachable_locations:
            return f"cannot place at {a.location_id}"
    return None


class CapabilityAllocator(Allocator):
    """Eligible robot per binding; operator constraint > model preference > unused > robot ID."""

    def allocate(self, proposal, robots, operator_constraints):
        result = Allocation(robots={})
        used: set[str] = set()
        for binding, tasks in binding_tasks(proposal).items():
            why_not: dict[str, str] = {}
            eligible = []
            for rid in sorted(ROBOTS):
                reason = static_ineligibility(ROBOTS[rid], tasks)
                state = robots.get(rid)
                if reason is None and state is not None and (not state.available or state.health != "ok"):
                    reason = f"unavailable ({state.health})"
                if reason:
                    why_not[rid] = reason
                else:
                    eligible.append(rid)
            result.ineligible[binding] = why_not

            forced = operator_constraints.get(binding)
            if forced:
                # Hard operator constraint: honour it even if ineligible; the validator reports it.
                result.robots[binding] = forced
                result.reasons[binding] = "operator constraint"
                used.add(forced)
                continue
            if not eligible:
                result.robots[binding] = None
                result.reasons[binding] = "no eligible robot"
                continue
            prefs = {t.preferred_robot_id for t in tasks if t.preferred_robot_id}
            pref = next(iter(prefs)) if len(prefs) == 1 else None
            if pref in eligible:
                choice, why = pref, "model preference (eligible)"
            else:
                unused = [r for r in eligible if r not in used]
                choice = (unused or eligible)[0]
                why = "capability match, tie broken by robot ID"
                if pref:
                    why += f"; preferred {pref} ineligible ({why_not.get(pref, 'n/a')})"
            result.robots[binding] = choice
            result.reasons[binding] = why
            used.add(choice)
        return result
