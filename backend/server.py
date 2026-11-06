#!/usr/bin/env python3
"""Local Hive backend for ROS 2 manual robot control.

This backend is intended to run on Ubuntu 24.04 on the operator laptop. It
expects ROS 2 Jazzy to be installed at /opt/ros/jazzy.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import shlex
import subprocess
from typing import Any


HOST = os.environ.get("HIVE_BACKEND_HOST", "127.0.0.1")
PORT = int(os.environ.get("HIVE_BACKEND_PORT", "8787"))
ROS_SETUP = os.environ.get("HIVE_ROS_SETUP", "/opt/ros/jazzy/setup.bash")

IP_OR_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

TWIST_COMMANDS = {
    "forward": 'ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.5}, angular: {z: 0.0}}"',
    "backward": 'ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: -0.5}, angular: {z: 0.0}}"',
    "left": 'ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 1.0}}"',
    "right": 'ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: -1.0}}"',
    "stop": 'ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.0}}"',
}

GRIPPER_COMMANDS = {
    "gripper_open": 'ros2 topic pub --once -w 1 /gripper_open std_msgs/msg/Bool "{data: true}"',
    "gripper_close": 'ros2 topic pub --once -w 1 /gripper_open std_msgs/msg/Bool "{data: false}"',
}

CONTROL_COMMANDS = {**TWIST_COMMANDS, **GRIPPER_COMMANDS}


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


def ros_environment_command(peer: str, domain_id: int, ros_command: str) -> str:
    quoted_peer = shlex.quote(peer)
    quoted_setup = shlex.quote(ROS_SETUP)
    return " && ".join(
        [
            f"source {quoted_setup}",
            f"export ROS_DOMAIN_ID={domain_id}",
            "unset ROS_LOCALHOST_ONLY",
            "export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST",
            f"export ROS_STATIC_PEERS={quoted_peer}",
            ros_command,
        ]
    )


def run_ros_command(peer: str, domain_id: int, ros_command: str, timeout_seconds: int = 8) -> dict[str, Any]:
    peer = validate_peer(peer)
    domain_id = validate_domain_id(domain_id)
    command = ros_environment_command(peer, domain_id, ros_command)
    completed = subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
        "domain_id": domain_id,
    }


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
        result = run_ros_command(peer, domain_id, "ros2 node list", timeout_seconds=6)
        status = 200 if result["ok"] else 502
        self.write_json(status, result)

    def handle_control(self, body: dict[str, Any]) -> None:
        peer = validate_peer(str(body.get("ip", "")))
        domain_id = validate_domain_id(body.get("domain_id"))
        action = str(body.get("action", "")).strip()
        if action not in CONTROL_COMMANDS:
            raise ValueError(f"Unsupported control action: {action}")

        result = run_ros_command(peer, domain_id, CONTROL_COMMANDS[action])
        status = 200 if result["ok"] else 502
        self.write_json(status, {**result, "action": action})

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
    server = ThreadingHTTPServer((HOST, PORT), HiveRequestHandler)
    print(f"Hive backend listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
