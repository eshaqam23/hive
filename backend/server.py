#!/usr/bin/env python3
"""Local Hive backend for ROS 2 manual robot control.

This backend is intended to run on Ubuntu 24.04 on the operator laptop. It
expects ROS 2 Jazzy to be installed at /opt/ros/jazzy.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from typing import Any


HOST = os.environ.get("HIVE_BACKEND_HOST", "127.0.0.1")
PORT = int(os.environ.get("HIVE_BACKEND_PORT", "8787"))

IP_OR_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

TWIST_VALUES = {
    "forward": {"linear_x": 0.5, "angular_z": 0.0},
    "backward": {"linear_x": -0.5, "angular_z": 0.0},
    "left": {"linear_x": 0.0, "angular_z": 1.0},
    "right": {"linear_x": 0.0, "angular_z": -1.0},
    "stop": {"linear_x": 0.0, "angular_z": 0.0},
}

GRIPPER_VALUES = {"gripper_open": "open", "gripper_close": "close"}
CONTROL_ACTIONS = {*TWIST_VALUES.keys(), *GRIPPER_VALUES.keys()}
ROBOT_WORKERS: dict[tuple[str, int], "RobotWorker"] = {}
ROBOT_WORKERS_LOCK = threading.Lock()


def validate_peer(peer: str) -> str:
    peer = (peer or "").strip()
    if not peer or not IP_OR_HOST_RE.match(peer):
        raise ValueError("A valid robot IP address or hostname is required.")
    return peer


def validate_domain_id(domain_id: Any) -> int:
    try:
        parsed = int(domain_id)
    except (TypeError, ValueError) as error:
        raise ValueError("A valid ROS domain ID is required.") from error

    if parsed < 0 or parsed > 232:
        raise ValueError("ROS domain ID must be between 0 and 232.")
    return parsed


def worker_key(peer: str, domain_id: int) -> tuple[str, int]:
    return (validate_peer(peer), validate_domain_id(domain_id))


class RobotWorker:
    def __init__(self, peer: str, domain_id: int) -> None:
        self.peer = validate_peer(peer)
        self.domain_id = validate_domain_id(domain_id)
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self.is_running():
            return

        env = os.environ.copy()
        env["ROS_DOMAIN_ID"] = str(self.domain_id)
        env.pop("ROS_LOCALHOST_ONLY", None)
        env["ROS_AUTOMATIC_DISCOVERY_RANGE"] = "LOCALHOST"
        env["ROS_STATIC_PEERS"] = self.peer

        self.process = subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                "--robot-worker",
                "--peer",
                self.peer,
                "--domain-id",
                str(self.domain_id),
            ],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        self.wait_until_ready()

    def wait_until_ready(self, timeout_seconds: float = 10.0) -> None:
        if not self.process or not self.process.stdout:
            raise RuntimeError("Robot worker failed to start.")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Robot worker exited with code {self.process.returncode}.")

            readable, _, _ = select.select([self.process.stdout], [], [], 0.1)
            if not readable:
                continue

            line = self.process.stdout.readline().strip()
            if not line:
                continue

            payload = json.loads(line)
            if payload.get("ok"):
                return
            raise RuntimeError(payload.get("error", "Robot worker failed to initialize."))

        self.stop()
        raise RuntimeError("Robot worker timed out while starting.")

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def send_action(self, action: str) -> None:
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"Unsupported control action: {action}")
        if not self.is_running() or not self.process or not self.process.stdin:
            raise RuntimeError("Robot worker is not connected.")

        self.process.stdin.write(json.dumps({"action": action}) + "\n")
        self.process.stdin.flush()

    def stop(self) -> None:
        if not self.process:
            return

        if self.is_running():
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


def get_or_start_worker(peer: str, domain_id: int) -> RobotWorker:
    key = worker_key(peer, domain_id)
    with ROBOT_WORKERS_LOCK:
        worker = ROBOT_WORKERS.get(key)
        if worker is None:
            worker = RobotWorker(peer=key[0], domain_id=key[1])
            ROBOT_WORKERS[key] = worker
        worker.start()
        return worker


def stop_worker(peer: str, domain_id: int) -> bool:
    key = worker_key(peer, domain_id)
    with ROBOT_WORKERS_LOCK:
        worker = ROBOT_WORKERS.pop(key, None)
    if not worker:
        return False
    worker.stop()
    return True


def stop_all_workers() -> None:
    with ROBOT_WORKERS_LOCK:
        workers = list(ROBOT_WORKERS.values())
        ROBOT_WORKERS.clear()
    for worker in workers:
        worker.stop()


class HiveRequestHandler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.write_json(200, {"ok": True, "service": "hive-backend"})
            return
        self.write_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        try:
            body = self.read_json()
            if self.path == "/api/robots/connect":
                self.handle_connect(body)
            elif self.path == "/api/robots/control":
                self.handle_control(body)
            elif self.path == "/api/robots/disconnect":
                self.handle_disconnect(body)
            else:
                self.write_json(404, {"ok": False, "error": "Not found"})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except subprocess.TimeoutExpired:
            self.write_json(504, {"ok": False, "error": "ROS command timed out."})
        except Exception as error:  # noqa: BLE001 - API should return JSON failures.
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_connect(self, body: dict[str, Any]) -> None:
        peer = validate_peer(str(body.get("ip", "")))
        domain_id = validate_domain_id(body.get("domain_id"))
        get_or_start_worker(peer, domain_id)
        self.write_json(200, {"ok": True, "ip": peer, "domain_id": domain_id, "mode": "persistent_worker"})

    def handle_control(self, body: dict[str, Any]) -> None:
        peer = validate_peer(str(body.get("ip", "")))
        domain_id = validate_domain_id(body.get("domain_id"))
        action = str(body.get("action", "")).strip()
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"Unsupported control action: {action}")

        worker = get_or_start_worker(peer, domain_id)
        worker.send_action(action)
        self.write_json(200, {"ok": True, "ip": peer, "domain_id": domain_id, "action": action})

    def handle_disconnect(self, body: dict[str, Any]) -> None:
        peer = validate_peer(str(body.get("ip", "")))
        domain_id = validate_domain_id(body.get("domain_id"))
        stopped = stop_worker(peer, domain_id)
        self.write_json(200, {"ok": True, "ip": peer, "domain_id": domain_id, "stopped": stopped})

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw_body = self.rfile.read(content_length)
        return json.loads(raw_body.decode("utf-8"))

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[hive-backend] {self.address_string()} - {format % args}")


def main() -> None:
    signal.signal(signal.SIGINT, lambda _signum, _frame: (stop_all_workers(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (stop_all_workers(), sys.exit(0)))
    server = ThreadingHTTPServer((HOST, PORT), HiveRequestHandler)
    print(f"Hive backend listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    finally:
        stop_all_workers()


def publish_twist(publisher: Any, twist_type: Any, action: str) -> None:
    values = TWIST_VALUES[action]
    message = twist_type()
    message.linear.x = values["linear_x"]
    message.angular.z = values["angular_z"]
    publisher.publish(message)


def publish_gripper(publisher: Any, string_type: Any, action: str) -> None:
    message = string_type()
    message.data = GRIPPER_VALUES[action]
    publisher.publish(message)


def run_robot_worker(peer: str, domain_id: int) -> None:
    peer = validate_peer(peer)
    domain_id = validate_domain_id(domain_id)

    os.environ["ROS_DOMAIN_ID"] = str(domain_id)
    os.environ.pop("ROS_LOCALHOST_ONLY", None)
    os.environ["ROS_AUTOMATIC_DISCOVERY_RANGE"] = "LOCALHOST"
    os.environ["ROS_STATIC_PEERS"] = peer

    try:
        import rclpy
        from geometry_msgs.msg import Twist
        from std_msgs.msg import String

        rclpy.init(args=None)
        node = rclpy.create_node(f"hive_controller_domain_{domain_id}")
        cmd_vel_publisher = node.create_publisher(Twist, "/cmd_vel", 10)
        gripper_publisher = node.create_publisher(String, "/gripper_command", 10)

        print(json.dumps({"ok": True, "domain_id": domain_id, "ip": peer}), flush=True)

        for line in sys.stdin:
            if not line.strip():
                continue
            payload = json.loads(line)
            action = str(payload.get("action", "")).strip()
            if action in TWIST_VALUES:
                publish_twist(cmd_vel_publisher, Twist, action)
            elif action in GRIPPER_VALUES:
                publish_gripper(gripper_publisher, String, action)
            else:
                print(json.dumps({"ok": False, "error": f"Unsupported action: {action}"}), flush=True)
            rclpy.spin_once(node, timeout_sec=0.0)

        node.destroy_node()
        rclpy.shutdown()
    except Exception as error:  # noqa: BLE001 - worker reports initialization failures as JSON.
        print(json.dumps({"ok": False, "error": str(error)}), flush=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hive backend")
    parser.add_argument("--robot-worker", action="store_true", help="Run as a persistent per-robot ROS worker")
    parser.add_argument("--peer", default="", help="Robot IP address or hostname")
    parser.add_argument("--domain-id", type=int, default=42, help="ROS domain ID for this worker")
    args = parser.parse_args()

    if args.robot_worker:
        run_robot_worker(args.peer, args.domain_id)
    else:
        main()
