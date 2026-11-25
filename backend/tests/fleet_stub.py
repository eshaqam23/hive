"""A stub FleetControl behind a real SynapseGateway (A8 step 5 tests).

The gateway's bridge round trip (_request) is answered the way bridge/server.mjs relays a
/hive/fleet_control reply: status returns the parsed result_json as is, so an agent fleet's optional
top-level "fleet" block and per-robot "capabilities" reach SynapseGateway.status unchanged. It runs
no skills; every call is recorded so a test can see exactly what reached the "fleet".
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from hive_exec.gateway.synapse import GatewayUnavailable, SynapseGateway

EMULATED_FLEET = {"schema": "hive.fleet-report.v1", "kind": "hive_agent", "profile": "laptop",
                  "evidence": "emulated", "agent_commit": "fc1fe24"}
READY = {"ready": True, "code": None, "reason": None}


def caps(skills: dict[str, dict], **ids: list[str]) -> dict:
    return {"schema": "hive.capabilities.v1", "skills": copy.deepcopy(skills), **ids}


def full_caps() -> dict[str, dict]:
    """Every catalog skill ready and every catalog id listed, per robot (a report that narrows nothing)."""
    return {
        "camera_01": caps({"inspect_area": READY, "capture_image": READY, "navigate_to": READY},
                          areas=["work_area", "pickup_zone"], places=["camera_post"]),
        "lidar_01": caps({"scan_route": READY, "navigate_to": READY, "map_area": READY},
                         routes=["pickup_corridor"], places=["lidar_post"], map_areas=["arena"]),
        "gripper_01": caps({"pick_object": READY, "place_object": READY, "navigate_to": READY},
                           places=["dropoff_zone", "pickup_zone"], place_locations=["dropoff_zone", "pickup_zone"]),
    }


def agent_robots(capabilities: Optional[dict[str, dict]] = None, **fields: dict) -> dict[str, dict]:
    """Per-robot status as an agent sends it: no battery_pct unless a test adds one."""
    robots = {"camera_01": {"location_id": "camera_post", "health": "ok", "available": True},
              "lidar_01": {"location_id": "lidar_post", "health": "ok", "available": True},
              "gripper_01": {"location_id": "dropoff_zone", "health": "ok", "available": True}}
    for rid, c in (capabilities or {}).items():
        robots[rid]["capabilities"] = c
    for rid, extra in fields.items():
        robots[rid].update(extra)
    return robots


class StubFleetGateway(SynapseGateway):
    def __init__(self, robots: dict[str, dict], fleet: Optional[dict] = None):
        super().__init__("ws://stub-bridge")
        self.robots = robots
        self.fleet = fleet
        self.fail_status = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ros_connected = True

    @property
    def connected(self) -> bool:
        return True

    async def start(self, sink) -> None:
        self._sink = sink

    async def stop(self) -> None:
        pass

    async def _request(self, op: str, **payload: Any) -> Any:
        self.calls.append((op, copy.deepcopy(payload)))
        if op == "status":
            if self.fail_status:
                raise GatewayUnavailable("stub: status reply lost")
            reply = {"run_id": self.run_id, "active": {}, "ended": {}, "robots": copy.deepcopy(self.robots)}
            if self.fleet is not None:
                reply["fleet"] = copy.deepcopy(self.fleet)
            return reply
        if op == "reset":
            return {"run_id": payload["run_id"], "settled": True, "world": payload["world"], "seed": payload["seed"]}
        if op == "inject_fault":
            return {"fault": payload["fault"]}
        if op in ("truth", "cancel"):
            return {}
        if op == "submit":
            return {"accepted_for_delivery": False, "detail": "stub FleetControl runs no skills"}
        raise GatewayUnavailable(f"stub: unknown op {op}")

    def sent(self, op: str) -> list[dict[str, Any]]:
        return [p for o, p in self.calls if o == op]
