"""CameraTuner / CameraJob against a fake rclpy node (no ROS needed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "test", "mock_ros", "python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from camera_tuning import CameraTuner  # noqa: E402
from rcl_interfaces.msg import ParameterValue, SetParametersResult  # noqa: E402
from rcl_interfaces.srv import GetParameters, ListParameters, SetParameters  # noqa: E402


class FakeFuture:
    def __init__(self, result, done=True):
        self._r, self._d = result, done

    def done(self):
        return self._d

    def result(self):
        return self._r


class FakeClient:
    def __init__(self, world, name):
        self.world, self.name = world, name

    def service_is_ready(self):
        return self.name in self.world.services

    def call_async(self, req):
        return self.world.answer(self.name, req)


class FakeWorld:
    """Stands in for the rclpy node. nodes: {node: {param: value}}; refuse: {(node, value)} the camera
    refuses; silent: service names that never answer; absent: services that are not up; revert: nodes that
    answer a set with success but keep the old value (what realsense-ros may do with a profile it rejects);
    topics: the robot's (name, [types]) list."""

    def __init__(self, nodes, refuse=(), silent=(), absent=(), revert=(), topics=()):
        self.nodes = {n: dict(p) for n, p in nodes.items()}
        self.refuse, self.silent, self.revert, self.log = set(refuse), set(silent), set(revert), []
        self.services = {f"{n}/{kind}_parameters" for n in nodes for kind in ("set", "list", "get")}
        self.services -= set(absent)
        self.topics = list(topics)
        self.now = 0.0
        self.got_at = []  # clock readings when get_parameters was called

    def answer(self, name, req):
        if name in self.silent:
            return FakeFuture(None, done=False)
        node, kind = name.rsplit("/", 1)
        if kind == "list_parameters":
            resp = ListParameters.Response()
            resp.result.names = [n for n in self.nodes[node] if n.startswith("rgb_camera")]
            return FakeFuture(resp)
        if kind == "get_parameters":
            self.got_at.append(self.now)
            resp = GetParameters.Response()
            resp.values = [ParameterValue.of(self.nodes[node][n]) if n in self.nodes[node] else ParameterValue() for n in req.names]
            return FakeFuture(resp)
        resp = SetParameters.Response()
        p = req.parameters[0]
        value = p.value.python()
        self.log.append((node, p.name, p.value.type, value))
        ok = (node, value) not in self.refuse
        if ok and node not in self.revert:
            self.nodes[node][p.name] = value
        resp.results = [SetParametersResult(ok, "" if ok else "refused " + str(value))]
        return FakeFuture(resp)

    def get_topic_names_and_types(self):
        return list(self.topics)

    # the node API the tuner uses
    def get_namespace(self):
        return "/"

    def get_name(self):
        return "hive_operator"

    def get_service_names_and_types(self):
        own = ("/hive_operator/set_parameters", ["rcl_interfaces/srv/SetParameters"])
        kinds = {"set": "SetParameters", "list": "ListParameters", "get": "GetParameters"}
        return [(s, [f"rcl_interfaces/srv/{kinds[s.rsplit('/', 1)[1].split('_')[0]]}"]) for s in sorted(self.services)] + [own]

    def create_client(self, srv, name):
        return FakeClient(self, name)

    def clock(self):
        return self.now


def run(world, quality="medium", depth=False, tuner=None, tick=0.1, limit=500):
    tuner = tuner or CameraTuner(world, clock=world.clock, timeout=5.0)
    job = tuner.start(quality, depth)
    for _ in range(limit):
        if job.step():
            return job.result, tuner
        world.now += tick
    raise AssertionError("job never finished")


CAM = {"/camera/camera": {"rgb_camera.color_profile": "640x480x30", "enable_depth": True}, "/base": {"speed": 1}}


def test_applies_depth_then_profile_with_real_types():
    world = FakeWorld(dict(CAM))
    result, tuner = run(world)
    assert result == {"node": "/camera/camera", "applied": True, "quality": "medium", "depth": False,
                      "profile": "424x240x15", "reason": None}
    # PARAMETER_BOOL = 1, PARAMETER_STRING = 4
    assert world.log == [("/camera/camera", "enable_depth", 1, False), ("/camera/camera", "rgb_camera.color_profile", 4, "424x240x15")]
    assert tuner.camera_node == "/camera/camera"


def test_falls_back_to_comma_variant():
    world = FakeWorld(dict(CAM), refuse={("/camera/camera", "424x240x6")})
    result, _ = run(world, quality="low")
    assert result["applied"] is True and result["profile"] == "424,240,6"


def test_refusal_reports_camera_reason():
    world = FakeWorld(dict(CAM), refuse={("/camera/camera", "424x240x15"), ("/camera/camera", "424,240,15")})
    result, _ = run(world)
    assert result["applied"] is False and result["profile"] is None and result["reason"] == "refused 424,240,15"


def test_depth_refusal_stops_before_profile():
    world = FakeWorld(dict(CAM), refuse={("/camera/camera", False)})
    result, _ = run(world)
    assert result["applied"] is False and result["reason"] == "refused False"
    assert [x[1] for x in world.log] == ["enable_depth"]


def test_no_camera_node_gives_none_and_is_not_cached():
    world = FakeWorld({"/base": {"speed": 1}})
    result, tuner = run(world)
    assert result is None and tuner.camera_node is None


def test_node_is_cached_and_not_listed_again():
    world = FakeWorld(dict(CAM))
    _, tuner = run(world)
    world.services = {s for s in world.services if not s.endswith("list_parameters")}
    result, _ = run(world, quality="low", tuner=tuner)
    assert result["applied"] is True and result["profile"] == "424x240x6"


def test_silent_service_times_out_without_retrying_variants():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/set_parameters"})
    result, _ = run(world)
    assert result["applied"] is False and "did not answer in 5 s" in result["reason"]
    assert 5.0 <= world.now < 6.0


def test_step_returns_at_once_while_a_call_is_in_flight():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/list_parameters"})
    job = CameraTuner(world, clock=world.clock).start("medium", False)
    assert job.step() is False and job.step() is False


def test_unready_list_service_means_no_camera():
    world = FakeWorld(dict(CAM), absent={"/camera/camera/list_parameters"})
    result, _ = run(world)
    assert result is None


def test_set_service_that_vanishes_after_discovery_is_rediscovered_then_none():
    world = FakeWorld(dict(CAM))
    tuner = CameraTuner(world, clock=world.clock, timeout=5.0)
    tuner.camera_node = "/camera/camera"
    world.services.discard("/camera/camera/set_parameters")
    result, _ = run(world, tuner=tuner)
    assert result is None and tuner.camera_node is None


def test_unknown_quality_raises():
    with pytest.raises(ValueError):
        CameraTuner(FakeWorld(dict(CAM))).start("ultra", False)


def test_own_node_is_not_a_candidate():
    assert "/hive_operator" not in CameraTuner(FakeWorld(dict(CAM))).candidates()


def test_finished_job_stays_finished():
    world = FakeWorld(dict(CAM))
    job = CameraTuner(world, clock=world.clock).start("medium", False)
    while not job.step():
        world.now += 0.1
    assert job.step() is True and job.result["applied"] is True


def test_cached_node_that_went_away_is_rediscovered_once():
    world = FakeWorld(dict(CAM))
    tuner = CameraTuner(world, clock=world.clock, timeout=5.0)
    tuner.camera_node = "/camera/other"  # stale: no such node any more
    result, _ = run(world, tuner=tuner)
    assert result["applied"] is True and result["node"] == "/camera/camera" and tuner.camera_node == "/camera/camera"


def test_cached_node_gone_and_nothing_else_gives_none():
    world = FakeWorld({"/base": {"speed": 1}})
    tuner = CameraTuner(world, clock=world.clock, timeout=5.0)
    tuner.camera_node = "/camera/camera"
    result, _ = run(world, tuner=tuner)
    assert result is None and tuner.camera_node is None


def test_call_timeout_override_bounds_each_call():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/list_parameters"})
    tuner = CameraTuner(world, clock=world.clock, timeout=5.0)
    job = tuner.start("medium", False, call_timeout=1.0)
    while not job.step():
        world.now += 0.1
    assert job.result is None and world.now < 1.5


# ---- R30: fail closed, and check what the camera really applied ------------------------------------
REALSENSE_TOPICS = [("/camera/camera/color/image_raw", ["sensor_msgs/msg/Image"]), ("/cmd_vel", ["geometry_msgs/msg/Twist"])]


def unresolved(result):
    return result is not None and result["applied"] is False and result["node"] is None and bool(result["reason"])


def test_a_found_camera_whose_set_times_out_is_unresolved_not_absent():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/set_parameters"})
    result, tuner = run(world)
    assert unresolved(result) and tuner.camera_node is None  # looked for again next time


def test_candidates_that_do_not_answer_fail_closed_when_the_robot_has_a_realsense_topic():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/list_parameters"}, topics=REALSENSE_TOPICS)
    result, _ = run(world)
    assert unresolved(result) and "did not answer" in result["reason"]


def test_candidates_that_do_not_answer_mean_no_camera_without_a_camera_topic():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/list_parameters"}, topics=[("/scan", ["sensor_msgs/msg/LaserScan"])])
    result, _ = run(world)
    assert result is None


def test_no_parameter_node_at_all_fails_closed_when_the_robot_has_a_realsense_topic():
    world = FakeWorld({}, topics=REALSENSE_TOPICS)
    result, _ = run(world)
    assert unresolved(result)


def test_realsense_message_types_count_as_a_realsense_topic():
    world = FakeWorld({}, topics=[("/rs/extrinsics", ["realsense2_camera_msgs/msg/Extrinsics"])])
    assert unresolved(run(world)[0])
    world = FakeWorld({}, topics=[("/camera/camera/color/camera_info", ["sensor_msgs/msg/CameraInfo"])])
    assert run(world)[0] is None  # a /camera/ name alone, without an image or RealSense type, is not enough


def test_nodes_that_answer_without_the_profile_parameter_mean_no_camera_even_with_a_camera_topic():
    world = FakeWorld({"/base": {"speed": 1}}, topics=REALSENSE_TOPICS)
    result, _ = run(world)
    assert result is None


def test_a_set_the_camera_quietly_reverts_is_not_applied():
    world = FakeWorld(dict(CAM), revert={"/camera/camera"})
    result, tuner = run(world)
    assert result["applied"] is False and result["node"] == "/camera/camera" and tuner.camera_node == "/camera/camera"
    assert "640x480x30" in result["reason"] and "424x240x15" in result["reason"]


def test_a_read_back_that_does_not_answer_is_not_applied():
    world = FakeWorld(dict(CAM), silent={"/camera/camera/get_parameters"})
    result, _ = run(world)
    assert unresolved(result)


def test_read_back_waits_for_the_camera_to_settle_and_accepts_either_spelling():
    world = FakeWorld(dict(CAM), refuse={("/camera/camera", "424x240x6")})
    result, _ = run(world, quality="low")
    assert result["applied"] is True and result["profile"] == "424,240,6"
    assert world.got_at and world.got_at[0] >= 0.5  # realsense-ros applies a profile change asynchronously


def test_read_back_compares_profiles_by_their_numbers():
    world = FakeWorld(dict(CAM))
    tuner = CameraTuner(world, clock=world.clock, timeout=5.0)
    job = tuner.start("medium", False)
    world.nodes["/camera/camera"].update({"rgb_camera.color_profile": "424,240,15", "enable_depth": False})
    world.revert.add("/camera/camera")  # sets are not stored: the camera reports the other spelling
    while not job.step():
        world.now += 0.1
    assert job.result["applied"] is True


def test_older_realsense_profile_parameter_name_is_used():
    world = FakeWorld({"/camera/camera": {"rgb_camera.profile": "640x480x30", "enable_depth": True}})
    result, _ = run(world)
    assert result["applied"] is True
    assert [x[1] for x in world.log] == ["enable_depth", "rgb_camera.profile"]
