#!/usr/bin/env python3
"""Local Hive backend for ROS 2 robot control.

Runs on the operator laptop (Ubuntu 24.04, ROS 2 Jazzy at /opt/ros/jazzy) and drives robots with the
`ros2` CLI, scoped to one robot at a time by ROS_DOMAIN_ID and ROS_STATIC_PEERS.

Safety model
  * One command channel per robot and topic group. A new command kills any older process still in
    flight on that channel, so the newest command always wins: a stop can never be overtaken by a
    drive command that was still discovering the robot.
  * Driving is hold-to-drive: /cmd_vel is published at DRIVE_RATE_HZ while the operator holds a
    control. The page sends a keepalive every 250 ms; if keepalives stop for DEADMAN_SECONDS (tab
    closed, browser crashed, Wi-Fi dropped) the backend stops the robot itself.
  * Every stop kills the stream first, then publishes a zero Twist and waits for it to be delivered.
  * Only the Hive web app's own origins may call the API (HIVE_ALLOWED_ORIGINS), so another website
    open in the same browser cannot drive a robot.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any

HOST = os.environ.get("HIVE_BACKEND_HOST", "127.0.0.1")
PORT = int(os.environ.get("HIVE_BACKEND_PORT", "8787"))
ROS_SETUP = os.environ.get("HIVE_ROS_SETUP", "/opt/ros/jazzy/setup.bash")
ALLOWED_ORIGINS = {
    o.strip()
    for o in os.environ.get(
        "HIVE_ALLOWED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173",
    ).split(",")
    if o.strip()
}

CMD_VEL = "/cmd_vel"
GRIPPER_TOPIC = "/gripper_open"
COMMAND_TOPIC = os.environ.get("HIVE_COMMAND_TOPIC", "/hive/command")
DRIVE_RATE_HZ = 10
DEADMAN_SECONDS = 1.0
SUBSCRIBER_WAIT_SECONDS = 4      # how long a one-shot publish waits for the robot to be listening
DISCOVERY_SPIN_SECONDS = 2.5     # how long connect / status listen for the robot's nodes and topics
MAX_COMMAND_LENGTH = 500
MAX_BODY_BYTES = 64 * 1024

IP_OR_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
SPLIT = "__HIVE_SPLIT__"

LINEAR_SPEED = 0.5
ANGULAR_SPEED = 1.0
TWISTS = {
    "forward": (LINEAR_SPEED, 0.0),
    "backward": (-LINEAR_SPEED, 0.0),
    "left": (0.0, ANGULAR_SPEED),
    "right": (0.0, -ANGULAR_SPEED),
    "stop": (0.0, 0.0),
}
GRIPPER = {"gripper_open": "true", "gripper_close": "false"}
CONTROL_ACTIONS = set(TWISTS) | set(GRIPPER)


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ------------------------------------------------------------------ validation


def validate_peer(peer: Any) -> str:
    peer = str(peer or "").strip()
    if not peer or not IP_OR_HOST_RE.match(peer):
        raise ApiError(400, "A valid robot IP address or hostname is required.")
    return peer


def validate_domain_id(domain_id: Any) -> int:
    try:
        parsed = int(domain_id)
    except (TypeError, ValueError):
        raise ApiError(400, "A valid ROS domain ID is required.") from None
    if parsed < 0 or parsed > 232:
        raise ApiError(400, "ROS domain ID must be between 0 and 232.")
    return parsed


def ros_installed() -> bool:
    return os.path.isfile(ROS_SETUP)


def require_ros() -> None:
    if not ros_installed():
        raise ApiError(
            503,
            f"ROS 2 was not found at {ROS_SETUP}. Install ROS 2 Jazzy, or point HIVE_ROS_SETUP at your setup.bash.",
        )


def twist(action: str) -> str:
    linear, angular = TWISTS[action]
    return f"{{linear: {{x: {linear}}}, angular: {{z: {angular}}}}}"


# ------------------------------------------------------------------ running ros2


def ros_script(peer: str, domain_id: int, command: str) -> str:
    """The shell script for one ros2 call, scoped to one robot. Non-login bash, so the operator's
    own profile cannot change the ROS environment underneath us."""
    return " && ".join(
        [
            f"source {shlex.quote(ROS_SETUP)}",
            f"export ROS_DOMAIN_ID={domain_id}",
            "unset ROS_LOCALHOST_ONLY",
            "export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST",
            f"export ROS_STATIC_PEERS={shlex.quote(peer)}",
            command,
        ]
    )


def spawn(peer: str, domain_id: int, command: str) -> subprocess.Popen:
    # its own process group, so the whole tree (bash and ros2) can be killed together
    return subprocess.Popen(
        ["bash", "-c", ros_script(peer, domain_id, command)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def kill(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=1.5)
    except (ProcessLookupError, PermissionError):
        pass
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def explain(stderr: str, topic: str) -> str:
    text = (stderr or "").strip()
    if "Timed out waiting for subscribers" in text:
        if topic == CMD_VEL:
            return f"Nothing on the robot is listening on {topic}. Check that its driver node is running."
        return f"Nothing on the robot is listening on {topic}. Check that a node on the robot subscribes to it."
    if "No such file or directory" in text and "setup.bash" in text:
        return f"ROS 2 was not found at {ROS_SETUP}."
    if "command not found" in text and "ros2" in text:
        return "The ros2 command was not found after sourcing ROS 2."
    return text.splitlines()[-1] if text else "The ros2 command failed."


# ------------------------------------------------------------------ per-robot channels


class Channel:
    """Serialises the commands for one robot on one topic group. The newest command wins: starting a
    command kills whatever older process is still in flight on this channel."""

    def __init__(self, key: tuple[str, int, str]):
        self.peer, self.domain_id, self.name = key
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.generation = 0
        self.streaming_action: str | None = None
        self.last_keepalive = 0.0

    def _replace(self, command: str) -> tuple[subprocess.Popen, int]:
        kill(self.proc)
        self.generation += 1
        self.proc = spawn(self.peer, self.domain_id, command)
        return self.proc, self.generation

    def publish_once(self, topic: str, msg_type: str, msg: str) -> dict[str, Any]:
        """Publish one message and wait until the robot has received it (or report why not)."""
        command = (
            f"exec ros2 topic pub --once -w 1 --max-wait-time-secs {SUBSCRIBER_WAIT_SECONDS} -p 0 "
            f"-n hive_{self.name} {shlex.quote(topic)} {msg_type} {shlex.quote(msg)}"
        )
        with self.lock:
            self.streaming_action = None
            proc, generation = self._replace(command)
        try:
            _, stderr = proc.communicate(timeout=SUBSCRIBER_WAIT_SECONDS + 6)
        except subprocess.TimeoutExpired:
            kill(proc)
            raise ApiError(504, f"Publishing to {topic} timed out.") from None
        if generation != self.generation:
            raise ApiError(409, "A newer command replaced this one.", superseded=True)
        if proc.returncode != 0:
            raise ApiError(502, explain(stderr, topic))
        return {"ok": True, "topic": topic}

    def start_stream(self, action: str) -> dict[str, Any]:
        command = (
            f"exec ros2 topic pub -r {DRIVE_RATE_HZ} -w 1 --max-wait-time-secs {SUBSCRIBER_WAIT_SECONDS} -p 0 "
            f"-n hive_teleop {CMD_VEL} geometry_msgs/msg/Twist {shlex.quote(twist(action))}"
        )
        with self.lock:
            self._replace(command)
            self.streaming_action = action
            self.last_keepalive = time.monotonic()
        return {"ok": True, "streaming": True, "action": action}

    def keepalive(self) -> dict[str, Any]:
        with self.lock:
            proc = self.proc
            if self.streaming_action is None or proc is None:
                return {"ok": True, "streaming": False}
            self.last_keepalive = time.monotonic()
            if proc.poll() is None:
                return {"ok": True, "streaming": True, "action": self.streaming_action}
            # the stream ended by itself: usually nothing on the robot listens on /cmd_vel
            self.streaming_action = None
            _, stderr = proc.communicate()
        raise ApiError(502, explain(stderr, CMD_VEL), streaming=False)

    def stop(self) -> dict[str, Any]:
        """Kill the stream, then publish a zero Twist and wait for delivery."""
        result = self.publish_once(CMD_VEL, "geometry_msgs/msg/Twist", twist("stop"))
        return {**result, "streaming": False, "action": "stop"}

    def deadman_expired(self) -> bool:
        return (
            self.streaming_action is not None
            and time.monotonic() - self.last_keepalive > DEADMAN_SECONDS
        )

    def shutdown(self) -> None:
        with self.lock:
            self.streaming_action = None
            kill(self.proc)


channels: dict[tuple[str, int, str], Channel] = {}
channels_lock = threading.Lock()


def channel(peer: str, domain_id: int, name: str) -> Channel:
    key = (peer, domain_id, name)
    with channels_lock:
        if key not in channels:
            channels[key] = Channel(key)
        return channels[key]


def deadman_watch() -> None:
    while True:
        time.sleep(0.2)
        with channels_lock:
            expired = [c for c in channels.values() if c.name == "drive" and c.deadman_expired()]
        for c in expired:
            c.streaming_action = None  # claim it so the next tick does not stop it twice
            print(f"[hive-backend] deadman: no keepalive from the page, stopping {c.peer} (domain {c.domain_id})")
            threading.Thread(target=safe_stop, args=(c,), daemon=True).start()


def safe_stop(c: Channel) -> None:
    try:
        c.stop()
    except ApiError as error:
        if not error.extra.get("superseded"):
            print(f"[hive-backend] stop for {c.peer} failed: {error.message}")


# ------------------------------------------------------------------ discovery


def discover(peer: str, domain_id: int, *, with_topics: bool = True) -> dict[str, Any]:
    """Look for the robot's nodes, topics and /cmd_vel listeners, without the ros2 daemon (a daemon
    started earlier would remember an old peer list). The three queries run in parallel."""
    spin = DISCOVERY_SPIN_SECONDS
    parts = [f"ros2 node list --no-daemon --spin-time {spin}"]
    if with_topics:
        parts += [
            f"ros2 topic list -t --no-daemon --spin-time {spin}",
            f"ros2 topic info {CMD_VEL} --no-daemon --spin-time {spin}",
        ]
    runs = " ".join(f'( {p} > "$T/{i}" 2>/dev/null ) &' for i, p in enumerate(parts))
    reads = f"; echo {SPLIT}; ".join(f'cat "$T/{i}"' for i in range(len(parts)))
    # one subshell, so it stays inside ros_script's && chain: if sourcing ROS fails, nothing runs
    script = f'( T="$(mktemp -d)"; {runs} wait; {reads}; rm -rf "$T" )'
    proc = spawn(peer, domain_id, script)
    try:
        stdout, stderr = proc.communicate(timeout=spin * 2 + 10)
    except subprocess.TimeoutExpired:
        kill(proc)
        raise ApiError(504, "Discovery timed out.") from None
    if proc.returncode != 0:
        raise ApiError(502, explain(stderr, CMD_VEL))

    sections = stdout.split(SPLIT) + ["", "", ""]
    nodes = [line.strip() for line in sections[0].splitlines() if line.strip().startswith("/")]
    topics, cmd_vel_subscribers = [], 0
    if with_topics:
        for line in sections[1].splitlines():
            m = re.match(r"^(/\S+)\s+\[(.+)\]$", line.strip())
            if m:
                topics.append({"name": m.group(1), "type": m.group(2)})
        m = re.search(r"Subscription count:\s*(\d+)", sections[2])
        cmd_vel_subscribers = int(m.group(1)) if m else 0
    return {"nodes": nodes, "topics": topics, "cmd_vel_subscribers": cmd_vel_subscribers}


# ------------------------------------------------------------------ http


class HiveRequestHandler(BaseHTTPRequestHandler):
    server_version = "hive-backend"

    def origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_ORIGINS  # no Origin: curl, scripts, the same machine

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204 if self.origin_allowed() else 403)
        self.end_headers()

    def do_GET(self) -> None:
        if not self.origin_allowed():
            return self.write_json(403, {"ok": False, "error": "This origin may not use the Hive backend."})
        if self.path == "/api/health":
            return self.write_json(200, {"ok": True, "service": "hive-backend", "ros": {"setup": ROS_SETUP, "found": ros_installed()}})
        self.write_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        routes = {
            "/api/robots/connect": self.handle_connect,
            "/api/robots/status": self.handle_status,
            "/api/robots/control": self.handle_control,
            "/api/robots/drive": self.handle_drive,
            "/api/robots/drive/keepalive": self.handle_keepalive,
            "/api/robots/command": self.handle_command,
            "/api/robots/disconnect": self.handle_disconnect,
        }
        try:
            if not self.origin_allowed():
                raise ApiError(403, "This origin may not use the Hive backend.")
            handler = routes.get(self.path)
            if handler is None:
                raise ApiError(404, "Not found")
            body = self.read_json()
            peer = validate_peer(body.get("ip"))
            domain_id = validate_domain_id(body.get("domain_id"))
            self.write_json(200, {"domain_id": domain_id, "peer": peer, **handler(body, peer, domain_id)})
        except ApiError as error:
            self.write_json(error.status, {"ok": False, "error": error.message, **error.extra})
        except Exception as error:  # noqa: BLE001 - the API always answers in JSON
            self.write_json(500, {"ok": False, "error": f"Unexpected backend error: {error}"})

    # -------------------------------------------------------------- routes

    def handle_connect(self, body: dict, peer: str, domain_id: int) -> dict:
        require_ros()
        found = discover(peer, domain_id)
        if not found["nodes"] and not found["topics"]:
            raise ApiError(
                502,
                f"No ROS 2 nodes answered at {peer} on domain {domain_id}. Check that the robot is on, on this "
                f"network, and running with ROS_DOMAIN_ID={domain_id}.",
                **found,
            )
        warnings = []
        if found["cmd_vel_subscribers"] == 0:
            warnings.append(f"Nothing on the robot is listening on {CMD_VEL}, so drive commands will not move it. Check its driver node.")
        return {"ok": True, **found, "warnings": warnings}

    def handle_status(self, body: dict, peer: str, domain_id: int) -> dict:
        require_ros()
        found = discover(peer, domain_id, with_topics=False)
        if not found["nodes"]:
            raise ApiError(502, f"Lost contact: no ROS 2 nodes answered at {peer} on domain {domain_id}.", nodes=[])
        return {"ok": True, "nodes": found["nodes"]}

    def handle_control(self, body: dict, peer: str, domain_id: int) -> dict:
        """One-shot commands (the drive buttons' original behaviour, the gripper, stop)."""
        require_ros()
        action = str(body.get("action", "")).strip()
        if action not in CONTROL_ACTIONS:
            raise ApiError(400, f"Unsupported control action: {action}")
        if action in GRIPPER:
            result = channel(peer, domain_id, "gripper").publish_once(GRIPPER_TOPIC, "std_msgs/msg/Bool", f"{{data: {GRIPPER[action]}}}")
        elif action == "stop":
            result = channel(peer, domain_id, "drive").stop()
        else:
            result = channel(peer, domain_id, "drive").publish_once(CMD_VEL, "geometry_msgs/msg/Twist", twist(action))
        return {**result, "action": action}

    def handle_drive(self, body: dict, peer: str, domain_id: int) -> dict:
        """Hold-to-drive: start (or switch) a /cmd_vel stream, or stop it."""
        require_ros()
        action = str(body.get("action", "")).strip()
        if action not in TWISTS:
            raise ApiError(400, f"Unsupported drive action: {action}")
        drive = channel(peer, domain_id, "drive")
        return drive.stop() if action == "stop" else drive.start_stream(action)

    def handle_keepalive(self, body: dict, peer: str, domain_id: int) -> dict:
        return channel(peer, domain_id, "drive").keepalive()

    def handle_command(self, body: dict, peer: str, domain_id: int) -> dict:
        require_ros()
        text = str(body.get("text", "")).strip()
        if not text:
            raise ApiError(400, "The command is empty.")
        if len(text) > MAX_COMMAND_LENGTH:
            raise ApiError(400, f"Commands are limited to {MAX_COMMAND_LENGTH} characters.")
        # a JSON string is a valid YAML double-quoted scalar, so any text survives intact
        msg = f"{{data: {json.dumps(text)}}}"
        return channel(peer, domain_id, "command").publish_once(COMMAND_TOPIC, "std_msgs/msg/String", msg)

    def handle_disconnect(self, body: dict, peer: str, domain_id: int) -> dict:
        """Stop the robot before letting it go. Best effort: an unreachable robot still disconnects."""
        drive = channel(peer, domain_id, "drive")
        if not ros_installed():
            drive.shutdown()
            return {"ok": True, "stopped": False}
        try:
            drive.stop()
            return {"ok": True, "stopped": True}
        except ApiError as error:
            drive.shutdown()
            return {"ok": True, "stopped": False, "warning": error.message}

    # -------------------------------------------------------------- io

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "Request body is too large.")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "The request body is not valid JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(400, "The request body must be a JSON object.")
        return body

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        if "/keepalive" in (self.path or ""):
            return  # four a second while driving; not worth a log line each
        print(f"[hive-backend] {self.address_string()} - {format % args}")


def stop_everything(*_: Any) -> None:
    """On shutdown, stop every robot that is still streaming, then exit."""
    with channels_lock:
        active = [c for c in channels.values() if c.name == "drive" and c.streaming_action]
        everything = list(channels.values())
    for c in active:
        print(f"[hive-backend] shutting down: stopping {c.peer} (domain {c.domain_id})")
        safe_stop(c)
    for c in everything:
        c.shutdown()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, stop_everything)
    signal.signal(signal.SIGINT, stop_everything)
    threading.Thread(target=deadman_watch, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), HiveRequestHandler)
    server.daemon_threads = True
    ros = "found" if ros_installed() else "NOT FOUND"
    print(f"Hive backend listening on http://{HOST}:{PORT} (ROS 2 setup {ROS_SETUP}: {ros})")
    print(f"Allowed origins: {', '.join(sorted(ALLOWED_ORIGINS))}")
    server.serve_forever()


if __name__ == "__main__":
    main()
