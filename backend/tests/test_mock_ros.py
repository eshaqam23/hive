"""Tests for the mock rclpy in backend/test/mock_ros/python (used by e2e and later tasks)."""
import json
import os
import sys
import time

import pytest

MOCK_PY = os.path.join(os.path.dirname(__file__), "..", "test", "mock_ros", "python")
MOCK_MODULES = ("rclpy", "sensor_msgs", "std_srvs", "rcl_interfaces", "geometry_msgs", "std_msgs")
ENV_KEYS = ("HIVE_MOCK_ROOT", "HIVE_MOCK_DISCOVERY", "ROS_DOMAIN_ID", "ROS_STATIC_PEERS")

ROBOTS = {
    "10.0.0.1:7": {
        "nodes": ["/base_driver", "/camera/camera"],
        "topics": {"/cmd_vel": "geometry_msgs/msg/Twist"},
        "subscribers": {"/cmd_vel": 1},
        "publishes": {
            "/scan": {"type": "sensor_msgs/msg/LaserScan", "hz": 50},
            "/camera/camera/color/image_raw": {"type": "sensor_msgs/msg/Image", "hz": 50, "width": 8, "height": 4},
        },
        "echo_ros_data": True,
        "services": {"/gripper/grab": "std_srvs/srv/Trigger"},
        "params": {"/camera/camera": {"rgb_camera.color_profile": "640x480x30", "enable_depth": True}},
        "param_runtime": True,
        "extra_cmd_vel_publishers": 2,
    },
    "10.0.0.2:7": {
        "nodes": ["/camera/camera"],
        "params": {"/camera/camera": {"enable_depth": True}},
        "param_runtime": False,
        "line_prefix": "D",
        "echo_ros_data": True,
        "subscribers": {"/cmd_vel": 1},
    },
}


def _purge():
    for name in list(sys.modules):
        if name.split(".")[0] in MOCK_MODULES:
            del sys.modules[name]


def _load(tmp_path, peers, domain="7"):
    (tmp_path / "robots").mkdir(exist_ok=True)
    (tmp_path / "robots.json").write_text(json.dumps(ROBOTS))
    _purge()
    if MOCK_PY not in sys.path:
        sys.path.insert(0, MOCK_PY)
    os.environ.update(HIVE_MOCK_ROOT=str(tmp_path), HIVE_MOCK_DISCOVERY="0", ROS_DOMAIN_ID=domain, ROS_STATIC_PEERS=peers)
    import rclpy
    return rclpy


@pytest.fixture(autouse=True)
def _isolate():
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    path = list(sys.path)
    yield
    sys.path[:] = path
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _purge()


@pytest.fixture
def mock(tmp_path):
    rclpy = _load(tmp_path, "10.0.0.1")
    rclpy.init()
    node = rclpy.create_node("t")
    yield rclpy, node, tmp_path
    rclpy.shutdown()


def _spin_until(rclpy, node, pred, seconds=1.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.01)
        if pred():
            return True
    return False


def _events(tmp_path, suffix, ip="10.0.0.1"):
    p = tmp_path / "robots" / f"{ip}_7.{suffix}.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_raw_scan_subscription_delivers_deserializable_laserscan(mock):
    rclpy, node, _ = mock
    from rclpy.serialization import deserialize_message
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import LaserScan
    got = []
    node.create_subscription(LaserScan, "/scan", got.append, qos_profile_sensor_data, raw=True)
    assert _spin_until(rclpy, node, lambda: got)
    assert isinstance(got[0], bytes)
    scan = deserialize_message(got[0], LaserScan)
    assert scan.header.frame_id == "10.0.0.1" and len(scan.ranges) == 360


def test_one_message_per_call_and_rate_limited(mock):
    rclpy, node, _ = mock
    from sensor_msgs.msg import LaserScan
    got = []
    node.create_subscription(LaserScan, "/scan", got.append, 10)
    rclpy.spin_once(node, timeout_sec=0.0)
    rclpy.spin_once(node, timeout_sec=0.0)  # immediately again: the 20 ms period has not elapsed
    assert len(got) == 1


def test_image_is_solid_colour_with_ip_frame(mock):
    rclpy, node, _ = mock
    from sensor_msgs.msg import Image
    got = []
    node.create_subscription(Image, "/camera/camera/color/image_raw", got.append, 10)
    assert _spin_until(rclpy, node, lambda: got)
    img = got[0]
    assert img.header.frame_id == "10.0.0.1" and (img.width, img.height) == (8, 4)
    pixels = {bytes(img.data[i:i + 3]) for i in range(0, len(img.data), 3)}
    assert len(img.data) == 8 * 4 * 3 and len(pixels) == 1


def test_destroy_subscription_stops_delivery_and_logs(mock):
    rclpy, node, tmp = mock
    from sensor_msgs.msg import LaserScan
    got = []
    sub = node.create_subscription(LaserScan, "/scan", got.append, 10, raw=True)
    assert _spin_until(rclpy, node, lambda: got)
    node.destroy_subscription(sub)
    n = len(got)
    _spin_until(rclpy, node, lambda: False, 0.1)
    assert len(got) == n
    events = _events(tmp, "subs")
    assert [e["event"] for e in events] == ["create", "destroy"]
    assert events[0]["topic"] == "/scan"


def test_twist_echoes_ros_data_line(mock):
    rclpy, node, _ = mock
    from geometry_msgs.msg import Twist
    from std_msgs.msg import String
    lines = []
    node.create_subscription(String, "/ros_data", lambda m: lines.append(m.data), 10)
    pub = node.create_publisher(Twist, "/cmd_vel", 10)
    t = Twist()
    t.linear.x = 0.25
    pub.publish(t)
    assert _spin_until(rclpy, node, lambda: lines)
    assert lines == ["127,1,127,1\n"]


def test_echo_reverse_saturates(mock):
    rclpy, node, _ = mock
    from geometry_msgs.msg import Twist
    from std_msgs.msg import String
    lines = []
    node.create_subscription(String, "/ros_data", lambda m: lines.append(m.data), 10)
    pub = node.create_publisher(Twist, "/cmd_vel", 10)
    t = Twist()
    t.linear.x = -0.5
    pub.publish(t)
    assert _spin_until(rclpy, node, lambda: lines)
    assert lines[0] == "255,0,255,0\n"


@pytest.mark.parametrize("ip,ok", [("10.0.0.1", True), ("10.0.0.2", False)])
def test_set_parameters_logged_and_gated_by_param_runtime(tmp_path, ip, ok):
    rclpy = _load(tmp_path, ip)
    rclpy.init()
    node = rclpy.create_node("t")
    from rcl_interfaces.srv import SetParameters
    from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType
    client = node.create_client(SetParameters, "/camera/camera/set_parameters")
    assert client.wait_for_service(timeout_sec=1.0)
    req = SetParameters.Request()
    value = ParameterValue(type=ParameterType.PARAMETER_BOOL, bool_value=False)
    req.parameters = [Parameter(name="enable_depth", value=value)]
    fut = client.call_async(req)
    assert _spin_until(rclpy, node, fut.done)
    assert fut.result().results[0].successful is ok
    logged = _events(tmp_path, "params", ip)
    assert logged[-1]["name"] == "enable_depth" and logged[-1]["successful"] is ok
    rclpy.shutdown()


def _set_and_get(node, rclpy, name, value):
    from rcl_interfaces.msg import Parameter, ParameterValue
    from rcl_interfaces.srv import GetParameters, SetParameters
    setter = node.create_client(SetParameters, "/camera/camera/set_parameters")
    req = SetParameters.Request()
    req.parameters = [Parameter(name=name, value=ParameterValue.of(value))]
    ok = setter.call_async(req).result().results[0].successful
    getter = node.create_client(GetParameters, "/camera/camera/get_parameters")
    get = GetParameters.Request()
    get.names = [name]
    return ok, getter.call_async(get).result().values[0].python()


def test_get_parameters_reads_back_a_set_and_param_revert_answers_success_but_keeps_the_default(mock):
    rclpy, node, tmp = mock
    assert _set_and_get(node, rclpy, "rgb_camera.color_profile", "424x240x15") == (True, "424x240x15")
    robots = json.loads((tmp / "robots.json").read_text())
    robots["10.0.0.1:7"]["param_revert"] = True
    (tmp / "robots.json").write_text(json.dumps(robots))
    time.sleep(0.05)  # past the mock's robots.json cache
    assert _set_and_get(node, rclpy, "rgb_camera.color_profile", "424x240x6") == (True, "640x480x30")


def test_a_robots_json_that_cannot_be_read_keeps_the_last_good_copy(mock):
    rclpy, node, tmp = mock
    from rclpy import _robots
    assert "10.0.0.1:7" in _robots()
    (tmp / "robots.json").write_text('{"10.0.0.1:7": {"onl')  # caught half-written
    time.sleep(0.05)
    assert "10.0.0.1:7" in _robots()
    (tmp / "robots.json").unlink()  # caught between unlink and rename on a drive mount
    time.sleep(0.05)
    assert "10.0.0.1:7" in _robots()


def test_a_message_is_stamped_when_it_is_published_not_when_the_log_write_finishes(mock, monkeypatch):
    """The e2e checks time what the robot got: a slow drive-mount read or write must not move the stamp."""
    rclpy, node, tmp = mock
    from geometry_msgs.msg import Twist
    pub = node.create_publisher(Twist, "/cmd_vel", 10)
    slow = rclpy._visible

    def stalled():
        time.sleep(0.3)  # like a robots.json read on /mnt/c under load
        return slow()

    monkeypatch.setattr(rclpy, "_visible", stalled)
    t0 = time.time()
    pub.publish(Twist())
    logged = [json.loads(line) for line in (tmp / "robots" / "10.0.0.1_7.jsonl").read_text().splitlines()]
    assert logged and logged[-1]["t"] - t0 < 0.1


def test_trigger_service_and_graph(mock):
    rclpy, node, _ = mock
    from std_srvs.srv import Trigger
    names = dict(node.get_service_names_and_types())
    assert names["/gripper/grab"] == ["std_srvs/srv/Trigger"]
    assert "/camera/camera/set_parameters" in names
    client = node.create_client(Trigger, "/gripper/grab")
    assert client.wait_for_service(timeout_sec=1.0)
    fut = client.call_async(Trigger.Request())
    assert _spin_until(rclpy, node, fut.done)
    assert fut.result().success is True
    missing = node.create_client(Trigger, "/nope")
    assert not missing.wait_for_service(timeout_sec=0.1)


def test_count_publishers_and_worker_files(mock):
    rclpy, node, tmp = mock
    from geometry_msgs.msg import Twist
    assert node.count_publishers("/cmd_vel") == 2
    node.create_publisher(Twist, "/cmd_vel", 10)
    assert node.count_publishers("/cmd_vel") == 3
    assert node.count_publishers("/scan") == 1
    pubs = [json.loads(line) for line in (tmp / "robots" / "worker_pubs.jsonl").read_text().splitlines()]
    assert pubs[-1] == {"topic": "/cmd_vel", "depth": 10, "reliability": "reliable"}
    assert json.loads((tmp / "robots" / "worker_env.json").read_text())["ROS_DOMAIN_ID"] == "7"


def test_gripper_car_prefix(tmp_path):
    rclpy = _load(tmp_path, "10.0.0.2")
    rclpy.init()
    node = rclpy.create_node("t")
    from geometry_msgs.msg import Twist
    from std_msgs.msg import String
    lines = []
    node.create_subscription(String, "/ros_data", lambda m: lines.append(m.data), 10)
    pub = node.create_publisher(Twist, "/cmd_vel", 10)
    pub.publish(Twist())
    assert _spin_until(rclpy, node, lambda: lines)
    assert lines == ["D,0,1,0,1\n"]
    rclpy.shutdown()


def test_publisher_info_and_count_include_robot_streams(mock):
    rclpy, node, _ = mock
    assert len(node.get_publishers_info_by_topic("/scan")) == 1
    assert len(node.get_publishers_info_by_topic("/camera/camera/color/image_raw")) == 1
    assert len(node.get_publishers_info_by_topic("/ros_data")) == 1
    assert node.count_publishers("/ros_data") == 1
    assert node.get_subscriptions_info_by_topic("/scan") == []


def test_list_parameters_service_filters_by_prefix(mock):
    rclpy, node, _ = mock
    from rcl_interfaces.srv import ListParameters
    names = dict(node.get_service_names_and_types())
    assert names["/camera/camera/list_parameters"] == ["rcl_interfaces/srv/ListParameters"]
    assert "/camera/camera/set_parameters" in names
    client = node.create_client(ListParameters, "/camera/camera/list_parameters")
    assert client.wait_for_service(timeout_sec=1.0)
    req = ListParameters.Request()
    req.prefixes = ["rgb_camera"]
    fut = client.call_async(req)
    assert fut.done() and fut.result().result.names == ["rgb_camera.color_profile"]


def test_float32_messages():
    sys.path.insert(0, MOCK_PY)
    from std_msgs.msg import Float32, Float32MultiArray
    assert Float32._type == "std_msgs/msg/Float32" and Float32()._yaml() == "{data: 0.0}"
    m = Float32MultiArray()
    m.data = [1, 2.5]
    assert Float32MultiArray._type.endswith("Float32MultiArray") and m._yaml() == "{data: [1.0, 2.5]}"


def test_worker_env_file_has_only_allowlisted_keys(tmp_path):
    os.environ["HIVE_TEST_SECRET"] = "x"
    try:
        rclpy = _load(tmp_path, "10.0.0.1")
        rclpy.init()
        rclpy.create_node("t")
    finally:
        del os.environ["HIVE_TEST_SECRET"]
    env = json.loads((tmp_path / "robots" / "worker_env.json").read_text())
    assert "HIVE_TEST_SECRET" not in env and "PATH" not in env
    assert env["ROS_DOMAIN_ID"] == "7" and env["ROS_STATIC_PEERS"] == "10.0.0.1"
    assert env["ROS_LOCALHOST_ONLY_set"] is False
