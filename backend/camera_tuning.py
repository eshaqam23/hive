"""Runtime RealSense tuning over ROS 2 parameters, driven from the worker's tick.

The stock realsense2_camera node streams 640x480@30 colour plus depth by default, far more than campus
Wi-Fi carries. The laptop lowers it at connect time by setting the node's parameters, so the robot needs
no changes. Every service call is asynchronous: `CameraJob.step()` is polled from the worker loop and
never blocks it. Each call gives up after `timeout` seconds.

Tuning fails closed (R30). The job's result is one of:
  * None: no RealSense here. Every parameter node answered and none lists a colour profile parameter, or
    there is no parameter node and no RealSense-looking topic. Only then does nothing gate the camera.
  * applied True: the camera took the settings and reports them back.
  * applied False with a node: the camera refused, or reported something else after the change.
  * applied False with node None (unresolved): a camera node did not answer, or no node answered while the
    robot publishes a RealSense-looking topic. The backend keeps the MJPEG gate on and looks again later.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional

import robot_link

PROFILE_PARAMS = ("rgb_camera.color_profile", "rgb_camera.profile")  # current realsense-ros, then older releases
PROFILE_PARAM = PROFILE_PARAMS[0]
CALL_TIMEOUT = 5.0
READBACK_DELAY = 0.5  # realsense-ros applies a profile change asynchronously and may revert one it rejects
NO_ANSWER = "the camera node did not answer"
IMAGE_TYPES = ("sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage")


class CallFailed:
    """A service call that got no usable answer (not ready, timed out). Distinct from a refusal."""

    def __init__(self, reason: str):
        self.reason = reason


class CameraTuner:
    """Per-worker state: the cached camera node, its profile parameter name and one client per service."""

    def __init__(self, node: Any, clock: Callable[[], float] = time.monotonic, timeout: float = CALL_TIMEOUT):
        self.node, self.clock, self.timeout = node, clock, timeout
        self.camera_node: Optional[str] = None
        self.profile_param: str = PROFILE_PARAM
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

    def realsense_topic(self) -> bool:
        """The robot publishes something that looks like a RealSense: an image topic under /camera/, or any
        realsense2_camera_msgs type."""
        for name, types in self.node.get_topic_names_and_types():
            if any(t.startswith("realsense2_camera_msgs/") for t in types):
                return True
            if name.startswith("/camera/") and any(t in IMAGE_TYPES for t in types):
                return True
        return False

    def start(self, quality: str, depth: bool, call_timeout: Optional[float] = None) -> "CameraJob":
        params = robot_link.camera_params(quality, depth)  # ValueError for an unknown quality
        return CameraJob(self, quality, depth, params, call_timeout or self.timeout)


def value_of(pv: Any) -> Any:
    """The Python value of an rcl_interfaces ParameterValue (real or mock), by its type field."""
    return {1: getattr(pv, "bool_value", None), 2: getattr(pv, "integer_value", None),
            3: getattr(pv, "double_value", None), 4: getattr(pv, "string_value", None)}.get(getattr(pv, "type", 0))


def same_profile(reported: Any, wanted: str) -> bool:
    """424x240x15 and 424,240,15 are the same profile."""
    return isinstance(reported, str) and re.findall(r"\d+", reported) == re.findall(r"\d+", wanted)


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


class _Wait:
    """A pause inside the script, polled like a call. Answers True once the time has come."""

    def __init__(self, until: float):
        self.until = until

    def poll(self, now: float, timeout: float) -> Any:
        return True if now >= self.until else None


class CameraJob:
    """One tuning request. `step()` returns False while waiting, True once `result` is set (see the
    module docstring for what the result means)."""

    def __init__(self, tuner: CameraTuner, quality: str, depth: bool, params: list, call_timeout: float = CALL_TIMEOUT):
        self.tuner, self.quality, self.depth, self.params = tuner, quality, depth, params
        self.call_timeout = call_timeout
        self.result: Optional[dict] = None
        self.done = False
        self._script = self._run()
        self._started = False
        self._call: Any = None
        self._answer: Any = None

    # ------------------------------------------------------------ the script (a coroutine of service calls)
    def _outcome(self, node: Optional[str], **fields: Any) -> dict:
        return {"node": node, "applied": False, "quality": self.quality, "depth": self.depth, "profile": None, "reason": None, **fields}

    def _find(self):
        """Look for the camera node. Returns True when found (and cached), else False with the reason in
        self._unanswered: the candidates that gave no usable answer."""
        from rcl_interfaces.srv import ListParameters

        tuner = self.tuner
        candidates = tuner.candidates()
        self._unanswered = not candidates
        for candidate in candidates:
            request = ListParameters.Request()
            request.prefixes, request.depth = ["rgb_camera"], 0
            reply = yield (ListParameters, f"{candidate}/list_parameters", request)
            if isinstance(reply, CallFailed):
                self._unanswered = True
                continue
            found = next((p for p in PROFILE_PARAMS if p in reply.result.names), None)
            if found:
                tuner.camera_node, tuner.profile_param = candidate, found
                return True
        return False

    def _run(self, rediscover: bool = True):
        tuner = self.tuner
        was_cached = tuner.camera_node is not None
        if tuner.camera_node is None and not (yield from self._find()):
            if self._unanswered and tuner.realsense_topic():
                return self._outcome(None, reason=f"{NO_ANSWER} (the robot publishes a RealSense camera topic)")
            return None
        node, profile_name = tuner.camera_node, tuner.profile_param
        (depth_name, depth_value), (_, profile) = self.params
        ok, reason, unreachable = yield from self._set(node, depth_name, depth_value)
        if not ok:
            if unreachable and was_cached and rediscover:  # the cached node went away: look again, once
                tuner.camera_node = None
                return (yield from self._run(rediscover=False))
            return self._failed(self._outcome(node), reason, unreachable)
        accepted = None
        for variant in robot_link.profile_variants(profile):
            ok, reason, unreachable = yield from self._set(node, profile_name, variant)
            if ok:
                accepted = variant
                break
            if unreachable:  # no answer: another spelling will not help
                break
        if accepted is None:
            return self._failed(self._outcome(node), reason, unreachable)
        return (yield from self._verify(node, (depth_name, depth_value), (profile_name, accepted)))

    def _verify(self, node: str, depth: tuple, profile: tuple):
        """Read the settings back after the camera has had time to apply (or revert) them."""
        from rcl_interfaces.srv import GetParameters

        yield _Wait(self.tuner.clock() + READBACK_DELAY)
        request = GetParameters.Request()
        request.names = [depth[0], profile[0]]
        reply = yield (GetParameters, f"{node}/get_parameters", request)
        if isinstance(reply, CallFailed):
            return self._failed(self._outcome(node), f"the camera did not report its settings back: {reply.reason}", True)
        values = [value_of(v) for v in reply.values]
        got_depth, got_profile = (values + [None, None])[:2]
        if got_depth is not depth[1] or not same_profile(got_profile, profile[1]):
            return self._outcome(node, reason=(
                f"the camera reports {profile[0]}={got_profile} and {depth[0]}={got_depth} after the change "
                f"(asked for {profile[1]} and {depth[1]})"))
        return self._outcome(node, applied=True, profile=profile[1])

    def _failed(self, outcome: dict, reason: Optional[str], unreachable: bool) -> dict:
        """A refusal keeps the node. A node that did not answer is forgotten (it may have restarted or
        gone), and reported with node None so the caller knows to look for it again."""
        if unreachable:
            self.tuner.camera_node = None
            outcome = {**outcome, "node": None}
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
        """Advance without blocking: start the next call (or pause), or collect the one in flight."""
        if self.done:
            return True
        tuner = self.tuner
        while True:
            if self._call is None:
                try:
                    step = self._script.send(self._answer) if self._started else next(self._script)
                except StopIteration as finished:
                    self.result, self.done = finished.value, True
                    return True
                self._started = True
                if isinstance(step, _Wait):
                    self._call = step
                else:
                    srv_type, name, request = step
                    self._call = _Call(tuner.client(srv_type, name), name, request, tuner.clock() + self.call_timeout)
            answer = self._call.poll(tuner.clock(), self.call_timeout)
            if answer is None:
                return False
            self._answer, self._call = answer, None
