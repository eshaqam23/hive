"""Observed world and trusted robot state (Executive side).

This module never sees simulator truth. Everything here comes from accepted
robot reports (with source, backend timestamp, entity and freshness) or from
explicit operator seeding of a scenario.
"""

from __future__ import annotations

import copy
import itertools
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from .catalog import (
    BATTERY_START_PCT,
    OBSERVATION_FRESH_S,
    ROBOTS,
    area_contains,
)
from .contracts import Observation

Clock = Callable[[], float]


@dataclass
class ObservationRecord:
    obs_id: str
    kind: str
    entity_id: str
    source: str  # robot id or "operator_seed"
    observed_at: float
    fresh_until: float
    attempt_id: Optional[str] = None
    area_id: Optional[str] = None
    route_id: Optional[str] = None
    location_id: Optional[str] = None
    label: Optional[str] = None
    attrs: dict = field(default_factory=dict)


@dataclass
class ObjectBelief:
    object_id: str
    label: str
    location_id: Optional[str]
    held_by: Optional[str]
    source: str
    observed_at: float
    fresh_until: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class RouteBelief:
    route_id: str
    status: str  # clear | blocked
    source: str
    observed_at: float
    fresh_until: float
    evidence: str = ""


@dataclass
class RobotState:
    robot_id: str
    location_id: str
    battery_pct: float = BATTERY_START_PCT
    health: str = "ok"  # ok | fault
    available: bool = True
    current_attempt_id: Optional[str] = None
    carried_object: Optional[str] = None
    last_update: float = 0.0


class ObservedWorld:
    def __init__(self, clock: Clock = time.time):
        self.clock = clock
        self.version = 0
        self.reports: list[ObservationRecord] = []
        self.objects: dict[str, ObjectBelief] = {}
        self.absences: dict[tuple[str, str], ObservationRecord] = {}
        self.routes: dict[str, RouteBelief] = {}
        self.images: list[ObservationRecord] = []
        self._ids = itertools.count(1)

    # ------------------------------------------------------------ ingestion
    def accept(self, obs: Observation, source: str, attempt_id: Optional[str] = None) -> ObservationRecord:
        now = self.clock()
        rec = ObservationRecord(
            obs_id=f"obs-{next(self._ids)}", kind=obs.kind, entity_id=obs.entity_id, source=source,
            observed_at=now, fresh_until=now + OBSERVATION_FRESH_S, attempt_id=attempt_id,
            area_id=obs.area_id, route_id=obs.route_id, location_id=obs.location_id,
            label=obs.label, attrs=dict(obs.attrs),
        )
        self.reports.append(rec)
        if obs.kind == "object_seen":
            prev = self.objects.get(obs.entity_id)
            self.objects[obs.entity_id] = ObjectBelief(
                object_id=obs.entity_id, label=obs.label or (prev.label if prev else "unknown"),
                location_id=obs.location_id, held_by=obs.attrs.get("held_by"), source=source,
                observed_at=now, fresh_until=rec.fresh_until,
                evidence=((prev.evidence if prev else []) + [rec.obs_id])[-8:],
            )
            if obs.area_id and obs.label:
                self.absences.pop((obs.area_id, obs.label), None)
        elif obs.kind == "object_absent":
            key = (obs.area_id or obs.entity_id, obs.label or "")
            self.absences[key] = rec
            # A fresh absence report invalidates beliefs of that label in that area.
            for oid, belief in list(self.objects.items()):
                if belief.label == obs.label and belief.held_by is None and area_contains(
                    obs.area_id or "", belief.location_id
                ):
                    del self.objects[oid]
        elif obs.kind in ("route_clear", "route_blocked"):
            rid = obs.route_id or obs.entity_id
            self.routes[rid] = RouteBelief(
                rid, "clear" if obs.kind == "route_clear" else "blocked", source, now,
                rec.fresh_until, rec.obs_id,
            )
        elif obs.kind == "image":
            self.images.append(rec)
        self.version += 1
        return rec

    def set_possession(self, object_id: str, robot_id: Optional[str], location_id: Optional[str],
                       source: str, evidence: str) -> None:
        now = self.clock()
        prev = self.objects.get(object_id)
        label = prev.label if prev else object_id.rsplit("_", 1)[0]
        self.objects[object_id] = ObjectBelief(
            object_id, label, location_id, robot_id, source, now, now + OBSERVATION_FRESH_S,
            ((prev.evidence if prev else []) + [evidence])[-8:],
        )
        self.version += 1

    # ------------------------------------------------------------- queries
    def is_fresh(self, fresh_until: float) -> bool:
        return self.clock() <= fresh_until

    def instances(self, label: str, area_id: Optional[str] = None, fresh_only: bool = True) -> list[ObjectBelief]:
        out = []
        for b in self.objects.values():
            if b.label != label:
                continue
            if fresh_only and not self.is_fresh(b.fresh_until):
                continue
            if area_id and b.held_by is None and not area_contains(area_id, b.location_id):
                continue
            out.append(b)
        return sorted(out, key=lambda b: b.object_id)

    def route_status(self, route_id: str) -> Optional[str]:
        b = self.routes.get(route_id)
        if b is None or not self.is_fresh(b.fresh_until):
            return None
        return b.status

    def absence(self, area_id: str, label: str) -> Optional[ObservationRecord]:
        rec = self.absences.get((area_id, label))
        if rec and self.is_fresh(rec.fresh_until):
            return rec
        return None

    def image_for(self, area_id: str, since: float = 0.0) -> Optional[ObservationRecord]:
        for rec in reversed(self.images):
            if rec.area_id == area_id and rec.observed_at >= since:
                return rec
        return None

    def snapshot(self) -> dict:
        now = self.clock()
        return {
            "version": self.version,
            "objects": [dict(asdict(b), fresh=b.fresh_until >= now) for b in self.objects.values()],
            "routes": [dict(asdict(r), fresh=r.fresh_until >= now) for r in self.routes.values()],
            "absences": [asdict(r) for r in self.absences.values()],
            "images": [asdict(r) for r in self.images[-10:]],
            "reports": [asdict(r) for r in self.reports],
        }

    def copy(self) -> "ObservedWorld":
        return copy.deepcopy(self)


def initial_robot_states(clock: Clock = time.time) -> dict[str, RobotState]:
    return {
        rid: RobotState(rid, d.start_location, last_update=clock()) for rid, d in ROBOTS.items()
    }
