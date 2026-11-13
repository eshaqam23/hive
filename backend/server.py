#!/usr/bin/env python3
"""Local Hive backend for ROS 2 robot control.

Runs on the operator laptop (Ubuntu 24.04, ROS 2 Jazzy at /opt/ros/jazzy). Each robot gets one
persistent worker process with a long-lived rclpy node, scoped to that robot by ROS_DOMAIN_ID and
ROS_STATIC_PEERS. Commands reach the worker over a pipe, so a button press never waits for DDS to
discover the robot again.

Safety model
  * Commands for one robot are applied in order. The page numbers its drive and gripper commands; one
    that arrives after a newer one is rejected (409) instead of overriding it, so a late drive
    command can never undo a stop.
  * Hold-to-drive: while the operator holds a control, the worker publishes /cmd_vel at
    DRIVE_RATE_HZ. The page sends a keepalive every 250 ms; if keepalives stop for DEADMAN_SECONDS
    (tab closed, browser crashed, Wi-Fi dropped) the worker stops the robot itself.
  * A stop is published at once, not on the next tick.
  * Disconnecting, shutting the backend down, or the backend dying stops the robot: a worker that
    loses its pipe to the backend publishes a zero Twist before it exits.
  * Only the Hive web app's own origins may call the API (HIVE_ALLOWED_ORIGINS), so another website
    open in the same browser cannot drive a robot.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
import queue
import re
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
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
DRIVE_RATE_HZ = 20
DEADMAN_SECONDS = 1.0
SUBSCRIBER_WAIT_SECONDS = 4      # how long a gripper or command-log publish waits for a listener
DISCOVERY_SPIN_SECONDS = 2.5     # how long a new worker listens for the robot before giving up
WORKER_START_SECONDS = 15
MAX_COMMAND_LENGTH = 500
MAX_BODY_BYTES = 64 * 1024

IP_OR_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

LINEAR_SPEED = 0.5
ANGULAR_SPEED = 1.0
TWISTS = {
    "forward": (LINEAR_SPEED, 0.0),
    "backward": (-LINEAR_SPEED, 0.0),
    "left": (0.0, ANGULAR_SPEED),
    "right": (0.0, -ANGULAR_SPEED),
    "stop": (0.0, 0.0),
}
GRIPPER = {"gripper_open": True, "gripper_close": False}
CONTROL_ACTIONS = set(TWISTS) | set(GRIPPER)


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def no_listener(topic: str) -> str:
    if topic == CMD_VEL:
        return f"Nothing on the robot is listening on {topic}. Check that its driver node is running."
    return f"Nothing on the robot is listening on {topic}. Check that a node on the robot subscribes to it."


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


def validate_order(body: dict) -> tuple[str | None, int | None]:
    """The page's tab id and command number, used to drop commands that arrive out of order."""
    session = str(body.get("session") or "")[:64] or None
    seq = body.get("seq")
    if seq is None:
        return session, None
    try:
        return session, int(seq)
    except (TypeError, ValueError):
        raise ApiError(400, "The command number (seq) must be an integer.") from None


def ros_installed() -> bool:
    return os.path.isfile(ROS_SETUP)


def require_ros() -> None:
    if not ros_installed():
        raise ApiError(
            503,
            f"ROS 2 was not found at {ROS_SETUP}. Install ROS 2 Jazzy, or point HIVE_ROS_SETUP at your setup.bash.",
            code="no_ros",
        )


# ------------------------------------------------------------------ per-robot workers (backend side)


class RobotWorker:
    """The backend's handle on one robot's worker process. Requests are JSON lines on the worker's
    stdin; each reply is a JSON line on its stdout carrying the request's id."""

    def __init__(self, peer: str, domain_id: int) -> None:
        self.peer, self.domain_id = peer, domain_id
        self.process: subprocess.Popen[str] | None = None
        self.start_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.replies_lock = threading.Lock()
        self.waiting: dict[int, threading.Event] = {}
        self.replies: dict[int, dict[str, Any]] = {}
        self.next_id = 0
        self.last_order: dict[str, tuple[str | None, int]] = {}  # channel -> newest (session, seq) applied
        self.discovered = threading.Event()  # set once DDS has had time to find the robot

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        with self.start_lock:
            if self.is_running():
                return
            require_ros()
            self.discovered.clear()
            # Non-login bash, so the operator's own profile cannot change the ROS environment. Its own
            # session, so Ctrl+C in the terminal reaches only the backend, which then stops the robot.
            script = " && ".join(
                [
                    f"source {shlex.quote(ROS_SETUP)}",
                    f"export ROS_DOMAIN_ID={self.domain_id}",
                    "unset ROS_LOCALHOST_ONLY",
                    "export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST",
                    f"export ROS_STATIC_PEERS={shlex.quote(self.peer)}",
                    f"exec python3 {shlex.quote(os.path.abspath(__file__))} --robot-worker",
                ]
            )
            process = subprocess.Popen(
                ["bash", "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            try:
                self._wait_until_ready(process)
            except ApiError:
                self._end(process)
                raise
            self.process = process
            threading.Thread(target=self._read_replies, args=(process,), daemon=True).start()

    def _wait_until_ready(self, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + WORKER_START_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ApiError(
                    502,
                    f"The ROS 2 worker for {self.peer} exited (code {process.returncode}). See the backend terminal.",
                    code="worker_failed",
                )
            readable, _, _ = select.select([process.stdout], [], [], 0.1)
            if not readable:
                continue
            line = process.stdout.readline().strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("ready"):
                return
            raise ApiError(502, f"The ROS 2 worker for {self.peer} failed to start: {payload.get('error')}", code="worker_failed")
        raise ApiError(504, f"The ROS 2 worker for {self.peer} took too long to start.", code="worker_failed")

    def _read_replies(self, process: subprocess.Popen[str]) -> None:
        for line in process.stdout:
            try:
                reply = json.loads(line)
            except ValueError:
                reply = None
            if not isinstance(reply, dict) or "id" not in reply:
                # DDS and ROS libraries can print to stdout too; show it rather than choke on it
                print(f"[hive-worker {self.peer}] {line.rstrip()}", flush=True)
                continue
            with self.replies_lock:
                event = self.waiting.pop(reply.get("id"), None)
                if event:
                    self.replies[reply["id"]] = reply
            if event:
                event.set()
        # the worker is gone: wake every request still waiting, so each reports it
        if self.process is process:
            try:
                code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                code = "unknown"
            print(f"[hive-backend] the ROS 2 worker for {self.peer} (domain {self.domain_id}) exited unexpectedly (code {code})", flush=True)
        with self.replies_lock:
            events = list(self.waiting.values())
            self.waiting.clear()
        for event in events:
            event.set()

    def request(
        self,
        op: str,
        *,
        timeout: float,
        channel: str | None = None,
        session: str | None = None,
        seq: int | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Send one request and wait for its reply. Numbered requests on a channel (drive, gripper) that
        arrive after a newer one from the same tab are rejected rather than applied out of order."""
        with self.write_lock:
            process = self.process
            if process is None or process.poll() is not None or process.stdin is None:
                raise ApiError(502, "The ROS 2 worker for this robot is not running.", code="worker_gone")
            if channel and seq is not None:
                last_session, last_seq = self.last_order.get(channel, (None, -1))
                if session == last_session and seq <= last_seq:
                    raise ApiError(409, "A newer command replaced this one.", superseded=True, code="superseded")
                self.last_order[channel] = (session, seq)
            self.next_id += 1
            request_id = self.next_id
            event = threading.Event()
            with self.replies_lock:
                self.waiting[request_id] = event
            try:
                process.stdin.write(json.dumps({"id": request_id, "op": op, **params}) + "\n")
                process.stdin.flush()
            except OSError:
                with self.replies_lock:
                    self.waiting.pop(request_id, None)
                raise ApiError(502, "Lost the ROS 2 worker for this robot.", code="worker_gone") from None

        if not event.wait(timeout):
            with self.replies_lock:
                self.waiting.pop(request_id, None)
            raise ApiError(504, f"The ROS 2 worker did not answer in time ({op}).", code="timeout")
        with self.replies_lock:
            reply = self.replies.pop(request_id, None)
        if reply is None:
            raise ApiError(502, "The ROS 2 worker for this robot exited. See the backend terminal.", code="worker_gone")
        if not reply.get("ok"):
            raise ApiError(reply.get("status", 502), reply.get("error", "The ROS 2 worker failed."), **reply.get("extra", {}))
        return {k: v for k, v in reply.items() if k not in ("id", "ok")}

    def discover(self, *, with_topics: bool) -> dict[str, Any]:
        """The robot's nodes, topics and /cmd_vel listeners. A new worker first gives DDS up to
        DISCOVERY_SPIN_SECONDS to find the robot, stopping early once something listens on /cmd_vel."""
        wait = 0.0 if self.discovered.is_set() else DISCOVERY_SPIN_SECONDS
        found = self.request("discover", timeout=wait + 5, wait=wait, topics=with_topics)
        self.discovered.set()
        return found

    def wait_for_discovery(self) -> None:
        if not self.discovered.is_set():
            self.discover(with_topics=False)

    def stop(self) -> None:
        """End the worker. Closing its stdin makes it publish a zero Twist before it exits."""
        with self.start_lock:
            process, self.process = self.process, None
            if process:
                self._end(process)

    def _end(self, process: subprocess.Popen[str]) -> None:
        with self.write_lock:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()


WORKERS: dict[tuple[str, int], RobotWorker] = {}
WORKERS_LOCK = threading.Lock()


def get_worker(peer: str, domain_id: int) -> RobotWorker:
    with WORKERS_LOCK:
        worker = WORKERS.get((peer, domain_id))
        if worker is None:
            worker = WORKERS[(peer, domain_id)] = RobotWorker(peer, domain_id)
    worker.start()
    return worker


def existing_worker(peer: str, domain_id: int) -> RobotWorker | None:
    with WORKERS_LOCK:
        worker = WORKERS.get((peer, domain_id))
    return worker if worker and worker.is_running() else None


def drop_worker(peer: str, domain_id: int) -> RobotWorker | None:
    with WORKERS_LOCK:
        return WORKERS.pop((peer, domain_id), None)


def stop_all_workers() -> None:
    with WORKERS_LOCK:
        workers = list(WORKERS.values())
        WORKERS.clear()
    for worker in workers:
        if worker.is_running():
            print(f"[hive-backend] shutting down: stopping {worker.peer} (domain {worker.domain_id})")
        worker.stop()


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
            return self.write_json(
                200,
                {"ok": True, "service": "hive-backend", "ros": {"setup": ROS_SETUP, "found": ros_installed()}},
            )
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
        """Find the robot. A failed attempt leaves the worker running, so DDS keeps looking for the robot
        between the page's retries; Disconnect ends it."""
        worker = get_worker(peer, domain_id)
        found = worker.discover(with_topics=True)
        if not found["nodes"] and not found["topics"]:
            raise ApiError(
                502,
                f"No ROS 2 nodes answered at {peer} on domain {domain_id}. Check that the robot is on, on this "
                f"network, and running with ROS_DOMAIN_ID={domain_id}.",
                code="not_found",
                **found,
            )
        warnings = []
        if found["cmd_vel_subscribers"] == 0:
            warnings.append(f"Nothing on the robot is listening on {CMD_VEL}, so drive commands will not move it. Check its driver node.")
        return {"ok": True, **found, "warnings": warnings}

    def handle_status(self, body: dict, peer: str, domain_id: int) -> dict:
        found = get_worker(peer, domain_id).discover(with_topics=False)
        if not found["nodes"]:
            raise ApiError(502, f"Lost contact: no ROS 2 nodes answered at {peer} on domain {domain_id}.", nodes=[], code="lost_contact")
        return {"ok": True, "nodes": found["nodes"]}

    def handle_control(self, body: dict, peer: str, domain_id: int) -> dict:
        """The gripper, and one-shot drive actions (a drive action without keepalives runs until the
        deadman stops it, about a second)."""
        action = str(body.get("action", "")).strip()
        if action not in CONTROL_ACTIONS:
            raise ApiError(400, f"Unsupported control action: {action}")
        session, seq = validate_order(body)
        worker = get_worker(peer, domain_id)
        if action in GRIPPER:
            result = worker.request(
                "publish",
                timeout=SUBSCRIBER_WAIT_SECONDS + 5,
                channel="gripper",
                session=session,
                seq=seq,
                topic=GRIPPER_TOPIC,
                value=GRIPPER[action],
                wait=SUBSCRIBER_WAIT_SECONDS,
            )
        else:
            result = self.drive(worker, action, session, seq)
        return {**result, "action": action}

    def handle_drive(self, body: dict, peer: str, domain_id: int) -> dict:
        """Hold-to-drive: start (or switch) the /cmd_vel stream, or stop it."""
        action = str(body.get("action", "")).strip()
        if action not in TWISTS:
            raise ApiError(400, f"Unsupported drive action: {action}")
        session, seq = validate_order(body)
        return self.drive(get_worker(peer, domain_id), action, session, seq)

    @staticmethod
    def drive(worker: RobotWorker, action: str, session: str | None, seq: int | None) -> dict:
        if action != "stop":
            worker.wait_for_discovery()
        return worker.request("drive", timeout=5, channel="drive", session=session, seq=seq, action=action)

    def handle_keepalive(self, body: dict, peer: str, domain_id: int) -> dict:
        worker = existing_worker(peer, domain_id)
        if worker is None:
            raise ApiError(502, "The backend is not driving this robot any more.", streaming=False, code="worker_gone")
        return worker.request("keepalive", timeout=3)

    def handle_command(self, body: dict, peer: str, domain_id: int) -> dict:
        text = str(body.get("text", "")).strip()
        if not text:
            raise ApiError(400, "The command is empty.")
        if len(text) > MAX_COMMAND_LENGTH:
            raise ApiError(400, f"Commands are limited to {MAX_COMMAND_LENGTH} characters.")
        return get_worker(peer, domain_id).request(
            "publish", timeout=SUBSCRIBER_WAIT_SECONDS + 5, topic=COMMAND_TOPIC, value=text, wait=SUBSCRIBER_WAIT_SECONDS
        )

    def handle_disconnect(self, body: dict, peer: str, domain_id: int) -> dict:
        """Stop the robot before letting it go. Best effort: an unreachable robot still disconnects."""
        worker = drop_worker(peer, domain_id)
        if worker is None or not worker.is_running():
            return {"ok": True, "stopped": False}
        try:
            worker.request("drive", timeout=3, action="stop")
            return {"ok": True, "stopped": True}
        except ApiError as error:
            return {"ok": True, "stopped": False, "warning": error.message}
        finally:
            worker.stop()

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


def shutdown(*_: Any) -> None:
    stop_all_workers()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server = ThreadingHTTPServer((HOST, PORT), HiveRequestHandler)
    server.daemon_threads = True
    ros = "found" if ros_installed() else "NOT FOUND"
    print(f"Hive backend listening on http://{HOST}:{PORT} (ROS 2 setup {ROS_SETUP}: {ros})")
    print(f"Allowed origins: {', '.join(sorted(ALLOWED_ORIGINS))}")
    try:
        server.serve_forever()
    finally:
        stop_all_workers()


# ------------------------------------------------------------------ the worker process (robot side)


class RobotWorkerLoop:
    """Runs inside the worker process: owns the rclpy node and publishers for one robot, and the drive
    state. Everything that touches ROS happens on this one thread."""

    def __init__(self) -> None:
        import rclpy
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Bool, String

        try:
            from rclpy.signals import SignalHandlerOptions

            rclpy.init(args=None, signal_handler_options=SignalHandlerOptions.NO)
        except ImportError:
            rclpy.init(args=None)
        self.rclpy, self.Twist, self.Bool, self.String = rclpy, Twist, Bool, String
        domain_id = os.environ.get("ROS_DOMAIN_ID", "0")
        self.node = rclpy.create_node(f"hive_operator_domain_{domain_id}")
        self.publishers = {
            CMD_VEL: self.node.create_publisher(Twist, CMD_VEL, 10),
            GRIPPER_TOPIC: self.node.create_publisher(Bool, GRIPPER_TOPIC, 10),
            COMMAND_TOPIC: self.node.create_publisher(String, COMMAND_TOPIC, 10),
        }
        self.action = "stop"
        self.last_keepalive = 0.0
        self.deadman_tripped = False
        self.next_publish_at = time.monotonic()
        self.pending: list[dict[str, Any]] = []  # requests waiting on discovery or a listener
        self.last_logged: tuple[str, float] = ("", 0.0)
        self.requests: queue.Queue[dict[str, Any] | None] = queue.Queue()

    # -------------------------------------------------------------- replies

    @staticmethod
    def reply(request_id: Any, **fields: Any) -> None:
        print(json.dumps({"id": request_id, "ok": True, **fields}), flush=True)

    @staticmethod
    def fail(request_id: Any, status: int, error: str, **extra: Any) -> None:
        print(json.dumps({"id": request_id, "ok": False, "status": status, "error": error, "extra": extra}), flush=True)

    @staticmethod
    def log(text: str) -> None:
        print(f"[hive-worker {os.environ.get('ROS_STATIC_PEERS')}] {text}", file=sys.stderr, flush=True)

    # -------------------------------------------------------------- ROS

    def publish_twist(self, action: str) -> None:
        message = self.Twist()
        message.linear.x, message.angular.z = TWISTS[action]
        self.publishers[CMD_VEL].publish(message)

    def publish_value(self, topic: str, value: Any) -> None:
        message = self.Bool() if topic == GRIPPER_TOPIC else self.String()
        message.data = value
        self.publishers[topic].publish(message)

    def listeners(self, topic: str) -> int:
        return self.node.count_subscribers(topic)

    def is_own(self, name: str, namespace: str) -> bool:
        return name == self.node.get_name() and namespace == self.node.get_namespace()

    def discover(self, with_topics: bool) -> dict[str, Any]:
        nodes = sorted(
            f"{namespace.rstrip('/')}/{name}"
            for name, namespace in self.node.get_node_names_and_namespaces()
            if not self.is_own(name, namespace) and not name.startswith("_")
        )
        topics = []
        if with_topics:
            for name, types in sorted(self.node.get_topic_names_and_types()):
                endpoints = self.node.get_publishers_info_by_topic(name) + self.node.get_subscriptions_info_by_topic(name)
                if any(not self.is_own(e.node_name, e.node_namespace) for e in endpoints):
                    topics.append({"name": name, "type": ", ".join(types)})
        return {"nodes": nodes, "topics": topics, "cmd_vel_subscribers": self.listeners(CMD_VEL)}

    # -------------------------------------------------------------- drive state

    def start_driving(self, action: str) -> None:
        now = time.monotonic()
        if action != self.action:
            self.log(f"driving {action} ({self.listeners(CMD_VEL)} listening on {CMD_VEL})")
        self.action, self.last_keepalive, self.deadman_tripped = action, now, False
        self.publish_twist(action)
        self.next_publish_at = now + 1.0 / DRIVE_RATE_HZ

    def stop_driving(self) -> None:
        if self.action != "stop":
            self.log("stopped")
        self.action = "stop"
        self.publish_twist("stop")

    # -------------------------------------------------------------- requests

    def handle(self, request: dict[str, Any]) -> None:
        request_id, op = request.get("id"), request.get("op")
        now = time.monotonic()
        if op == "drive":
            action = request.get("action")
            if action == "stop":
                self.stop_driving()
                self.reply(request_id, action="stop", streaming=False)
            elif action in TWISTS:
                if self.listeners(CMD_VEL) == 0:
                    self.fail(request_id, 502, no_listener(CMD_VEL), streaming=False, code="no_listener")
                    return
                self.start_driving(action)
                self.reply(request_id, action=action, streaming=True)
            else:
                self.fail(request_id, 400, f"Unsupported drive action: {action}")
        elif op == "keepalive":
            if self.action != "stop":
                self.last_keepalive = now
                self.reply(request_id, streaming=True, action=self.action)
            elif self.deadman_tripped:
                self.deadman_tripped = False
                self.fail(
                    request_id,
                    502,
                    f"The robot was stopped because keepalives from the page stopped arriving for over {DEADMAN_SECONDS:g} s.",
                    streaming=False,
                    code="deadman",
                )
            else:
                self.reply(request_id, streaming=False)
        elif op == "publish":
            topic = request.get("topic")
            if topic not in (GRIPPER_TOPIC, COMMAND_TOPIC):
                self.fail(request_id, 400, f"Unsupported topic: {topic}")
                return
            self.pending.append({**request, "deadline": now + float(request.get("wait", 0))})
        elif op == "discover":
            self.pending.append({**request, "deadline": now + float(request.get("wait", 0))})
        else:
            self.fail(request_id, 400, f"Unsupported worker request: {op}")

    def settle_pending(self) -> None:
        for request in list(self.pending):
            try:
                done = self.settle(request)
            except Exception as error:  # noqa: BLE001 - fail this request, keep the worker
                self.log(f"{request['op']} failed:\n{traceback.format_exc()}")
                self.fail(request["id"], 500, f"The ROS 2 worker hit an error: {type(error).__name__}: {error}", code="worker_error")
                done = True
            if done:
                self.pending.remove(request)

    def settle(self, request: dict[str, Any]) -> bool:
        """Answer a waiting request if it can be answered now. Returns True once it has been."""
        done_waiting = time.monotonic() >= request["deadline"]
        if request["op"] == "publish":
            topic = request["topic"]
            if self.listeners(topic) > 0:
                self.publish_value(topic, request["value"])
                self.reply(request["id"], topic=topic)
                return True
            if done_waiting:
                self.fail(request["id"], 502, no_listener(topic), code="no_listener")
                return True
            return False
        if done_waiting or self.listeners(CMD_VEL) > 0:
            self.reply(request["id"], **self.discover(bool(request.get("topics"))))
            return True
        return False

    def tick(self) -> None:
        """The deadman, the 20 Hz drive stream, and ROS housekeeping."""
        now = time.monotonic()
        if self.action != "stop" and now - self.last_keepalive > DEADMAN_SECONDS:
            self.log("deadman: no keepalive from the page, stopping the robot")
            self.deadman_tripped = True
            self.stop_driving()
        if now >= self.next_publish_at:
            if self.action != "stop":
                self.publish_twist(self.action)
            self.next_publish_at += 1.0 / DRIVE_RATE_HZ
            if self.next_publish_at < now:  # fell behind (e.g. a slow discovery): don't burst
                self.next_publish_at = now + 1.0 / DRIVE_RATE_HZ
        self.rclpy.spin_once(self.node, timeout_sec=0.0)

    def log_throttled(self, text: str) -> None:
        """Log a repeating error once, then at most every 5 s."""
        now = time.monotonic()
        if text != self.last_logged[0] or now - self.last_logged[1] > 5:
            self.log(text)
            self.last_logged = (text, now)

    def read_stdin(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except ValueError:
                self.log(f"ignoring malformed request: {line.strip()}")
                continue
            if isinstance(request, dict):
                self.requests.put(request)
        self.requests.put(None)  # the backend closed the pipe, or died

    def run(self) -> None:
        threading.Thread(target=self.read_stdin, daemon=True).start()
        print(json.dumps({"ready": True}), flush=True)
        try:
            while True:
                timeout = max(0.0, min(self.next_publish_at - time.monotonic(), 0.05))
                try:
                    request = self.requests.get(timeout=timeout)
                except queue.Empty:
                    request = {}
                if request is None:
                    break
                # one failing ROS call must not end the worker: that would cut the robot's connection
                if request:
                    try:
                        self.handle(request)
                    except Exception as error:  # noqa: BLE001
                        self.log(f"{request.get('op')} failed:\n{traceback.format_exc()}")
                        self.fail(request.get("id"), 500, f"The ROS 2 worker hit an error: {type(error).__name__}: {error}", code="worker_error")
                self.settle_pending()
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    self.log_throttled(f"drive loop error (worker still running):\n{traceback.format_exc()}")
        finally:
            self.stop_driving()
            self.node.destroy_node()
            self.rclpy.shutdown()


def run_robot_worker() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        loop = RobotWorkerLoop()
    except Exception as error:  # noqa: BLE001 - reported to the backend, which shows it to the operator
        print(json.dumps({"ready": False, "error": f"{type(error).__name__}: {error}"}), flush=True)
        raise
    loop.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hive backend")
    parser.add_argument("--robot-worker", action="store_true", help="Run as one robot's ROS 2 worker (started by the backend)")
    args = parser.parse_args()
    if args.robot_worker:
        run_robot_worker()
    else:
        main()
