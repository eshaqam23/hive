"""Static capability catalog and named-location world fixture.

All durations, energy costs and limits are SYNTHETIC fixture values for the fake
fleet. Navigation/localization are simulation assumptions. Required capabilities
for a task are derived from this catalog, never from model claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SYNTHETIC_NOTE = "synthetic fixture values (fake fleet); not measured on hardware"


@dataclass(frozen=True)
class SkillDef:
    kind: str
    signature: str
    description: str
    duration_s: float  # synthetic
    energy_pct: float  # synthetic
    effect_commit: str  # when the effect is considered committed
    timeout_s: float = 60.0


SKILLS: dict[str, SkillDef] = {
    s.kind: s
    for s in [
        SkillDef("navigate_to", "navigate_to(location_id, route_id|null)",
                 "Drive to a named location. Moving between pickup_zone and dropoff_zone requires "
                 "route_id=pickup_corridor and a fresh observation that the corridor is clear.",
                 4.0, 3.0, "robot reports arrival at location_id"),
        SkillDef("inspect_area", "inspect_area(area_id, object_query)",
                 "Camera looks for objects matching a label in an area it can observe. "
                 "May succeed with nothing found.",
                 3.0, 1.0, "observation report accepted"),
        SkillDef("capture_image", "capture_image(area_id)",
                 "Camera captures an image of an observable area.",
                 2.0, 0.5, "image report accepted"),
        SkillDef("scan_route", "scan_route(route_id)",
                 "Lidar scans a route it can observe and reports clear or blocked. "
                 "May succeed with a blocked result.",
                 3.0, 1.0, "scan report accepted"),
        SkillDef("pick_object", "pick_object(object_id)",
                 "Gripper picks a uniquely observed object at its current location.",
                 3.0, 2.0, "gripper confirms object held"),
        SkillDef("place_object", "place_object(object_id, location_id)",
                 "Gripper releases the held object at its current location (= location_id).",
                 2.0, 1.0, "gripper confirms release at location_id"),
    ]
}


@dataclass(frozen=True)
class RobotDescriptor:
    robot_id: str
    display_name: str
    preset: str  # maps to frontend PRESET_ROBOTS key
    skills: tuple[str, ...]
    start_location: str
    reachable_locations: tuple[str, ...]
    observable_areas: tuple[str, ...] = ()
    observable_routes: tuple[str, ...] = ()
    payload_limit_kg: float = 0.0
    capacity: int = 0
    ros_action: str = ""  # Delivery B adapter routing
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "display_name": self.display_name,
            "preset": self.preset,
            "skills": list(self.skills),
            "start_location": self.start_location,
            "reachable_locations": list(self.reachable_locations),
            "observable_areas": list(self.observable_areas),
            "observable_routes": list(self.observable_routes),
            "payload_limit_kg": self.payload_limit_kg,
            "capacity": self.capacity,
            "adapter": {"fake": self.robot_id, "ros_action": self.ros_action},
            "notes": self.notes,
        }


ROBOTS: dict[str, RobotDescriptor] = {
    r.robot_id: r
    for r in [
        RobotDescriptor(
            "camera_01", "Camera RC Car", "cameraRcCar",
            ("inspect_area", "capture_image", "navigate_to"),
            start_location="camera_post", reachable_locations=("camera_post",),
            observable_areas=("work_area", "pickup_zone"),
            ros_action="/camera_01/execute_skill",
            notes="Identifies trusted object profiles; does not measure mass from pixels.",
        ),
        RobotDescriptor(
            "lidar_01", "LiDAR RC Car", "lidarRcCar",
            ("scan_route", "navigate_to"),
            start_location="lidar_post", reachable_locations=("lidar_post",),
            observable_routes=("pickup_corridor",),
            ros_action="/lidar_01/execute_skill",
        ),
        RobotDescriptor(
            "gripper_01", "Gripper RC Car", "gripperRcCar",
            ("pick_object", "place_object", "navigate_to"),
            start_location="dropoff_zone", reachable_locations=("dropoff_zone", "pickup_zone"),
            payload_limit_kg=0.25, capacity=1,
            ros_action="/gripper_01/execute_skill",
        ),
    ]
}


@dataclass(frozen=True)
class Location:
    location_id: str
    label: str
    kind: str  # post | zone | area
    x: float  # schematic map coordinates for the UI only
    y: float
    within: Optional[str] = None


LOCATIONS: dict[str, Location] = {
    loc.location_id: loc
    for loc in [
        Location("work_area", "Work area", "area", 70, 30),
        Location("pickup_zone", "Pickup zone", "zone", 72, 34, within="work_area"),
        Location("dropoff_zone", "Drop-off zone", "zone", 15, 70),
        Location("camera_post", "Camera post", "post", 88, 10),
        Location("lidar_post", "LiDAR post", "post", 40, 82),
    ]
}

AREAS = ("work_area", "pickup_zone", "dropoff_zone")


@dataclass(frozen=True)
class Route:
    route_id: str
    a: str
    b: str
    bidirectional: bool = True


ROUTES: dict[str, Route] = {"pickup_corridor": Route("pickup_corridor", "dropoff_zone", "pickup_zone")}


@dataclass(frozen=True)
class ObjectProfile:
    label: str
    mass_kg: float
    description: str


# Trusted object profiles: mass comes from the profile, not from perception.
OBJECT_PROFILES: dict[str, ObjectProfile] = {
    "red_block": ObjectProfile("red_block", 0.10, "small red foam block (compatible profile)"),
    "blue_block": ObjectProfile("blue_block", 0.10, "small blue foam block"),
    "green_block": ObjectProfile("green_block", 0.10, "small green foam block"),
}

BATTERY_START_PCT = 80.0
BATTERY_RESERVE_PCT = 20.0
OBSERVATION_FRESH_S = 600.0  # synthetic freshness window


def route_between(a: str, b: str) -> Optional[Route]:
    for route in ROUTES.values():
        if (route.a == a and route.b == b) or (route.bidirectional and route.a == b and route.b == a):
            return route
    return None


def area_contains(area_id: str, location_id: Optional[str]) -> bool:
    if location_id is None:
        return False
    if area_id == location_id:
        return True
    loc = LOCATIONS.get(location_id)
    return bool(loc and loc.within == area_id)


def catalog_dict() -> dict:
    return {
        "note": SYNTHETIC_NOTE,
        "skills": {k: vars(v) for k, v in SKILLS.items()},
        "robots": [r.as_dict() for r in ROBOTS.values()],
        "locations": [vars(l) for l in LOCATIONS.values()],
        "routes": [vars(r) for r in ROUTES.values()],
        "object_profiles": {k: vars(v) for k, v in OBJECT_PROFILES.items()},
        "battery": {"start_pct": BATTERY_START_PCT, "reserve_pct": BATTERY_RESERVE_PCT},
    }
