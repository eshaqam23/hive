"""A backend that stops reading must not stop the worker's drive tick, deadman or stops (regression)."""
import json
import os
import sys
import threading
import time

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_PY = os.path.join(BACKEND, "test", "mock_ros", "python")
MOCK_MODULES = ("rclpy", "sensor_msgs", "std_srvs", "rcl_interfaces", "geometry_msgs", "std_msgs", "sensor_watch", "server")
ENV_KEYS = ("HIVE_MOCK_ROOT", "HIVE_MOCK_DISCOVERY", "ROS_DOMAIN_ID", "ROS_STATIC_PEERS")
ROBOTS = {
    "10.0.0.1:7": {
        "nodes": ["/base_driver"],
        "topics": {"/cmd_vel": "geometry_msgs/msg/Twist"},
        "subscribers": {"/cmd_vel": 1},
        "publishes": {"/scan": {"type": "sensor_msgs/msg/LaserScan", "hz": 200}},
        "echo_ros_data": True,
    },
}


def purge():
    for name in list(sys.modules):
        if name.split(".")[0] in MOCK_MODULES:
            del sys.modules[name]


@pytest.fixture
def loop(tmp_path):
    saved, path = {k: os.environ.get(k) for k in ENV_KEYS}, list(sys.path)
    (tmp_path / "robots").mkdir()
    (tmp_path / "robots.json").write_text(json.dumps(ROBOTS))
    purge()
    sys.path[:0] = [MOCK_PY, BACKEND]
    os.environ.update(HIVE_MOCK_ROOT=str(tmp_path), HIVE_MOCK_DISCOVERY="0", ROS_DOMAIN_ID="7", ROS_STATIC_PEERS="10.0.0.1")
    import outputs
    import server
    release = threading.Event()
    written = []

    def blocked_sink(line):
        written.append(line)
        release.wait()  # the backend has stopped reading: this write never returns

    server.OUT = outputs.LineWriter(sink=blocked_sink)
    worker = server.RobotWorkerLoop()
    yield worker, server, tmp_path, written
    release.set()
    worker.watcher.stop()
    sys.path[:] = path
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    purge()


def robot_msgs(tmp_path):
    p = tmp_path / "robots" / "10.0.0.1_7.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_deadman_still_stops_the_car_while_stdout_is_blocked(loop):
    worker, server, tmp_path, written = loop
    worker.handle({"id": 1, "op": "watch", "topic": "/scan"})  # a sensor stream is open
    worker.start_driving("forward", 0.5)
    worker.last_keepalive = time.monotonic()  # the page then goes silent
    start = time.monotonic()
    stopped_at = None
    while time.monotonic() - start < 2.5:
        worker.tick()
        worker.reply(99, filler="x" * 1000)  # replies and link lines keep coming, and must not block
        if worker.action == "stop" and stopped_at is None:
            stopped_at = time.monotonic() - start
        time.sleep(0.02)
    assert stopped_at is not None and stopped_at < 1.3, f"deadman fired after {stopped_at}"
    zeros = [m for m in robot_msgs(tmp_path) if m["topic"] == "/cmd_vel" and "x: 0.0" in m["msg"] and "z: 0.0" in m["msg"]]
    assert zeros, "no zero Twist was published"
    assert len(written) == 1  # the writer really was stuck on its first line the whole time
    assert server.OUT.dropped > 0 or len(server.OUT._control) > 0


def test_startup_failure_reaches_stdout_before_exit(tmp_path):
    """run_robot_worker must flush the ready:false line before the exception ends the process."""
    import subprocess
    root = tmp_path / "mock"
    (root / "robots").mkdir(parents=True)
    (root / "robots.json").write_text("{}")
    env = dict(os.environ, HIVE_MOCK_ROOT=str(root), ROS_DOMAIN_ID="7", ROS_STATIC_PEERS="10.0.0.1",
               PYTHONPATH=os.pathsep.join([MOCK_PY, BACKEND]))
    # rclpy.init fails inside RobotWorkerLoop.__init__
    code = ("import rclpy, server\n"
            "def boom(*a, **k): raise RuntimeError('no DDS here')\n"
            "rclpy.init = boom\n"
            "server.run_robot_worker()\n")
    for _ in range(5):
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=20)
        lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
        assert proc.returncode != 0
        assert lines == [{"ready": False, "error": "RuntimeError: no DDS here"}], (lines, proc.stderr[-300:])
