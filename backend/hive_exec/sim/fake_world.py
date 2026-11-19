"""Simulator truth and fake skill semantics.

ONLY fake skill implementations (the fake gateway and the ROS 2 fake action
servers) may import this module. The Executive, planner, validator and UI work
from the observed world. A test enforces that the planning context never
contains simulator-only fields.

This module is deliberately standalone (stdlib only) so the ROS 2 fake action
servers in ``ros/hive_fake_robots`` reuse the exact same semantics.
"""

from __future__ import annotations

import copy
import random
import secrets
from dataclasses import dataclass, field
from typing import Any, Optional

# Duplicated minimal fixture (kept standalone for ROS reuse; a test checks it
# matches hive_exec.catalog).
START_LOCATIONS = {"camera_01": "camera_post", "lidar_01": "lidar_post", "gripper_01": "dropoff_zone"}
SKILLS = {
    "camera_01": {"inspect_area", "capture_image", "navigate_to"},
    "lidar_01": {"scan_route", "navigate_to"},
    "gripper_01": {"pick_object", "place_object", "navigate_to"},
}
OBSERVABLE_AREAS = {"camera_01": {"work_area", "pickup_zone"}}
OBSERVABLE_ROUTES = {"lidar_01": {"pickup_corridor"}}
REACHABLE = {
    "camera_01": {"camera_post"},
    "lidar_01": {"lidar_post"},
    "gripper_01": {"dropoff_zone", "pickup_zone"},
}
ROUTES = {"pickup_corridor": ("dropoff_zone", "pickup_zone")}
WITHIN = {"pickup_zone": "work_area"}
PAYLOAD_LIMIT = {"gripper_01": 0.25}
DURATION_S = {"navigate_to": 4.0, "inspect_area": 3.0, "capture_image": 2.0, "scan_route": 3.0,
              "pick_object": 3.0, "place_object": 2.0}
ENERGY_PCT = {"navigate_to": 3.0, "inspect_area": 1.0, "capture_image": 0.5, "scan_route": 1.0,
              "pick_object": 2.0, "place_object": 1.0}
BATTERY_START = 80.0

# Object pool for the random world (synthetic masses). PROFILE_MASS_KG mirrors
# catalog.OBJECT_PROFILES (a test checks it); UNKNOWN_MASS_KG are labels with no trusted
# profile, so the planner meets objects it can find but must not carry.
PROFILE_MASS_KG = {"red_block": 0.10, "blue_block": 0.10, "green_block": 0.10, "duck": 0.05, "tennis_ball": 0.06,
                   "traffic_cone": 0.20, "rc_car": 1.20, "cardboard_box": 0.40, "water_bottle": 0.50}
UNKNOWN_MASS_KG = {"mystery_crate": 0.30, "plush_toy": 0.08, "coffee_mug": 0.30}
WORLD_KINDS = ("fixed", "random")
RANDOM_MAX_OBJECTS = 5
RANDOM_OBJECT_LOCATIONS = ("pickup_zone", "work_area")  # camera-observable; gripper reaches only pickup_zone
RANDOM_GRIPPER_STARTS = ("dropoff_zone", "pickup_zone")
RANDOM_BLOCKED_P = 0.25
RANDOM_DUPLICATE_P = 0.2
RANDOM_BATTERY_PCT = (35.0, 100.0)

KNOWN_FAULTS = {
    "route_blocked": "Obstacle in pickup_corridor (scan reports blocked; navigation fails).",
    "block_missing": "No red block exists in the work area.",
    "two_red_blocks": "Two red blocks in the work area (ambiguous target).",
    "gripper_unavailable": "gripper_01 reports unavailable.",
    "nav_fault_after_pickup": "First navigation while carrying an object fails (motor stall); robot keeps the object.",
    "gripper_fault_after_pickup": "gripper_01 develops a fault right after a successful pick; keeps holding the object.",
    "uncertain_place": "place_object returns an uncertain outcome.",
    "hang_navigate": "navigate_to never finishes until canceled.",
    "hang_scan": "scan_route never finishes until canceled.",
    "low_battery_gripper": "gripper_01 battery drops to 24% (below transport work + 20% reserve).",
}


def _within(area: str, loc: Optional[str]) -> bool:
    return loc is not None and (loc == area or WITHIN.get(loc) == area)


@dataclass
class Outcome:
    status: str  # succeeded | failed | uncertain
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[dict[str, str]] = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    energy_used_pct: float = 0.0


class FakeWorld:
    """Hidden simulator truth for one run."""

    def __init__(self, faults: Optional[list[str]] = None, seed: Optional[int] = None, world: str = "fixed"):
        if world not in WORLD_KINDS:
            raise ValueError(f"unknown world {world!r} (expected one of {list(WORLD_KINDS)})")
        _check_seed(seed)
        self.kind = world
        self.seed: Optional[int] = None
        self.objects: dict[str, dict[str, Any]] = {
            "red_block_1": {"label": "red_block", "location_id": "pickup_zone", "held_by": None, "mass_kg": 0.10},
        }
        self.obstacles: set[str] = set()
        self.robots: dict[str, dict[str, Any]] = {
            rid: {"location_id": loc, "battery_pct": BATTERY_START, "carried": None, "health": "ok", "available": True}
            for rid, loc in START_LOCATIONS.items()
        }
        if world == "random":
            self.seed = secrets.randbits(32) if seed is None else seed
            self._randomize(random.Random(self.seed))
        self.faults: set[str] = set()
        for f in faults or []:
            self.inject(f)

    def _randomize(self, rng: random.Random) -> None:
        """Every random choice goes through ``rng`` (seeded), so a seed replays the world."""
        pool = sorted(PROFILE_MASS_KG) + sorted(UNKNOWN_MASS_KG)
        labels: list[str] = []
        for _ in range(rng.randint(0, RANDOM_MAX_OBJECTS)):
            dup = labels and rng.random() < RANDOM_DUPLICATE_P
            labels.append(rng.choice(labels) if dup else rng.choice(pool))
        objects: dict[str, dict[str, Any]] = {}
        for label in labels:
            oid = _next_id(objects, label)
            objects[oid] = {"label": label, "location_id": rng.choice(RANDOM_OBJECT_LOCATIONS), "held_by": None,
                            "mass_kg": PROFILE_MASS_KG.get(label, UNKNOWN_MASS_KG.get(label))}
        self.objects = objects
        self.obstacles = {"pickup_corridor"} if rng.random() < RANDOM_BLOCKED_P else set()
        self.robots["gripper_01"]["location_id"] = rng.choice(RANDOM_GRIPPER_STARTS)
        for rid in sorted(self.robots):
            self.robots[rid]["battery_pct"] = round(rng.uniform(*RANDOM_BATTERY_PCT), 1)

    # ------------------------------------------------------------- faults
    def inject(self, fault: str) -> None:
        if fault not in KNOWN_FAULTS:
            raise ValueError(f"unknown fault {fault}")
        self.faults.add(fault)
        if fault == "route_blocked":
            self.obstacles.add("pickup_corridor")
        elif fault == "block_missing":
            self.objects = {k: v for k, v in self.objects.items() if v["label"] != "red_block" or v["held_by"]}
        elif fault == "two_red_blocks":
            # ensure at least two free red blocks (the fixed world gains red_block_2, as before)
            while sum(o["label"] == "red_block" and o["held_by"] is None for o in self.objects.values()) < 2:
                self.objects[_next_id(self.objects, "red_block")] = {
                    "label": "red_block", "location_id": "pickup_zone", "held_by": None, "mass_kg": 0.10}
        elif fault == "gripper_unavailable":
            self.robots["gripper_01"]["available"] = False
        elif fault == "low_battery_gripper":
            self.robots["gripper_01"]["battery_pct"] = 24.0

    def hangs(self, skill: str) -> bool:
        return (skill == "navigate_to" and "hang_navigate" in self.faults) or (
            skill == "scan_route" and "hang_scan" in self.faults)

    # ------------------------------------------------------------- status
    def status(self, robot_id: str) -> dict[str, Any]:
        r = self.robots[robot_id]
        return {"robot_id": robot_id, "location_id": r["location_id"], "battery_pct": round(r["battery_pct"], 2),
                "health": r["health"], "available": r["available"] and r["health"] == "ok",
                "carried_object": r["carried"]}

    # ------------------------------------------------- local preconditions
    def precheck(self, robot_id: str, skill: str, params: dict[str, Any]) -> Optional[dict[str, str]]:
        """Robot-local precondition check before accepting a goal (reject on failure)."""
        r = self.robots.get(robot_id)
        if r is None:
            return {"code": "unknown_robot", "message": robot_id}
        if skill not in SKILLS[robot_id]:
            return {"code": "unsupported_skill", "message": f"{robot_id} cannot {skill}"}
        if not r["available"] or r["health"] != "ok":
            return {"code": "robot_unavailable", "message": f"{robot_id} is unavailable ({r['health']})"}
        if r["battery_pct"] < ENERGY_PCT[skill]:
            return {"code": "battery_low", "message": "insufficient battery"}
        loc = r["location_id"]
        if skill == "navigate_to":
            target = params.get("location_id")
            if target not in REACHABLE[robot_id]:
                return {"code": "unreachable", "message": f"{target} not reachable"}
            if target != loc:
                route = params.get("route_id")
                ends = ROUTES.get(route or "")
                if not ends or {loc, target} != set(ends):
                    return {"code": "no_route", "message": f"no route {route} from {loc} to {target}"}
        elif skill in ("inspect_area", "capture_image"):
            if params.get("area_id") not in OBSERVABLE_AREAS.get(robot_id, set()):
                return {"code": "not_observable", "message": f"cannot observe {params.get('area_id')}"}
        elif skill == "scan_route":
            if params.get("route_id") not in OBSERVABLE_ROUTES.get(robot_id, set()):
                return {"code": "not_observable", "message": f"cannot scan {params.get('route_id')}"}
        elif skill == "pick_object":
            if r["carried"]:
                return {"code": "already_holding", "message": f"holding {r['carried']}"}
        elif skill == "place_object":
            if r["carried"] != params.get("object_id"):
                return {"code": "not_holding", "message": f"not holding {params.get('object_id')}"}
            if params.get("location_id") != loc:
                return {"code": "wrong_location", "message": f"at {loc}, not {params.get('location_id')}"}
        return None

    # ------------------------------------------------------------ execute
    def execute(self, robot_id: str, skill: str, params: dict[str, Any]) -> Outcome:
        """Apply the skill at its commit point and return the robot's report."""
        r = self.robots[robot_id]
        energy = ENERGY_PCT[skill]
        r["battery_pct"] = max(0.0, r["battery_pct"] - energy)
        loc = r["location_id"]

        if skill == "navigate_to":
            target = params["location_id"]
            route = params.get("route_id")
            if target == loc:
                return Outcome("succeeded", {"location_id": loc}, energy_used_pct=energy)
            if route in self.obstacles:
                return Outcome("failed", {"location_id": loc},
                               {"code": "route_blocked", "message": f"{route} blocked"},
                               [{"kind": "route_blocked", "entity_id": route, "route_id": route}], energy)
            if r["carried"] and "nav_fault_after_pickup" in self.faults:
                self.faults.discard("nav_fault_after_pickup")
                return Outcome("failed", {"location_id": loc, "carried_object": r["carried"]},
                               {"code": "motor_stall", "message": "drive motor stalled; robot stopped in place"},
                               [], energy)
            r["location_id"] = target
            if r["carried"]:
                self.objects[r["carried"]]["location_id"] = target
            return Outcome("succeeded", {"location_id": target, "carried_object": r["carried"]}, energy_used_pct=energy)

        if skill == "inspect_area":
            area, label = params["area_id"], params["object_query"]["label"]
            seen = [(oid, o) for oid, o in sorted(self.objects.items())
                    if o["label"] == label and o["held_by"] is None and _within(area, o["location_id"])]
            if not seen:
                obs = [{"kind": "object_absent", "entity_id": f"{area}:{label}", "area_id": area, "label": label}]
            else:
                obs = [{"kind": "object_seen", "entity_id": oid, "area_id": area, "label": o["label"],
                        "location_id": o["location_id"], "attrs": {"profile": o["label"], "confidence": 0.95}}
                       for oid, o in seen]
            return Outcome("succeeded", {"found": [oid for oid, _ in seen]}, observations=obs, energy_used_pct=energy)

        if skill == "capture_image":
            area = params["area_id"]
            return Outcome("succeeded", {"image_ref": f"synthetic://{robot_id}/{area}"},
                           observations=[{"kind": "image", "entity_id": f"image:{area}", "area_id": area,
                                          "attrs": {"image_ref": f"synthetic://{robot_id}/{area}"}}],
                           energy_used_pct=energy)

        if skill == "scan_route":
            route = params["route_id"]
            kind = "route_blocked" if route in self.obstacles else "route_clear"
            return Outcome("succeeded", {"route_id": route, "status": kind.split("_")[1]},
                           observations=[{"kind": kind, "entity_id": route, "route_id": route}], energy_used_pct=energy)

        if skill == "pick_object":
            oid = params["object_id"]
            o = self.objects.get(oid)
            if o is None or o["held_by"] or o["location_id"] != loc:
                return Outcome("failed", {}, {"code": "object_not_present", "message": f"{oid} not at {loc}"},
                               energy_used_pct=energy)
            if o["mass_kg"] > PAYLOAD_LIMIT.get(robot_id, 0.0):
                return Outcome("failed", {}, {"code": "over_payload", "message": "too heavy"}, energy_used_pct=energy)
            o["held_by"], r["carried"] = robot_id, oid
            if "gripper_fault_after_pickup" in self.faults:
                r["health"] = "fault"
            return Outcome("succeeded", {"object_id": oid, "held": True, "location_id": loc}, energy_used_pct=energy)

        if skill == "place_object":
            oid, target = params["object_id"], params["location_id"]
            o = self.objects[oid]
            o["held_by"], o["location_id"], r["carried"] = None, target, None
            if "uncertain_place" in self.faults:
                return Outcome("uncertain", {}, {"code": "release_unconfirmed",
                                                 "message": "gripper opened but release not confirmed"},
                               energy_used_pct=energy)
            return Outcome("succeeded", {"object_id": oid, "released": True, "location_id": target},
                           observations=[{"kind": "object_seen", "entity_id": oid, "location_id": target,
                                          "label": o["label"], "attrs": {"placed_by": robot_id}}],
                           energy_used_pct=energy)

        return Outcome("failed", {}, {"code": "unsupported_skill", "message": skill})

    def snapshot(self) -> dict[str, Any]:
        """Debug view of hidden truth (never sent to the planner or normal map)."""
        return copy.deepcopy({"world": self.kind, "seed": self.seed, "objects": self.objects,
                              "obstacles": sorted(self.obstacles),
                              "corridor": {r: ("blocked" if r in self.obstacles else "clear") for r in ROUTES},
                              "robots": self.robots, "faults": sorted(self.faults)})


def _next_id(objects: dict[str, Any], label: str) -> str:
    n = 1
    while f"{label}_{n}" in objects:
        n += 1
    return f"{label}_{n}"


def _check_seed(seed: Any) -> None:
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise ValueError(f"seed must be a non-negative integer, got {seed!r}")


def world_from_args(args: dict[str, Any]) -> FakeWorld:
    """Build a world from reset arguments (fake gateway and ROS fake fleet share this)."""
    return FakeWorld(list(args.get("faults") or []), seed=args.get("seed"), world=args.get("world") or "fixed")
