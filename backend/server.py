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
import socket
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Any
from urllib.parse import parse_qs, urlparse

import adapters
import camera_tuning
import robot_link
import outputs
import streams

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
ROS_DATA = "/ros_data"
GRIPPER_TOPIC = "/gripper_open"
COMMAND_TOPIC = os.environ.get("HIVE_COMMAND_TOPIC", "/hive/command")
DRIVE_RATE_HZ = 10
STOP_BURST_COUNT = 5            # a stop is sent once at once, then repeated this many times in all
STOP_BURST_SPACING = 0.05       # seconds between the burst messages
LINK_EMIT_SECONDS = 0.5
GRIPPER_CALL_TIMEOUT = 5.0
GRIPPER_WAIT_SECONDS = 2.0      # how long a gripper press waits for the robot's interface to appear
RMW_DEFAULT = "rmw_fastrtps_cpp"
DEADMAN_SECONDS = 1.0
SUBSCRIBER_WAIT_SECONDS = 4      # how long a gripper or command-log publish waits for a listener
DISCOVERY_SPIN_SECONDS = 2.5     # how long a new worker listens for the robot before giving up
WORKER_START_SECONDS = 15
MAX_COMMAND_LENGTH = 500
MAX_BODY_BYTES = 64 * 1024
MAX_STREAM_TOPICS = 32  # per stream and per kind (data, image stats); more are reported as too_many, never refused
STREAM_WATCH_TIMEOUT = 5.0
RATE_CAPS = {"image": 15.0, "scan": 10.0, "value": 5.0, "generic": 5.0}  # Hz per payload kind, converter side
HUB = streams.StreamHub(
    on_first=lambda key, topic: watch_request(key, "watch", topic),
    on_last=lambda key, topic: watch_request(key, "unwatch", topic),
)
OUT: outputs.LineWriter | None = None  # the worker's one stdout writer thread, made on first use

IP_OR_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

TWISTS = {"forward", "backward", "left", "right", "stop"}
GRIPPER = {"gripper_open": "open", "gripper_close": "close", "gripper_position": "position"}
CONTROL_ACTIONS = TWISTS | set(GRIPPER)
GRIPPER_POSITION_TOPIC = "/gripper/position"
GRIPPER_SERVICES = {"open": "/gripper/release", "close": "/gripper/grab"}
DEFAULT_CAMERA = ("medium", False)   # colour quality and depth, applied at connect unless the user chose otherwise
CAMERA_WANT: dict[tuple, tuple] = {}  # robot key -> the last (quality, depth) requested; survives disconnects
CAMERA_RETRY_SECONDS = 5.0          # least time between lazy re-tries of tuning for one robot
CAMERA_LAZY_SECONDS = 3.0            # the whole lazy re-try, so a stream request never waits longer
CAMERA_LAZY_CALL_SECONDS = 1.0       # per service call on the lazy path
CAMERA_REQUEST_SECONDS = 30          # a whole tuning run: discovery plus both parameter calls, 5 s per call at most


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


def validate_power(body: dict) -> float:
    try:
        return robot_link.clamp_power(body.get("power"))
    except ValueError:
        raise ApiError(400, "power must be a number between 0.2 and 1.0.", code="bad_power") from None


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
        self.link: dict[str, Any] | None = None  # newest link state the worker reported
        self.topic_kinds: dict[str, str] = {}  # topic -> payload kind, from the last discover reply
        self.camera: dict[str, Any] | None = None  # newest camera tuning result; None when no RealSense node
        self.camera_tried_at = 0.0  # monotonic time of the last tuning attempt
        self.camera_lock = threading.Lock()  # one lazy re-try at a time per robot
        self.replaced = False  # a worker that died on its own was replaced and has not been reported yet

    def take_replaced(self) -> bool:
        """True once after a start replaced a worker that had died (crash, kill): the new worker sent the
        robot a stop on startup, and the page should say so."""
        with self.replies_lock:
            replaced, self.replaced = self.replaced, False
        return replaced

    def on_link(self, link: dict[str, Any]) -> None:
        self.link = link
        HUB.publish_link((self.peer, self.domain_id), link)

    def on_stream(self, payload: dict[str, Any]) -> None:
        HUB.publish((self.peer, self.domain_id), payload)

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        """Start the worker process if it is not running. Every new worker stops the robot on startup (R29),
        so one that replaces a crashed worker also stops a car the old one left moving."""
        with self.start_lock:
            if self.is_running():
                return
            died = self.process is not None  # stop() clears process; still set means it died by itself
            self.link = None  # a new worker must not show the old one's state
            self.camera, self.camera_tried_at = None, 0.0  # nor the old camera result: it would gate or ungate MJPEG wrongly
            require_ros()
            self.discovered.clear()
            # Non-login bash, so the operator's own profile cannot change the ROS environment. Its own
            # session, so Ctrl+C in the terminal reaches only the backend, which then stops the robot.
            script = " && ".join(
                [
                    f"source {shlex.quote(ROS_SETUP)}",
                    f"export ROS_DOMAIN_ID={self.domain_id}",
                    f"export RMW_IMPLEMENTATION=${{HIVE_RMW:-{RMW_DEFAULT}}}",
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
            if died:
                with self.replies_lock:
                    self.replaced = True
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
            if isinstance(reply, dict) and "id" not in reply and isinstance(reply.get("link"), dict):
                self.on_link(reply["link"])
                continue
            if isinstance(reply, dict) and "id" not in reply and isinstance(reply.get("stream"), dict):
                self.on_stream(reply["stream"])
                continue
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
        result = {k: v for k, v in reply.items() if k not in ("id", "ok")}
        if isinstance(result.get("link"), dict):
            self.on_link(result["link"])
        return result

    def discover(self, *, with_topics: bool) -> dict[str, Any]:
        """The robot's nodes, topics and /cmd_vel listeners. A new worker first gives DDS up to
        DISCOVERY_SPIN_SECONDS to find the robot, stopping early once something listens on /cmd_vel."""
        wait = 0.0 if self.discovered.is_set() else DISCOVERY_SPIN_SECONDS
        found = self.request("discover", timeout=wait + 5, wait=wait, topics=with_topics)
        self.discovered.set()
        if with_topics:
            self.topic_kinds = {t["name"]: t.get("kind", "generic") for t in found.get("topics", [])}
        return found

    def wait_for_discovery(self) -> None:
        if not self.discovered.is_set():
            self.discover(with_topics=False)

    def stop(self) -> None:
        """End the worker. Closing its stdin makes it publish a zero Twist before it exits."""
        with self.start_lock:
            process, self.process = self.process, None
            self.link = None
            self.camera, self.camera_tried_at = None, 0.0
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


def apply_camera(worker: RobotWorker, quality: str, depth: bool, *, lazy: bool = False) -> dict[str, Any] | None:
    """Tune the robot's RealSense and remember the choice. A tuning failure never raises: it is the
    result (applied false, with the reason), so connecting still works and the MJPEG gate can act on it."""
    CAMERA_WANT[(worker.peer, worker.domain_id)] = (quality, depth)
    worker.camera_tried_at = time.monotonic()
    try:
        if lazy:
            camera = worker.request("camera", timeout=CAMERA_LAZY_SECONDS, quality=quality, depth=depth, call_timeout=CAMERA_LAZY_CALL_SECONDS)["camera"]
        else:
            camera = worker.request("camera", timeout=CAMERA_REQUEST_SECONDS, quality=quality, depth=depth)["camera"]
    except ApiError as error:
        camera = {"node": None, "applied": False, "quality": quality, "depth": depth, "profile": None, "reason": error.message}
    worker.camera = camera
    return camera


def retry_camera(worker: RobotWorker) -> None:
    """A RealSense node that joined the graph after connect (or a tuning attempt that got no answer) is
    tuned on first use, at most once per CAMERA_RETRY_SECONDS, with the remembered choice."""
    def unresolved() -> bool:
        camera = worker.camera
        return camera is None or (camera.get("applied") is False and camera.get("node") is None)

    def due() -> bool:
        return unresolved() and time.monotonic() - worker.camera_tried_at >= CAMERA_RETRY_SECONDS

    if not due():
        return
    with worker.camera_lock:  # a concurrent request waits here, then finds the window already used
        if not due():
            return
        quality, depth = CAMERA_WANT.get((worker.peer, worker.domain_id), DEFAULT_CAMERA)
        apply_camera(worker, quality, depth, lazy=True)
        if unresolved():  # still no answer: treat the camera as unknown, so nothing gates the stream
            worker.camera = None


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


def watch_request(key: tuple, op: str, topic: str) -> None:
    """The hub's on_first / on_last: tell the robot's worker to start or stop watching a topic."""
    worker = existing_worker(*key)
    if worker is None:
        raise ApiError(502, "The ROS 2 worker for this robot is not running.", code="worker_gone")
    worker.request(op, timeout=STREAM_WATCH_TIMEOUT, topic=topic)


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
        url = urlparse(self.path)
        if url.path in ("/api/robots/stream", "/api/robots/camera.mjpg"):
            return self.handle_stream(url)
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
            "/api/robots/camera": self.handle_camera,
            "/api/robots/link": self.handle_link,
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
        if found.get("other_publishers", 0) > 0:
            warnings.append(
                f"{found['other_publishers']} other publisher(s) are also sending on {CMD_VEL}. Their commands will mix with Hive's; "
                "stop the other controller before driving."
            )
        quality, depth = CAMERA_WANT.get((peer, domain_id), DEFAULT_CAMERA)
        camera = apply_camera(worker, quality, depth)
        return {"ok": True, **found, "warnings": warnings, "camera": camera, "worker_replaced": worker.take_replaced()}

    def handle_camera(self, body: dict, peer: str, domain_id: int) -> dict:
        """Pick the colour quality (and depth) of the robot's RealSense; the choice is re-applied on reconnect."""
        quality, depth = body.get("quality"), body.get("depth", False)
        if quality not in robot_link.CAMERA_PROFILES:
            raise ApiError(400, f"quality must be one of {', '.join(robot_link.CAMERA_PROFILES)}.", code="bad_quality")
        if not isinstance(depth, bool):
            raise ApiError(400, "depth must be true or false.", code="bad_depth")
        worker = get_worker(peer, domain_id)
        worker.wait_for_discovery()
        camera = apply_camera(worker, quality, depth)
        if camera is None:
            raise ApiError(409, "This robot has no RealSense camera node to tune.", code="no_camera")
        return {"ok": True, "camera": camera}

    def handle_status(self, body: dict, peer: str, domain_id: int) -> dict:
        """Still there? Also restarts a worker that died (it stops the robot on startup and the reply says so),
        and carries the newest link, so a page without a live stream (Home) learns how the last stop went."""
        worker = get_worker(peer, domain_id)
        found = worker.discover(with_topics=False)
        if not found["nodes"]:
            raise ApiError(502, f"Lost contact: no ROS 2 nodes answered at {peer} on domain {domain_id}.", nodes=[], code="lost_contact")
        return {"ok": True, "nodes": found["nodes"], "link": worker.link, "worker_replaced": worker.take_replaced()}

    def handle_link(self, body: dict, peer: str, domain_id: int) -> dict:
        """The newest link the worker reported (it reports at once on a change, else every 0.5 s). No worker
        round trip, so the page can follow a stop's confirmation cheaply where it has no live stream."""
        worker = existing_worker(peer, domain_id)
        if worker is None:
            raise ApiError(502, "The backend is not connected to this robot.", code="worker_gone")
        return {"ok": True, "link": worker.link}

    def handle_control(self, body: dict, peer: str, domain_id: int) -> dict:
        """The gripper, and one-shot drive actions (a drive action without keepalives runs until the
        deadman stops it, about a second)."""
        action = str(body.get("action", "")).strip()
        if action not in CONTROL_ACTIONS:
            raise ApiError(400, f"Unsupported control action: {action}")
        session, seq = validate_order(body)
        value = None
        if action == "gripper_position":
            value = body.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ApiError(400, "The gripper position must be a number from 0 to 1.", code="bad_value")
            value = float(value)
        power = validate_power(body) if action in TWISTS and action != "stop" else None  # a stop is never refused
        worker = get_worker(peer, domain_id)
        if action in GRIPPER:
            worker.wait_for_discovery()
            result = worker.request(
                "gripper",
                timeout=GRIPPER_WAIT_SECONDS + GRIPPER_CALL_TIMEOUT + 3,
                channel="gripper",
                session=session,
                seq=seq,
                action=GRIPPER[action],
                value=value,
                wait=GRIPPER_WAIT_SECONDS,
            )
        else:
            result = self.drive(worker, action, session, seq, power)
        return {**result, "action": action}

    def handle_drive(self, body: dict, peer: str, domain_id: int) -> dict:
        """Hold-to-drive: start (or switch) the /cmd_vel stream, or stop it."""
        action = str(body.get("action", "")).strip()
        if action not in TWISTS:
            raise ApiError(400, f"Unsupported drive action: {action}")
        session, seq = validate_order(body)
        power = None if action == "stop" else validate_power(body)
        return self.drive(get_worker(peer, domain_id), action, session, seq, power)

    @staticmethod
    def drive(worker: RobotWorker, action: str, session: str | None, seq: int | None, power: float) -> dict:
        if action != "stop":
            worker.wait_for_discovery()
        result = worker.request("drive", timeout=5, channel="drive", session=session, seq=seq, action=action, power=power)
        result.setdefault("link", worker.link)
        return result

    def handle_keepalive(self, body: dict, peer: str, domain_id: int) -> dict:
        worker = existing_worker(peer, domain_id)
        if worker is None:
            raise ApiError(502, "The backend is not driving this robot any more.", streaming=False, code="worker_gone")
        result = worker.request("keepalive", timeout=3)
        result.setdefault("link", worker.link)
        return result

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

    # -------------------------------------------------------------- live streams

    def handle_stream(self, url: Any) -> None:
        """SSE for data topics, MJPEG for one image topic. The viewer is registered before the headers go
        out, so a worker that refuses an MJPEG watch becomes an HTTP error and leaves no viewer behind.
        An SSE stream always opens once the worker runs, even with no data topic at all: it carries the
        link (R27), and each topic it cannot stream is reported as a status event instead of failing the
        whole stream (R21). Past MAX_STREAM_TOPICS the extra topics are reported as too_many."""
        mjpeg = url.path.endswith(".mjpg")
        try:
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            peer, domain_id = validate_peer(query.get("ip")), validate_domain_id(query.get("domain_id"))
            names = [query.get("topic", "")] if mjpeg else list(dict.fromkeys(t for t in query.get("topics", "").split(",") if t))
            shown = [] if mjpeg else list(dict.fromkeys(t for t in query.get("images", "").split(",") if t))
            if any(not t.startswith("/") for t in names + shown):
                raise ApiError(400, "Name the topic(s) to stream, each starting with /.", code="bad_topics")
            problems: list[tuple[str, str]] = [(t, "too_many") for t in names[MAX_STREAM_TOPICS:] + shown[MAX_STREAM_TOPICS:]]
            names, shown = names[:MAX_STREAM_TOPICS], shown[:MAX_STREAM_TOPICS]
            worker = existing_worker(peer, domain_id)
            if worker is None:
                raise ApiError(502, "The ROS 2 worker for this robot is not running. Connect first.", code="worker_gone")
            if mjpeg:
                for name in names:
                    kind = worker.topic_kinds.get(name)
                    if kind is None:
                        raise ApiError(404, f"The robot has no topic {name}.", code="unknown_topic")
                    if kind != "image":
                        raise ApiError(400, f"{name} is not an image topic; use the data stream.", code="use_sse")
                valid = names
            else:
                valid = []
                for name in names:
                    kind = worker.topic_kinds.get(name)
                    if kind is None:
                        problems.append((name, "unknown_topic"))
                    elif kind == "image":
                        problems.append((name, "use_mjpeg"))
                    else:
                        valid.append(name)
                shown = [t for t in shown if worker.topic_kinds.get(t) == "image"]
            key = (peer, domain_id)
            if mjpeg:
                retry_camera(worker)
            camera = worker.camera
            if mjpeg and camera and camera.get("applied") is False and query.get("force_full") != "1":
                raise ApiError(
                    409,
                    f"The camera could not be set to a low-bandwidth mode ({camera.get('reason')}), so the stream would flood the Wi-Fi.",
                    code="camera_untuned",
                    reason=camera.get("reason"),
                )
            try:
                failed: list = []
                viewer = HUB.subscribe(key, valid, errors=None if mjpeg else failed)
                problems += [(topic, "watch_failed") for topic, _ in failed]
                if not mjpeg:
                    viewer.image_topics = tuple(shown)
            except ApiError:
                raise
            except Exception as error:  # noqa: BLE001
                raise ApiError(502, f"Could not start watching: {error}", code="watch_failed") from None
        except ApiError as error:
            return self.write_json(error.status, {"ok": False, "error": error.message, **error.extra})

        def write(data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()

        def client_gone() -> bool:
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
            except (OSError, ValueError):
                return True

        try:
            # the headers are inside the try: a client that is already gone must still release its viewer
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={streams.BOUNDARY}" if mjpeg else "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if mjpeg:
                streams.serve_mjpeg(write, HUB, key, names[0], alive=worker.is_running, client_gone=client_gone)
            else:
                streams.serve_sse(write, HUB, viewer, alive=worker.is_running, client_gone=client_gone, link=worker.link, errors=problems)
        except OSError:  # the browser left (BrokenPipe, ConnectionReset), even before the first byte
            pass
        finally:
            HUB.unsubscribe(viewer)

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


def output() -> outputs.LineWriter:
    global OUT
    if OUT is None:
        OUT = outputs.LineWriter()
    return OUT


def write_line(obj: dict[str, Any]) -> None:
    """A control line (reply, link, ready) for the backend. Never blocks: a stuck pipe must not stop the drive tick."""
    output().send(obj)


def write_stream(obj: dict[str, Any]) -> None:
    """A sensor payload for the backend. Never blocks; the oldest is dropped when the pipe is behind."""
    output().send_stream(obj)


class RobotWorkerLoop:
    """Runs inside the worker process: owns the rclpy node and publishers for one robot, and the drive
    state. Everything that touches ROS happens on this one thread."""

    def __init__(self) -> None:
        import rclpy
        from geometry_msgs.msg import Twist
        from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import Bool, Float32, String

        global sensor_watch
        import sensor_watch  # needs rclpy, so only the worker imports it

        try:
            from rclpy.signals import SignalHandlerOptions

            rclpy.init(args=None, signal_handler_options=SignalHandlerOptions.NO)
        except ImportError:
            rclpy.init(args=None)
        self.rclpy, self.Twist, self.Bool, self.Float32, self.String = rclpy, Twist, Bool, Float32, String
        domain_id = os.environ.get("ROS_DOMAIN_ID", "0")
        self.node = rclpy.create_node(f"hive_operator_domain_{domain_id}")
        self.publishers = {
            CMD_VEL: self.node.create_publisher(
                Twist,
                CMD_VEL,
                QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST),
            ),
            GRIPPER_TOPIC: self.node.create_publisher(Bool, GRIPPER_TOPIC, 10),
            GRIPPER_POSITION_TOPIC: self.node.create_publisher(Float32, GRIPPER_POSITION_TOPIC, 10),
            COMMAND_TOPIC: self.node.create_publisher(String, COMMAND_TOPIC, 10),
        }
        self.node.create_subscription(
            String,
            ROS_DATA,
            self.on_ros_data,
            QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST),
        )
        self.clients: dict[str, Any] = {}
        self.tuner = camera_tuning.CameraTuner(self.node)
        self.watcher = sensor_watch.SensorWatcher(self.node, write_stream, RATE_CAPS, outputs.ThrottledLog(self.log).log)
        self.confirmer = robot_link.StopConfirmer()
        self.burst: list[float] = []           # times of the stop repeats still to send
        self.power = robot_link.DEFAULT_POWER
        self.last_line: tuple[str, float] | None = None  # newest /ros_data line and when it came
        self.received = 0
        self.drove, self.saw_motion = False, False  # a zero line confirms a stop only after motion was seen
        self.reporting, self.other_publishers = False, 0
        self.link_sent: tuple[tuple, float] = ((), 0.0)
        self.link_seq = 0  # newest link's seq (R28)
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
        write_line({"id": request_id, "ok": True, **fields})

    @staticmethod
    def fail(request_id: Any, status: int, error: str, **extra: Any) -> None:
        write_line({"id": request_id, "ok": False, "status": status, "error": error, "extra": extra})

    @staticmethod
    def log(text: str) -> None:
        try:
            print(f"[hive-worker {os.environ.get('ROS_STATIC_PEERS')}] {text}", file=sys.stderr, flush=True)
        except (OSError, ValueError):  # the terminal or pipe is gone: a log line must never stop a stop
            pass

    # -------------------------------------------------------------- ROS

    def publish_twist(self, action: str) -> None:
        message = self.Twist()
        message.linear.x, message.angular.z = robot_link.twist_for(action, self.power)
        self.publishers[CMD_VEL].publish(message)

    def publish_value(self, topic: str, value: Any) -> None:
        message = {GRIPPER_TOPIC: self.Bool, GRIPPER_POSITION_TOPIC: self.Float32}.get(topic, self.String)()
        message.data = value
        self.publishers[topic].publish(message)

    def on_ros_data(self, message: Any) -> None:
        now = time.monotonic()
        self.received += 1
        text = str(message.data).strip()
        self.last_line = (text, now)
        line = robot_link.parse_motor_line(text)
        if line is not None and line.kind == "drive" and not robot_link.is_zero_drive(line):
            self.saw_motion = True
        elif robot_link.is_zero_drive(line) and self.drove and not self.saw_motion:
            return  # a zero line from before the car acted on this drive (stale or in flight)
        self.confirmer.observe(line, now)

    def drain(self) -> None:
        """Handle every ready callback (the /ros_data echoes), at most 50 per call."""
        for _ in range(50):
            before = self.received + self.watcher.callbacks
            self.rclpy.spin_once(self.node, timeout_sec=0.0)
            if self.received + self.watcher.callbacks == before:
                break

    def refresh_graph(self) -> None:
        self.reporting = self.node.count_publishers(ROS_DATA) > 0
        self.other_publishers = max(0, self.node.count_publishers(CMD_VEL) - 1)
        self.confirmer.set_reporting(self.reporting)

    def stop_state(self, now: float) -> str:
        """The stop confirmation state. A due resend is one more zero Twist."""
        state, resend = self.confirmer.poll(now)
        if resend and self.action == "stop":
            self.publish_twist("stop")
        return state

    def next_link_seq(self) -> int:
        """A number that grows with every link, also across worker restarts on this machine: microseconds
        of the system-wide monotonic clock (shared by every process since boot), bumped by one if two links
        fall in the same microsecond. The page keeps the link with the highest seq from any route (R28)."""
        self.link_seq = max(self.link_seq + 1, time.monotonic_ns() // 1000)
        return self.link_seq

    def link_state(self, now: float) -> dict[str, Any]:
        last = self.last_line
        return {
            "last_line": last[0] if last else None,
            "last_line_age_ms": int((now - last[1]) * 1000) if last else None,
            "stop": self.stop_state(now),
            "reporting": self.reporting,
            "other_publishers": self.other_publishers,
            "seq": self.next_link_seq(),
        }

    def emit_link(self, now: float) -> None:
        """Tell the backend the link state: at once when it changes, otherwise every LINK_EMIT_SECONDS."""
        link = self.link_state(now)
        key = (link["stop"], link["reporting"], link["other_publishers"])
        if key != self.link_sent[0] or now - self.link_sent[1] >= LINK_EMIT_SECONDS:
            self.link_sent = (key, now)
            write_line({"link": link})

    def detect_gripper(self) -> str | None:
        subscribed = {
            t for t in (GRIPPER_TOPIC, GRIPPER_POSITION_TOPIC)
            if any(not self.is_own(e.node_name, e.node_namespace) for e in self.node.get_subscriptions_info_by_topic(t))
        }
        services = {name for name, _ in self.node.get_service_names_and_types()}
        return robot_link.detect_gripper(subscribed, services)

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
        topics, extra = [], {}
        self.refresh_graph()
        if with_topics:
            for name, types in sorted(self.node.get_topic_names_and_types()):
                endpoints = self.node.get_publishers_info_by_topic(name) + self.node.get_subscriptions_info_by_topic(name)
                if any(not self.is_own(e.node_name, e.node_namespace) for e in endpoints):
                    topics.append({"name": name, "type": ", ".join(types), "kind": adapters.kind_for_type(types[0] if types else "")})
            extra = {
                "services": [{"name": n, "type": ", ".join(t)} for n, t in sorted(self.node.get_service_names_and_types())],
                "gripper_interface": self.detect_gripper(),
                "other_publishers": self.other_publishers,
            }
        return {"nodes": nodes, "topics": topics, "cmd_vel_subscribers": self.listeners(CMD_VEL), **extra}

    # -------------------------------------------------------------- drive state

    def start_driving(self, action: str, power: float) -> None:
        now = time.monotonic()
        if action != self.action:
            self.log(f"driving {action} ({self.listeners(CMD_VEL)} listening on {CMD_VEL})")
        self.power = power
        self.drove, self.saw_motion = True, False
        self.burst = []  # a stop burst in flight must not cut into the new drive
        self.confirmer = robot_link.StopConfirmer()
        self.confirmer.set_reporting(self.reporting)
        self.action, self.last_keepalive, self.deadman_tripped = action, now, False
        self.publish_twist(action)
        self.next_publish_at = now + 1.0 / DRIVE_RATE_HZ

    def stop_driving(self) -> None:
        """Every stop path ends here: one zero Twist at once, the rest of the burst on the next ticks,
        and a stop confirmation to wait for. Publish first, log last: a log failure cannot lose a stop."""
        was_moving = self.action != "stop"
        try:
            self.drain()  # lines already queued must not count as confirming this stop
        except Exception:  # noqa: BLE001
            pass
        now = time.monotonic()
        self.action = "stop"
        self.confirmer.stop_requested(now)
        self.publish_twist("stop")
        self.burst = [now + STOP_BURST_SPACING * i for i in range(1, STOP_BURST_COUNT)]
        if was_moving:
            self.log("stopped")

    def send_burst(self, now: float) -> None:
        while self.burst and self.burst[0] <= now and self.action == "stop":
            self.burst.pop(0)
            self.publish_twist("stop")

    def handle(self, request: dict[str, Any]) -> None:
        request_id, op = request.get("id"), request.get("op")
        now = time.monotonic()
        if op == "drive":
            action = request.get("action")
            if action == "stop":
                self.stop_driving()
                self.refresh_graph()
                self.reply(request_id, action="stop", streaming=False, link=self.link_state(now))
            elif action in TWISTS:
                if self.listeners(CMD_VEL) == 0:
                    self.fail(request_id, 502, no_listener(CMD_VEL), streaming=False, code="no_listener")
                    return
                try:
                    power = robot_link.clamp_power(request.get("power"))
                except ValueError:
                    self.fail(request_id, 400, "power must be a number.", code="bad_power")
                    return
                self.start_driving(action, power)
                self.refresh_graph()
                self.reply(request_id, action=action, streaming=True, link=self.link_state(now))
            else:
                self.fail(request_id, 400, f"Unsupported drive action: {action}")
        elif op == "keepalive":
            self.refresh_graph()
            link = self.link_state(now)
            if self.action != "stop":
                self.last_keepalive = now
                self.reply(request_id, streaming=True, action=self.action, link=link)
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
                self.reply(request_id, streaming=False, link=link)
        elif op == "publish":
            topic = request.get("topic")
            if topic not in (GRIPPER_TOPIC, COMMAND_TOPIC):
                self.fail(request_id, 400, f"Unsupported topic: {topic}")
                return
            self.pending.append({**request, "deadline": now + float(request.get("wait", 0))})
        elif op == "gripper":
            if request.get("action") not in ("open", "close", "position"):
                self.fail(request_id, 400, f"Unsupported gripper action: {request.get('action')}")
                return
            self.pending.append({**request, "deadline": now + float(request.get("wait", 0))})
        elif op == "camera":
            try:
                job = self.tuner.start(request.get("quality"), request.get("depth"), request.get("call_timeout"))
            except ValueError as error:
                self.fail(request_id, 400, str(error), code="bad_quality")
                return
            self.pending.append({**request, "job": job, "deadline": now})
        elif op == "watch":
            try:
                self.reply(request_id, **self.watcher.watch(request.get("topic")))
            except KeyError:
                self.fail(request_id, 404, f"The robot has no topic {request.get('topic')}.", code="unknown_topic")
        elif op == "unwatch":
            self.watcher.unwatch(request.get("topic"))
            self.reply(request_id)
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
        if request["op"] == "gripper":
            return self.settle_gripper(request, done_waiting)
        if request["op"] == "camera":
            job = request["job"]
            if not job.step():
                return False
            self.reply(request["id"], camera=job.result)
            return True
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

    def settle_gripper(self, request: dict[str, Any], done_waiting: bool) -> bool:
        """Drive the gripper through whichever interface the robot has. A Trigger service call is async:
        it is polled on later ticks and given up on after GRIPPER_CALL_TIMEOUT."""
        rid, action = request["id"], request["action"]
        future = request.get("future")
        if future is not None:
            if future.done():
                result = future.result()
                if getattr(result, "success", True):
                    self.reply(rid, interface="trigger", service=request["service"], message=getattr(result, "message", ""))
                else:
                    self.fail(rid, 502, f"The gripper service refused: {getattr(result, 'message', '')}", code="gripper_failed")
                return True
            if time.monotonic() >= request["call_deadline"]:
                self.fail(rid, 504, f"{request['service']} did not answer in {GRIPPER_CALL_TIMEOUT:g} s.", code="gripper_timeout")
                return True
            return False
        interface = self.detect_gripper()
        if interface is None:
            if done_waiting:
                self.fail(rid, 409, "No gripper interface was found on this robot.", code="no_gripper")
                return True
            return False
        if interface == "trigger":
            if action == "position":
                self.fail(rid, 409, "This gripper opens and closes only; it has no position control.", code="no_position")
                return True
            service = GRIPPER_SERVICES[action]
            from std_srvs.srv import Trigger

            client = self.clients.get(service) or self.clients.setdefault(service, self.node.create_client(Trigger, service))
            if not client.service_is_ready():
                if done_waiting:
                    self.fail(rid, 502, f"{service} is not available.", code="no_service")
                    return True
                return False
            request["service"], request["call_deadline"] = service, time.monotonic() + GRIPPER_CALL_TIMEOUT
            request["future"] = client.call_async(Trigger.Request())
            return False
        if interface == "bool" and action == "position":
            self.fail(rid, 409, "This gripper opens and closes only; it has no position control.", code="no_position")
            return True
        if interface == "position":
            topic, value = GRIPPER_POSITION_TOPIC, {"open": 0.0, "close": 1.0}.get(action, request.get("value"))
        else:
            topic, value = GRIPPER_TOPIC, action == "open"
        self.publish_value(topic, value)
        self.reply(rid, interface=interface, topic=topic)
        return True

    def tick(self) -> None:
        """The deadman, the 10 Hz drive stream, stop bursts and confirmation, and ROS housekeeping."""
        now = time.monotonic()
        if self.action != "stop" and now - self.last_keepalive > DEADMAN_SECONDS:
            self.deadman_tripped = True
            self.stop_driving()
            self.log("deadman: no keepalive from the page, stopping the robot")
        if now >= self.next_publish_at:
            if self.action != "stop":
                self.publish_twist(self.action)
            self.next_publish_at += 1.0 / DRIVE_RATE_HZ
            if self.next_publish_at < now:  # fell behind (e.g. a slow discovery): don't burst
                self.next_publish_at = now + 1.0 / DRIVE_RATE_HZ
        self.send_burst(now)
        self.drain()  # so no /ros_data echo queues up behind a tick
        now = time.monotonic()
        self.refresh_graph()
        self.send_burst(now)
        self.emit_link(now)

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

    def shutdown_stop(self) -> None:
        """Stop the robot on the way out: the whole burst, on ticks, for at most half a second."""
        try:
            self.stop_driving()
            end = time.monotonic() + 0.5
            while self.burst and time.monotonic() < end:
                try:
                    self.requests.get(timeout=max(0.0, min(self.burst[0] - time.monotonic(), 0.05)))
                except queue.Empty:
                    pass
                self.send_burst(time.monotonic())
            flush_end = time.monotonic() + 0.15  # let the last messages leave before the node goes
            while time.monotonic() < flush_end:
                self.rclpy.spin_once(self.node, timeout_sec=0.0)
                try:
                    self.requests.get(timeout=0.02)
                except queue.Empty:
                    pass
        except Exception:  # noqa: BLE001
            self.log(f"final stop failed:\n{traceback.format_exc()}")

    def startup_stop(self) -> None:
        """R29: a new worker stops the robot before it handles anything. If it replaces one that crashed
        mid-drive, the car may still be running the old command (the camera and LiDAR cars have no command
        timeout). DDS has not found the robot yet, so the first zero Twists go nowhere; the stop confirmer
        resends every 200 ms for 5 s, which covers discovery, until the car's ros_data echo confirms it."""
        try:
            self.stop_driving()
            self.log("startup: sent the robot a stop")
        except Exception:  # noqa: BLE001 - the worker must still start; the deadman and later stops remain
            self.log(f"startup stop failed:\n{traceback.format_exc()}")

    def run(self) -> None:
        self.startup_stop()
        threading.Thread(target=self.read_stdin, daemon=True).start()
        write_line({"ready": True})
        try:
            while True:
                due = min([self.next_publish_at, *self.burst[:1]])
                timeout = max(0.0, min(due - time.monotonic(), 0.05))
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
            self.shutdown_stop()
            self.watcher.stop()
            output().flush(0.5)  # the last replies leave before the process does
            self.node.destroy_node()
            self.rclpy.shutdown()


def run_robot_worker() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        loop = RobotWorkerLoop()
    except Exception as error:  # noqa: BLE001 - reported to the backend, which shows it to the operator
        write_line({"ready": False, "error": f"{type(error).__name__}: {error}"})
        output().flush(0.5)  # the operator's startup error must leave before the process does
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
