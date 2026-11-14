"""SensorWatcher (worker side) against the mock rclpy: raw callbacks stay cheap, a converter thread emits."""
import base64
import json
import os
import sys
import threading
import time

import pytest

MOCK_PY = os.path.join(os.path.dirname(__file__), "..", "test", "mock_ros", "python")
MOCK_MODULES = ("rclpy", "sensor_msgs", "std_srvs", "rcl_interfaces", "geometry_msgs", "std_msgs", "sensor_watch")
ENV_KEYS = ("HIVE_MOCK_ROOT", "HIVE_MOCK_DISCOVERY", "ROS_DOMAIN_ID", "ROS_STATIC_PEERS")

ROBOTS = {
    "10.0.0.1:7": {
        "nodes": ["/base_driver"],
        "topics": {"/cmd_vel": "geometry_msgs/msg/Twist"},
        "subscribers": {"/cmd_vel": 1},
        "publishes": {
            "/scan": {"type": "sensor_msgs/msg/LaserScan", "hz": 200},
            "/cam": {"type": "sensor_msgs/msg/Image", "hz": 200, "width": 8, "height": 4},
        },
        "echo_ros_data": True,
    },
}
CAPS = {"image": 100.0, "scan": 100.0, "value": 100.0, "generic": 100.0}


def _purge():
    for name in list(sys.modules):
        if name.split(".")[0] in MOCK_MODULES:
            del sys.modules[name]


@pytest.fixture
def env(tmp_path):
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    path = list(sys.path)
    (tmp_path / "robots").mkdir()
    (tmp_path / "robots.json").write_text(json.dumps(ROBOTS))
    _purge()
    sys.path.insert(0, MOCK_PY)
    os.environ.update(HIVE_MOCK_ROOT=str(tmp_path), HIVE_MOCK_DISCOVERY="0", ROS_DOMAIN_ID="7", ROS_STATIC_PEERS="10.0.0.1")
    import rclpy
    import sensor_watch
    rclpy.init()
    node = rclpy.create_node("t")
    emitted, lock = [], threading.Lock()

    def emit(line):
        with lock:
            emitted.append(line["stream"])

    watcher = sensor_watch.SensorWatcher(node, emit, CAPS)
    yield rclpy, node, watcher, emitted, tmp_path, sensor_watch
    watcher.stop()
    rclpy.shutdown()
    sys.path[:] = path
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _purge()


def spin_until(rclpy, node, pred, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.0)
        time.sleep(0.002)
        if pred():
            return True
    return False


def subs(tmp_path):
    p = tmp_path / "robots" / "10.0.0.1_7.subs.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_watch_returns_info_and_scan_payload(env):
    rclpy, node, watcher, emitted, _, _ = env
    assert watcher.watch("/scan") == {"topic": "/scan", "type": "sensor_msgs/msg/LaserScan", "kind": "scan"}
    assert watcher.watched() == {"/scan"}
    assert spin_until(rclpy, node, lambda: len(emitted) >= 3)
    p = emitted[0]
    assert p["topic"] == "/scan" and p["kind"] == "scan" and p["type"].endswith("LaserScan")
    assert len(p["data"]["ranges_mm"]) == 360 and p["frame_id"] == "10.0.0.1"
    assert emitted[-1]["hz"] > 0 and emitted[-1]["bytes_per_s"] > 0 and isinstance(p["stamp"], float)
    json.dumps(p)


def test_image_payload_is_base64_jpeg(env):
    rclpy, node, watcher, emitted, _, _ = env
    watcher.watch("/cam")
    assert spin_until(rclpy, node, lambda: emitted)
    p = emitted[0]
    assert p["kind"] == "image" and (p["width"], p["height"]) == (8, 4)
    assert "jpeg" not in p and base64.b64decode(p["jpeg_b64"])[:2] == b"\xff\xd8"
    assert p["frame_id"] == "10.0.0.1"


def test_unknown_topic_raises_keyerror(env):
    rclpy, node, watcher, emitted, _, _ = env
    with pytest.raises(KeyError):
        watcher.watch("/nope")
    with pytest.raises(KeyError):
        watcher.watch(None)
    assert watcher.watched() == set()


def test_unwatch_destroys_subscription_and_stops_emitting(env):
    rclpy, node, watcher, emitted, tmp_path, _ = env
    watcher.watch("/scan")
    spin_until(rclpy, node, lambda: emitted)
    watcher.unwatch("/scan")
    watcher.unwatch("/scan")  # idempotent
    assert watcher.watched() == set()
    assert [e["event"] for e in subs(tmp_path)] == ["create", "destroy"]
    time.sleep(0.1)
    n = len(emitted)
    spin_until(rclpy, node, lambda: False, 0.3)
    assert len(emitted) == n


def test_watch_twice_creates_one_subscription(env):
    rclpy, node, watcher, emitted, tmp_path, _ = env
    watcher.watch("/scan")
    watcher.watch("/scan")
    assert [e["event"] for e in subs(tmp_path)] == ["create"]


def test_ros_data_uses_reliable_qos_and_sensors_best_effort(env):
    rclpy, node, watcher, emitted, tmp_path, sw = env
    seen = {}
    real = node.create_subscription

    def spy(msg_type, topic, cb, qos, raw=False):
        seen[topic] = (qos.reliability.name, raw)
        return real(msg_type, topic, cb, qos, raw=raw)

    node.create_subscription = spy
    watcher.watch("/scan")
    watcher.watch("/ros_data")
    assert seen["/scan"] == ("BEST_EFFORT", True)
    assert seen["/ros_data"] == ("RELIABLE", True)


def test_rate_is_capped_per_kind(env):
    rclpy, node, watcher, emitted, _, sw = env
    watcher.stop()
    slow = sw.SensorWatcher(node, lambda line: emitted.append(line["stream"]), {**CAPS, "scan": 10.0})
    slow.watch("/scan")
    spin_until(rclpy, node, lambda: False, 1.0)
    slow.stop()
    assert 6 <= len(emitted) <= 14  # source is 200 Hz, cap 10 Hz


def test_conversion_is_off_the_spinning_thread(env):
    rclpy, node, watcher, emitted, _, sw = env
    threads = []
    real_adapt = sw.adapters.adapt
    sw.adapters.adapt = lambda t, m: (threads.append(threading.current_thread().name), real_adapt(t, m))[1]
    try:
        watcher.watch("/scan")
        spin_until(rclpy, node, lambda: emitted)
    finally:
        sw.adapters.adapt = real_adapt
    assert threads and threading.current_thread().name not in threads


def test_callback_counter_and_measured_rate(env):
    rclpy, node, watcher, emitted, _, _ = env
    watcher.watch("/scan")
    spin_until(rclpy, node, lambda: watcher.callbacks >= 20 and emitted)
    assert watcher.callbacks >= 20
    assert emitted[-1]["hz"] > 20


def test_emit_failure_does_not_kill_converter(env):
    rclpy, node, watcher, emitted, _, sw = env
    calls = []

    def flaky(line):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("pipe")
        emitted.append(line["stream"])

    watcher.stop()
    w = sw.SensorWatcher(node, flaky, CAPS)
    w.watch("/scan")
    assert spin_until(rclpy, node, lambda: emitted)
    w.stop()


def test_adapt_note_passes_through(env):
    rclpy, node, watcher, emitted, _, sw = env
    real = sw.adapters.adapt
    sw.adapters.adapt = lambda t, m: {"kind": "generic", "data": {}, "note": "boom"}
    try:
        watcher.watch("/scan")
        assert spin_until(rclpy, node, lambda: emitted)
    finally:
        sw.adapters.adapt = real
    assert emitted[0]["note"] == "boom"


def test_deserialize_failure_emits_note(env):
    rclpy, node, watcher, emitted, _, sw = env

    def bad(data, cls):
        raise ValueError("bad bytes")

    real = sw.deserialize
    sw.deserialize = bad
    try:
        watcher.watch("/scan")
        assert spin_until(rclpy, node, lambda: emitted)
    finally:
        sw.deserialize = real
    assert "bad bytes" in emitted[0]["note"]


def test_sensor_subscription_is_best_effort_keep_last_one(env):
    rclpy, node, watcher, emitted, _, sw = env
    seen = {}
    real = node.create_subscription

    def spy(msg_type, topic, cb, qos, raw=False):
        seen[topic] = (qos.reliability.name, qos.history.name, qos.depth)
        return real(msg_type, topic, cb, qos, raw=raw)

    node.create_subscription = spy
    watcher.watch("/scan")
    watcher.watch("/cam")
    assert seen["/scan"] == ("BEST_EFFORT", "KEEP_LAST", 1)
    assert seen["/cam"] == ("BEST_EFFORT", "KEEP_LAST", 1)


def test_persistent_emit_failure_leaves_a_trace(env):
    rclpy, node, watcher, emitted, _, sw = env
    logged = []

    def broken(line):
        raise TypeError("not serializable")

    watcher.stop()
    w = sw.SensorWatcher(node, broken, CAPS, lambda key, err: logged.append((key, type(err).__name__)))
    w.watch("/scan")
    assert spin_until(rclpy, node, lambda: logged)
    w.stop()
    assert logged[0] == ("stream /scan", "TypeError")
