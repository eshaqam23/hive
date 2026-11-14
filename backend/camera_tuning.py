"""Runtime RealSense tuning over ROS 2 parameters, driven from the worker's tick.

The stock realsense2_camera node streams 640x480@30 colour plus depth by default, far more than campus
Wi-Fi carries. The laptop lowers it at connect time by setting the node's parameters, so the robot needs
no changes. Every service call is asynchronous: `CameraJob.step()` is polled from the worker loop and
never blocks it. Each call gives up after `timeout` seconds.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import robot_link

PROFILE_PARAM = "rgb_camera.color_profile"
CALL_TIMEOUT = 5.0


class CallFailed:
    """A service call that got no usable answer (not ready, timed out). Distinct from a refusal."""

    def __init__(self, reason: str):
        self.reason = reason


class CameraTuner:
    """Per-worker state: the cached camera node and one client per service."""

    def __init__(self, node: Any, clock: Callable[[], float] = time.monotonic, timeout: float = CALL_TIMEOUT):
        self.node, self.clock, self.timeout = node, clock, timeout
        self.camera_node: Optional[str] = None
        self.clients: dict[str, Any] = {}

    def client(self, srv_type: Any, name: str) -> Any:
        if name not in self.clients:
            self.clients[name] = self.node.create_client(srv_type, name)
        return self.clients[name]

    def candidates(self) -> list[str]:
        """Nodes that serve set_parameters, other than the worker's own node."""
        own = f"{self.node.get_namespace().rstrip('/')}/{self.node.get_name()}"
        suffix = "/set_parameters"
        found = {
            name[: -len(suffix)]
            for name, types in self.node.get_service_names_and_types()
            if name.endswith(suffix) and any(t.endswith("SetParameters") for t in types)
        }
        return sorted(found - {own})

    def start(self, quality: str, depth: bool) -> "CameraJob":
        params = robot_link.camera_params(quality, depth)  # ValueError for an unknown quality
        return CameraJob(self, quality, depth, params)


class _Call:
    """One in-flight service call: waits for the service, then for the answer, within a deadline."""

    def __init__(self, client: Any, name: str, request: Any, deadline: float):
        self.client, self.name, self.request, self.deadline = client, name, request, deadline
        self.future: Any = None

    def poll(self, now: float, timeout: float) -> Any:
        """The answer (a response or CallFailed), or None while still waiting."""
        if self.future is None:
            if self.client.service_is_ready():
                self.future = self.client.call_async(self.request)
            elif now >= self.deadline:
                return CallFailed(f"{self.name} is not available")
            else:
                return None
        if self.future.done():
            return self.future.result()
        if now >= self.deadline:
            return CallFailed(f"{self.name} did not answer in {timeout:g} s")
        return None


class CameraJob:
    """One tuning request. `step()` returns False while waiting, True once `result` is set. `result` is the
    camera dict, or None when the robot has no RealSense node."""

    def __init__(self, tuner: CameraTuner, quality: str, depth: bool, params: list):
        self.tuner, self.quality, self.depth, self.params = tuner, quality, depth, params
        self.result: Optional[dict] = None
        self.done = False
        self._script = self._run()
        self._started = False
        self._call: Optional[_Call] = None
        self._answer: Any = None

    # ------------------------------------------------------------ the script (a coroutine of service calls)
    def _run(self):
        from rcl_interfaces.srv import ListParameters

        tuner = self.tuner
        if tuner.camera_node is None:
            for candidate in tuner.candidates():
                request = ListParameters.Request()
                request.prefixes, request.depth = ["rgb_camera"], 0
                reply = yield (ListParameters, f"{candidate}/list_parameters", request)
                if not isinstance(reply, CallFailed) and PROFILE_PARAM in reply.result.names:
                    tuner.camera_node = candidate
                    break
        node = tuner.camera_node
        if node is None:
            return None
        outcome = {"node": node, "applied": False, "quality": self.quality, "depth": self.depth, "profile": None, "reason": None}
        (depth_name, depth_value), (profile_name, profile) = self.params
        ok, reason, _ = yield from self._set(node, depth_name, depth_value)
        if not ok:
            return {**outcome, "reason": reason}
        for variant in robot_link.profile_variants(profile):
            ok, reason, unreachable = yield from self._set(node, profile_name, variant)
            if ok:
                return {**outcome, "applied": True, "profile": variant}
            if unreachable:  # no answer: another spelling will not help
                break
        return {**outcome, "reason": reason}

    def _set(self, node: str, name: str, value: Any):
        from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
        from rcl_interfaces.srv import SetParameters

        if isinstance(value, bool):
            pv = ParameterValue(type=ParameterType.PARAMETER_BOOL, bool_value=value)
        else:
            pv = ParameterValue(type=ParameterType.PARAMETER_STRING, string_value=str(value))
        request = SetParameters.Request()
        request.parameters = [Parameter(name=name, value=pv)]
        reply = yield (SetParameters, f"{node}/set_parameters", request)
        if isinstance(reply, CallFailed):
            return False, reply.reason, True
        result = reply.results[0] if reply.results else None
        if result is not None and result.successful:
            return True, None, False
        return False, (getattr(result, "reason", "") or "the camera refused the parameter"), False

    # ------------------------------------------------------------ the driver
    def step(self) -> bool:
        """Advance without blocking: start the next call, or collect the one in flight."""
        if self.done:
            return True
        tuner = self.tuner
        while True:
            if self._call is None:
                try:
                    srv_type, name, request = self._script.send(self._answer) if self._started else next(self._script)
                except StopIteration as finished:
                    self.result, self.done = finished.value, True
                    return True
                self._started = True
                self._call = _Call(tuner.client(srv_type, name), name, request, tuner.clock() + tuner.timeout)
            answer = self._call.poll(tuner.clock(), tuner.timeout)
            if answer is None:
                return False
            self._answer, self._call = answer, None
