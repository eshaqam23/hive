"""Worker-level safety checks that need the real worker process or class, run against the mock ROS."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.join(BACKEND, "test", "mock_ros")
sys.path.insert(0, BACKEND)


def worker_env(root):
    env = dict(os.environ)
    env.update(
        HIVE_MOCK_ROOT=root,
        PYTHONPATH=os.path.join(root, "python"),
        ROS_DOMAIN_ID="45",
        ROS_STATIC_PEERS="192.168.1.50",
    )
    return env


@pytest.fixture
def mock_copy(tmp_path):
    root = str(tmp_path / "mock_ros")
    shutil.copytree(MOCK, root, ignore=shutil.ignore_patterns("*.jsonl", "__pycache__"))
    os.makedirs(os.path.join(root, "robots"), exist_ok=True)
    return root


def robot_log(root):
    path = os.path.join(root, "robots", "192.168.1.50_45.jsonl")
    return [json.loads(l) for l in open(path) if l.strip()] if os.path.exists(path) else []


def test_stop_is_published_when_stderr_is_broken(mock_copy):
    """C1: the worker's stderr is a pipe nobody reads any more; closing stdin must still stop the robot."""
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BACKEND, "server.py"), "--robot-worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=worker_env(mock_copy),
    )
    assert json.loads(proc.stdout.readline())["ready"]
    proc.stderr.close()  # the reader of the worker's stderr is gone
    time.sleep(1.2)  # mock discovery
    proc.stdin.write(json.dumps({"id": 1, "op": "drive", "action": "forward", "power": 0.5}) + "\n")
    proc.stdin.flush()
    while True:
        reply = json.loads(proc.stdout.readline())
        if reply.get("id") == 1:
            break
    assert reply["ok"]
    time.sleep(0.3)
    proc.stdin.close()
    proc.wait(timeout=5)
    msgs = robot_log(mock_copy)
    last = msgs[-1]["msg"]
    assert "x: 0.0" in last and "z: 0.0" in last, last
    assert sum(1 for m in msgs if "x: 0.0" in m["msg"] and "z: 0.0" in m["msg"]) >= 5


def test_link_is_cleared_with_the_worker(monkeypatch):
    """M6: a restarted or stopped worker must not show the previous process's link."""
    import server

    monkeypatch.setattr(server, "ROS_SETUP", "/nonexistent/setup.bash")
    worker = server.RobotWorker("192.168.1.50", 45)
    worker.link = {"stop": "confirmed"}
    with pytest.raises(server.ApiError):
        worker.start()
    assert worker.link is None
    worker.link = {"stop": "confirmed"}
    worker.stop()
    assert worker.link is None


STALE_SCRIPT = """
import json, sys, time
sys.path.insert(0, BACKEND_DIR)
import server

loop = server.RobotWorkerLoop()
time.sleep(1.2)
loop.refresh_graph()


class M:
    def __init__(self, data):
        self.data = data


loop.start_driving("forward", 0.5)
loop.confirmer.stop_requested(time.monotonic())
loop.on_ros_data(M("0,1,0,1\\n"))
stale = loop.confirmer.poll(time.monotonic())[0]
loop.on_ros_data(M("100,1,100,1\\n"))
loop.on_ros_data(M("0,1,0,1\\n"))
real = loop.confirmer.poll(time.monotonic())[0]
print(json.dumps([stale, real]))
"""


def test_stale_zero_line_does_not_confirm_a_new_stop(mock_copy):
    """M1: after a drive starts, a zero line that arrives before any motion line cannot confirm a stop."""
    code = STALE_SCRIPT.replace("BACKEND_DIR", repr(BACKEND))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=worker_env(mock_copy), timeout=20)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["pending", "confirmed"], out.stderr


SEQ_SCRIPT = """
import json, sys
sys.path.insert(0, BACKEND_DIR)
import server

loop = server.RobotWorkerLoop()
print(json.dumps([loop.link_state(0.0)["seq"] for _ in range(3)]))
"""


def test_link_seq_grows_within_a_worker_and_across_workers(mock_copy):
    """R28: every link carries a seq; a newer link always has a higher one, even from a restarted worker,
    so the page can keep the newest link whatever route it came by."""
    code = SEQ_SCRIPT.replace("BACKEND_DIR", repr(BACKEND))
    runs = []
    for _ in range(2):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=worker_env(mock_copy), timeout=20)
        runs.append(json.loads(out.stdout.strip().splitlines()[-1]))
    first, second = runs
    assert all(isinstance(s, int) for s in first + second)
    assert first == sorted(set(first)) and second == sorted(set(second))
    assert second[0] > first[-1]


def read_until(proc, pred, seconds):
    """Worker stdout lines (as dicts) until pred(line) holds or the time is up."""
    import select
    seen, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not ready:
            continue
        line = json.loads(proc.stdout.readline())
        seen.append(line)
        if pred(line):
            return line, seen
    return None, seen


def test_a_new_worker_stops_the_robot_before_any_request(mock_copy):
    """R29: a fresh worker publishes the stop burst on start, and keeps resending until the car confirms."""
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BACKEND, "server.py"), "--robot-worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=worker_env(mock_copy),
    )
    try:
        confirmed, seen = read_until(proc, lambda l: (l.get("link") or {}).get("stop") == "confirmed", 4)
        zeros = [m for m in robot_log(mock_copy) if m["topic"] == "/cmd_vel"]
        assert seen[0].get("ready") is True
        assert zeros and all("x: 0.0" in m["msg"] and "z: 0.0" in m["msg"] for m in zeros), zeros
        assert confirmed is not None, seen  # the mock car echoes the zero Twist on /ros_data
        assert not any("id" in l for l in seen)  # no request was ever sent
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


def test_a_worker_killed_mid_drive_is_replaced_by_one_that_stops_the_car(mock_copy, monkeypatch):
    """Final audit #4: a hard crash (SIGKILL) sends no stop; the replacement worker must, and say so once."""
    import signal

    import server

    monkeypatch.setattr(server, "ROS_SETUP", os.path.join(mock_copy, "setup.bash"))
    worker = server.RobotWorker("192.168.1.50", 45)
    try:
        worker.start()
        assert worker.take_replaced() is False  # a first start replaces nothing
        worker.discover(with_topics=False)
        worker.request("drive", timeout=5, action="forward", power=0.5)
        time.sleep(0.4)
        old = worker.process.pid
        assert any("x: 0.25" in m["msg"] for m in robot_log(mock_copy) if m["pid"] == old)
        os.killpg(old, signal.SIGKILL)
        worker.process.wait(timeout=5)
        killed_at = time.monotonic()
        worker.start()
        assert worker.take_replaced() is True
        assert worker.take_replaced() is False  # reported once
        time.sleep(2.0)
        new = [m for m in robot_log(mock_copy) if m["topic"] == "/cmd_vel" and m["t"] > killed_at]
        assert new and all(m["pid"] != old for m in new)
        assert all("x: 0.0" in m["msg"] and "z: 0.0" in m["msg"] for m in new), new
    finally:
        worker.stop()
