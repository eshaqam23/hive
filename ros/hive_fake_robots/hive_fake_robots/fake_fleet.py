"""Three HIVE fake RC-car ROS 2 action servers sharing ONE simulated world.

Each robot exposes /<robot_id>/execute_skill (hive_interfaces/action/ExecuteSkill).
/hive/fleet_control (hive_interfaces/srv/FleetControl) provides status, coordinated
reset, fault injection and rejection reasons.

Skill semantics come from backend/hive_exec/sim/fake_world.py, the same module the
in-process fake gateway uses, so Delivery A and B share task/result semantics.
Set HIVE_BACKEND_DIR to the repository's backend/ directory.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from functools import partial

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from hive_interfaces.action import ExecuteSkill
from hive_interfaces.srv import FleetControl

sys.path.insert(0, os.environ.get("HIVE_BACKEND_DIR", os.getcwd()))
from hive_exec.sim.fake_world import DURATION_S, KNOWN_FAULTS, FakeWorld  # noqa: E402

ROBOTS = ("camera_01", "lidar_01", "gripper_01")
STEPS = 4


class FakeFleet(Node):
    def __init__(self) -> None:
        super().__init__("hive_fake_fleet")
        self.declare_parameter("time_scale", float(os.environ.get("HIVE_SIM_TIME_SCALE", "1.0")))
        self.lock = threading.RLock()
        self.world = FakeWorld()
        self.run_id = ""
        self.generation = 0
        self.busy: dict[str, str] = {}
        self.seen_attempts: set[str] = set()
        self.rejections: dict[str, dict] = {}
        cb = ReentrantCallbackGroup()
        self._servers = [
            ActionServer(self, ExecuteSkill, f"/{rid}/execute_skill",
                         execute_callback=partial(self.execute, rid),
                         goal_callback=partial(self.on_goal, rid),
                         cancel_callback=self.on_cancel, callback_group=cb)
            for rid in ROBOTS
        ]
        self.create_service(FleetControl, "/hive/fleet_control", self.on_control, callback_group=cb)
        self.get_logger().info("HIVE fake fleet ready: " + ", ".join(f"/{r}/execute_skill" for r in ROBOTS))

    # ------------------------------------------------------------ goals
    def _reject(self, attempt_id: str, code: str, message: str) -> GoalResponse:
        self.rejections[attempt_id] = {"code": code, "message": message}
        self.get_logger().warn(f"reject {attempt_id}: {code} {message}")
        return GoalResponse.REJECT

    def on_goal(self, robot_id: str, goal) -> GoalResponse:
        try:
            env = json.loads(goal.envelope_json)
        except ValueError:
            return self._reject(goal.attempt_id, "bad_envelope", "envelope_json is not JSON")
        with self.lock:
            if env.get("run_id") != self.run_id or goal.run_id != self.run_id:
                return self._reject(goal.attempt_id, "stale_run", f"run {env.get('run_id')} != {self.run_id}")
            if env.get("robot_id") != robot_id:
                return self._reject(goal.attempt_id, "wrong_robot", f"envelope for {env.get('robot_id')}")
            if goal.attempt_id in self.seen_attempts:
                return self._reject(goal.attempt_id, "duplicate_attempt", "attempt already submitted")
            if robot_id in self.busy:
                return self._reject(goal.attempt_id, "robot_busy", f"busy with {self.busy[robot_id]}")
            problem = self.world.precheck(robot_id, env["skill"], env["params"])
            if problem:
                return self._reject(goal.attempt_id, problem["code"], problem["message"])
            self.seen_attempts.add(goal.attempt_id)
            self.busy[robot_id] = goal.attempt_id
        return GoalResponse.ACCEPT

    def on_cancel(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _result(self, goal, status: str, **kw) -> ExecuteSkill.Result:
        r = ExecuteSkill.Result()
        r.run_id, r.attempt_id, r.status = goal.run_id, goal.attempt_id, status
        r.result_json = json.dumps(kw.get("result") or {})
        err = kw.get("error") or {}
        r.error_code, r.error_message = err.get("code", ""), err.get("message", "")
        r.observations_json = json.dumps(kw.get("observations") or [])
        r.energy_used_pct = float(kw.get("energy", 0.0))
        return r

    def _feedback(self, goal_handle, phase: str, progress: float) -> None:
        fb = ExecuteSkill.Feedback()
        fb.run_id, fb.attempt_id = goal_handle.request.run_id, goal_handle.request.attempt_id
        fb.phase, fb.progress = phase, float(progress)
        goal_handle.publish_feedback(fb)

    def execute(self, robot_id: str, goal_handle):
        goal = goal_handle.request
        env = json.loads(goal.envelope_json)
        skill, params = env["skill"], env["params"]
        gen = self.generation
        scale = self.get_parameter("time_scale").value
        try:
            self._feedback(goal_handle, "accepted", 0.0)
            step = DURATION_S[skill] * scale / STEPS
            hang = self.world.hangs(skill)
            i = 0
            while hang or i < STEPS - 1:  # everything before the last step precedes the commit point
                end = time.monotonic() + max(step, 0.001)
                while time.monotonic() < end:
                    if goal_handle.is_cancel_requested or gen != self.generation:
                        break
                    time.sleep(0.01)
                if gen != self.generation:
                    goal_handle.abort()
                    return self._result(goal, "canceled", error={"code": "reset", "message": "coordinated reset"},
                                        result={"effect_committed": False})
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    return self._result(goal, "canceled", result={"effect_committed": False})
                i += 1
                if not hang:
                    self._feedback(goal_handle, "running", i / STEPS)
            with self.lock:
                out = self.world.execute(robot_id, skill, params)
            res = self._result(goal, out.status, result=out.result, error=out.error,
                               observations=out.observations, energy=out.energy_used_pct)
            if out.status == "succeeded":
                goal_handle.succeed()
            else:
                goal_handle.abort()
            return res
        finally:
            with self.lock:
                if self.busy.get(robot_id) == goal.attempt_id:
                    del self.busy[robot_id]

    # ---------------------------------------------------------- control
    def on_control(self, request, response):
        try:
            args = json.loads(request.args_json or "{}")
            result = self._control(request.command, args)
            response.ok, response.result_json = True, json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            response.ok, response.result_json = False, json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        return response

    def _control(self, command: str, args: dict) -> dict:
        if command == "status":
            with self.lock:
                return {"run_id": self.run_id, "active": dict(self.busy),
                        "robots": {r: dict(self.world.status(r), current_attempt_id=self.busy.get(r)) for r in ROBOTS}}
        if command == "reset":
            with self.lock:
                self.generation += 1
            deadline = time.monotonic() + 5.0
            while self.busy and time.monotonic() < deadline:
                time.sleep(0.02)
            with self.lock:
                settled = not self.busy
                self.busy.clear()
                self.world = FakeWorld(args.get("faults") or [])
                self.run_id = args["run_id"]
                self.seen_attempts.clear()
                self.rejections.clear()
            self.get_logger().info(f"reset to {self.run_id} faults={args.get('faults')} settled={settled}")
            return {"run_id": self.run_id, "settled": settled}
        if command == "inject_fault":
            if args.get("fault") not in KNOWN_FAULTS:
                raise ValueError(f"unknown fault {args.get('fault')}")
            with self.lock:
                self.world.inject(args["fault"])
            return {"fault": args["fault"]}
        if command == "rejection":
            return self.rejections.get(args.get("attempt_id", ""), {})
        if command == "truth":
            with self.lock:
                return self.world.snapshot()
        raise ValueError(f"unknown command {command}")


def main() -> None:
    rclpy.init()
    node = FakeFleet()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
