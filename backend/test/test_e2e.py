"""End-to-end checks for the Hive backend against the mock ROS 2 (rclpy) in mock_ros/.

Each check prints PASS / FAIL with the evidence. The mock records every message each simulated
robot receives (robots/<ip>_<domain>.jsonl), so ordering and stop behaviour are checked on what the
"robot" actually got, not on what the API said.

    python3 -I hive_e2e.py <mock_root> [base_url]
"""
import glob, http.client, json, os, queue, socket, sys, threading, time, urllib.error, urllib.request

MOCK = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8787/api"
ORIGIN = "http://127.0.0.1:5173"
results = []

CAR = {"ip": "192.168.1.20", "domain_id": 42}       # online, driver running
OFF = {"ip": "192.168.1.31", "domain_id": 43}       # powered off
NODRV = {"ip": "192.168.1.44", "domain_id": 44}     # online, but nothing subscribes to /cmd_vel
LIDAR = {"ip": "192.168.1.50", "domain_id": 45}     # /cmd_vel listener, /scan, /ros_data echo
POS = {"ip": "192.168.1.61", "domain_id": 47}       # /gripper/position subscriber only
LIDAR2 = {"ip": "192.168.1.51", "domain_id": 48}    # a second LiDAR car, for cross-talk checks
GRIP = {"ip": "192.168.1.60", "domain_id": 46}      # Trigger gripper services, prefixed /ros_data echo
MANY = {"ip": "192.168.1.70", "domain_id": 49}      # a camera car with a dozen non-image topics, like the real one


def call(path, body=None, method=None, origin=ORIGIN, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", origin)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), time.monotonic() - t0, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {"raw": raw.decode(errors="replace")}
        return e.code, payload, time.monotonic() - t0, dict(e.headers)


def received(robot):
    path = f"{MOCK}/robots/{robot['ip']}_{robot['domain_id']}.jsonl"
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def reset():
    for f in glob.glob(f"{MOCK}/robots/*.jsonl"):
        os.remove(f)


def check(name, ok, evidence):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {evidence}")


def moving(msg):
    return "x: 0.0" not in msg.split("angular")[0] or "z: 0.0" not in msg.split("angular")[1]


# ------------------------------------------------------------------ checks
def t_health():
    s, d, _, _ = call("/health")
    check("health reports whether ROS 2 is installed", s == 200 and "ros" in d, f"HTTP {s} {d}")


def t_connect_real():
    s, d, dt, _ = call("/robots/connect", CAR)
    check("connect to a running robot succeeds", s == 200 and d.get("ok"), f"HTTP {s} in {dt:.1f}s nodes={d.get('nodes')}")
    check("connect reports what it found (nodes, topics, /cmd_vel listener)",
          bool(d.get("nodes")) and any(t.get("name") == "/cmd_vel" for t in d.get("topics", [])) and d.get("cmd_vel_subscribers", 0) >= 1,
          f"topics={[t.get('name') for t in d.get('topics', [])]} cmd_vel_subscribers={d.get('cmd_vel_subscribers')}")


def t_connect_off():
    s, d, dt, _ = call("/robots/connect", OFF)
    check("connect to a powered-off robot FAILS (no false Live)", s >= 400 and not d.get("ok"), f"HTTP {s} in {dt:.1f}s error={d.get('error')!r}")


def t_connect_nodriver():
    s, d, _, _ = call("/robots/connect", NODRV)
    warn = (d.get("warnings") or [])
    check("connect warns when nothing listens on /cmd_vel", any("/cmd_vel" in w for w in warn) or (s >= 400 and "/cmd_vel" in str(d.get("error"))),
          f"HTTP {s} warnings={warn} error={d.get('error')!r}")


def t_no_subscriber_fast():
    s, d, dt, _ = call("/robots/control", {**NODRV, "action": "forward"})
    reason = str(d.get("error", "")).lower()
    check("drive with no /cmd_vel listener fails fast with a clear reason", s >= 400 and dt < 7.5 and ("subscri" in reason or "listening" in reason),
          f"HTTP {s} in {dt:.1f}s error={d.get('error')!r}")


def t_order_stop_wins():
    """Press forward, then stop 0.2 s later: the robot's LAST message must be a zero Twist."""
    reset()
    th = threading.Thread(target=lambda: call("/robots/control", {**CAR, "action": "forward"}))
    th.start()
    time.sleep(0.2)
    call("/robots/control", {**CAR, "action": "stop"})
    th.join()
    time.sleep(1.5)
    msgs = [m for m in received(CAR) if m["topic"] == "/cmd_vel"]
    last = msgs[-1]["msg"] if msgs else None
    check("stop pressed after forward is the last thing the robot gets", bool(msgs) and not moving(last),
          f"{len(msgs)} msgs, last={last!r}, sequence={[('MOVE' if moving(m['msg']) else 'STOP') for m in msgs]}")


def t_hold_and_deadman():
    """Hold forward via the drive stream with keepalives, then go silent: the robot must stop by itself."""
    reset()
    s, d, _, _ = call("/robots/drive", {**CAR, "action": "forward"})
    if s == 404:
        check("hold-to-drive stream exists", False, "HTTP 404: no /robots/drive endpoint")
        return
    for _ in range(8):                         # 2 s of keepalives
        time.sleep(0.25)
        call("/robots/drive/keepalive", CAR)
    t_quiet = time.monotonic()
    time.sleep(3.0)                            # client vanishes: no keepalive, no stop
    msgs = [m for m in received(CAR) if m["topic"] == "/cmd_vel"]
    moves = [m for m in msgs if moving(m["msg"])]
    after = [m for m in moves if m["t"] > t_quiet + 1.6]
    last = msgs[-1]["msg"] if msgs else None
    check("holding drive streams /cmd_vel continuously", len(moves) >= 8, f"{len(moves)} move messages while held (start HTTP {s})")
    check("deadman stops the robot when the client goes silent", bool(msgs) and not after and not moving(last),
          f"move msgs >1.6 s after silence: {len(after)} at {[round(m['t'] - t_quiet, 3) for m in after]} s; last move at {round(moves[-1]['t'] - t_quiet, 3) if moves else None} s; last={last!r}")


def t_stream_rate_and_latency():
    """A connected robot's worker is already running: a press reaches the robot at once, and the
    stream runs at the worker's 10 Hz."""
    reset()
    t_press = time.monotonic()
    s, _, dt, _ = call("/robots/drive", {**CAR, "action": "forward"})
    for _ in range(8):
        time.sleep(0.25)
        call("/robots/drive/keepalive", CAR)
    call("/robots/drive", {**CAR, "action": "stop"})
    msgs = [m for m in received(CAR) if m["topic"] == "/cmd_vel" and moving(m["msg"])]
    first = msgs[0]["t"] - t_press if msgs else None
    span = msgs[-1]["t"] - msgs[0]["t"] if len(msgs) > 1 else 0
    rate = (len(msgs) - 1) / span if span else 0
    check("a press reaches the robot within 0.3 s", s == 200 and first is not None and first < 0.3,
          f"HTTP {s} in {dt:.3f}s, first move message after {first if first is None else round(first, 3)}s")
    check("holding drive streams /cmd_vel at about 10 Hz", 8 <= rate <= 12, f"{len(msgs)} messages at {rate:.1f} Hz")


def t_stale_command_dropped():
    """A drive command that arrives after a newer stop (slow network) must not restart the robot."""
    reset()
    order = {"session": "test-tab", "seq": 1000}
    call("/robots/drive", {**CAR, "action": "stop", **order, "seq": 1001})
    s, d, _, _ = call("/robots/drive", {**CAR, "action": "forward", **order})
    time.sleep(0.5)
    msgs = [m for m in received(CAR) if m["topic"] == "/cmd_vel"]
    check("a drive command older than the last stop is rejected and never reaches the robot",
          s == 409 and d.get("superseded") and not any(moving(m["msg"]) for m in msgs),
          f"HTTP {s} superseded={d.get('superseded')} robot got={[('MOVE' if moving(m['msg']) else 'STOP') for m in msgs]}")


def t_channels_independent():
    """Drive and gripper commands are ordered separately: a newer gripper command must not make a drive
    command look stale."""
    reset()
    order = {"session": "test-tab-2"}
    s1, _, _, _ = call("/robots/control", {**CAR, "action": "gripper_close", **order, "seq": 11})
    s2, _, _, _ = call("/robots/drive", {**CAR, "action": "stop", **order, "seq": 10})
    got = [m["topic"] for m in received(CAR)]
    check("a gripper command and a drive command do not supersede each other", s1 == 200 and s2 == 200 and "/gripper_open" in got and "/cmd_vel" in got,
          f"gripper HTTP {s1}, drive HTTP {s2}, robot got={got}")


def t_release_stops():
    reset()
    call("/robots/drive", {**CAR, "action": "left"})
    time.sleep(1.6)
    t_rel = time.monotonic()
    call("/robots/drive", {**CAR, "action": "stop"})
    time.sleep(1.5)
    msgs = [m for m in received(CAR) if m["topic"] == "/cmd_vel"]
    late = [m for m in msgs if moving(m["msg"]) and m["t"] > t_rel + 0.3]
    check("releasing (stop) halts the stream at once", bool(msgs) and not late and not moving(msgs[-1]["msg"]),
          f"{len(late)} move msgs after release; last={msgs[-1]['msg'] if msgs else None!r}")


def t_gripper():
    reset()
    s, d, _, _ = call("/robots/control", {**CAR, "action": "gripper_open"})
    got = [m for m in received(CAR) if m["topic"] == "/gripper_open"]
    check("gripper open reaches /gripper_open as Bool true", s == 200 and got and "true" in got[-1]["msg"], f"HTTP {s} got={[m['msg'] for m in got]}")


def t_command_text():
    reset()
    text = "Patrol the east bay, then park; don't touch $HOME \"or\" `this`"
    s, d, _, _ = call("/robots/command", {**CAR, "text": text})
    got = [m for m in received(CAR) if m["topic"] == "/hive/command"]
    check("command text reaches /hive/command intact (quotes, $, backticks)", s == 200 and got and json.loads(got[-1]["msg"].split("data: ", 1)[1].rstrip("}").strip()) == text,
          f"HTTP {s} got={[m['msg'] for m in got]}")


def t_cors():
    s, _, _, h = call("/robots/control", {**CAR, "action": "stop"}, origin="https://evil.example")
    allow = {k.lower(): v for k, v in h.items()}.get("access-control-allow-origin")
    check("a foreign website cannot drive the robot", s == 403 and allow != "*", f"HTTP {s} allow-origin={allow!r}")
    s, _, _, h = call("/robots/control", {**CAR, "action": "stop"})
    allow = {k.lower(): v for k, v in h.items()}.get("access-control-allow-origin")
    check("the Hive app's own origin is allowed", s == 200 and allow == ORIGIN, f"HTTP {s} allow-origin={allow!r}")


def t_bad_input():
    s, d, _, _ = call("/robots/control", {"ip": "192.168.1.20 && echo injected", "domain_id": 42, "action": "stop"})
    check("shell metacharacters in the IP are rejected", s == 400, f"HTTP {s} {d.get('error')!r}")
    s, d, _, _ = call("/robots/control", ["not", "an", "object"])
    check("a non-object JSON body is a 400, not a 500", s == 400, f"HTTP {s} {d.get('error')!r}")


def zero_twists(robot):
    return [m for m in received(robot) if m["topic"] == "/cmd_vel" and not moving(m["msg"])]


def link_after_stop(robot, want, limit=1.0):
    """Stop, then poll keepalive until link.stop == want (or the limit passes). Returns the last link."""
    call("/robots/drive", {**robot, "action": "stop"})
    t0, link = time.monotonic(), {}
    while time.monotonic() - t0 < limit:
        s, d, _, _ = call("/robots/drive/keepalive", robot)
        link = d.get("link") or {}
        if link.get("stop") == want:
            break
        time.sleep(0.05)
    return link


def t_power_scales_twist():
    call("/robots/connect", CAR)
    reset()
    s, d, _, _ = call("/robots/drive", {**CAR, "action": "forward", "power": 0.5})
    time.sleep(0.3)
    call("/robots/drive", {**CAR, "action": "stop"})
    xs = [m["msg"] for m in received(CAR) if m["topic"] == "/cmd_vel" and moving(m["msg"])]
    check("drive power 0.5 publishes linear.x 0.25", s == 200 and bool(xs) and all("x: 0.25" in x for x in xs), f"HTTP {s} msgs={xs[:2]}")
    s, d, _, _ = call("/robots/drive", {**CAR, "action": "forward", "power": "fast"})
    check("a non-numeric power is 400 bad_power", s == 400 and d.get("code") == "bad_power", f"HTTP {s} {d}")
    call("/robots/drive", {**CAR, "action": "stop"})


def t_keep_last_one():
    call("/robots/disconnect", CAR)
    reset()
    call("/robots/connect", CAR)
    path = f"{MOCK}/robots/worker_pubs.jsonl"
    pubs = [json.loads(l) for l in open(path) if l.strip()] if os.path.exists(path) else []
    cmd = [p for p in pubs if p["topic"] == "/cmd_vel"]
    check("the worker creates /cmd_vel with KeepLast(1)", bool(cmd) and all(p["depth"] == 1 for p in cmd), f"publishers={cmd}")


def t_stop_burst():
    call("/robots/connect", CAR)
    call("/robots/drive", {**CAR, "action": "forward"})
    time.sleep(0.4)
    reset()
    call("/robots/drive", {**CAR, "action": "stop"})
    time.sleep(0.6)
    zs = zero_twists(CAR)
    within = [m for m in zs if zs and m["t"] <= zs[0]["t"] + 0.3]
    check("a stop yields at least 5 zero Twists within 300 ms", len(within) >= 5, f"{len(within)} within 300 ms of {len(zs)} zero twists")


def t_stop_confirmed():
    call("/robots/connect", LIDAR)
    call("/robots/drive", {**LIDAR, "action": "forward"})
    for _ in range(4):
        time.sleep(0.25)
        call("/robots/drive/keepalive", LIDAR)
    link = link_after_stop(LIDAR, "confirmed")
    check("a stop is confirmed from the car's own /ros_data echo", link.get("stop") == "confirmed" and "0,1,0,1" in str(link.get("last_line")), f"link={link}")


def t_stop_unreported():
    call("/robots/connect", NODRV)
    link = link_after_stop(NODRV, "unreported")
    check("a car with no /ros_data reports the stop as unreported", link.get("stop") == "unreported" and link.get("reporting") is False, f"link={link}")


def t_gripper_trigger():
    s, d, _, _ = call("/robots/connect", GRIP)
    reset()
    s2, d2, _, _ = call("/robots/control", {**GRIP, "action": "gripper_close"})
    hits = [m["topic"] for m in received(GRIP)]
    check("trigger gripper: close calls /gripper/grab, connect reports the interface",
          d.get("gripper_interface") == "trigger" and s2 == 200 and "/gripper/grab" in hits,
          f"interface={d.get('gripper_interface')} HTTP {s2} robot got={hits}")
    check("connect lists services and topic kinds", any(x.get("name") == "/gripper/grab" for x in d.get("services", [])) and all("kind" in t for t in d.get("topics", [])),
          f"services={d.get('services')} topics={d.get('topics')}")
    reset()
    call("/robots/control", {**GRIP, "action": "gripper_open"})
    check("trigger gripper: open calls /gripper/release", "/gripper/release" in [m["topic"] for m in received(GRIP)], f"robot got={[m['topic'] for m in received(GRIP)]}")


def t_no_gripper():
    call("/robots/connect", NODRV)
    s, d, _, _ = call("/robots/control", {**NODRV, "action": "gripper_close"})
    check("no gripper interface is a 409 no_gripper", s == 409 and d.get("code") == "no_gripper", f"HTTP {s} {d}")


def t_other_publishers():
    """Connect reports the graph at that instant, so the check waits for the worker's mock to see the new
    robots.json (cached 30 ms, plus drive-mount lag) and polls for up to 2 s instead of reading once."""
    restore = set_mock_robot("192.168.1.20:42", extra_cmd_vel_publishers=1)
    try:
        s, d, end = 0, {}, time.monotonic() + 2.0
        while True:
            s, d, _, _ = call("/robots/connect", CAR)
            if (s == 200 and d.get("other_publishers") == 1) or time.monotonic() >= end:
                break
            time.sleep(0.1)
    finally:
        restore()
    check("connect reports another publisher on /cmd_vel", s == 200 and d.get("other_publishers") == 1 and any("publish" in w for w in d.get("warnings", [])),
          f"HTTP {s} other_publishers={d.get('other_publishers')} warnings={d.get('warnings')}")


def t_rmw_pinned():
    call("/robots/connect", CAR)
    env = json.load(open(f"{MOCK}/robots/worker_env.json"))
    check("the worker's RMW implementation is pinned", env.get("RMW_IMPLEMENTATION") == "rmw_fastrtps_cpp", f"env={env}")


def t_stop_ignores_bad_power():
    call("/robots/connect", CAR)
    reset()
    s, d, _, _ = call("/robots/drive", {**CAR, "action": "stop", "power": "fast"})
    s2, _, _, _ = call("/robots/control", {**CAR, "action": "stop", "power": {"x": 1}})
    time.sleep(0.3)
    check("a stop with an invalid power is still accepted and published", s == 200 and s2 == 200 and len(zero_twists(CAR)) >= 5,
          f"drive HTTP {s}, control HTTP {s2}, zero twists={len(zero_twists(CAR))}")


def t_bool_gripper_has_no_position():
    call("/robots/connect", CAR)
    reset()
    s, d, _, _ = call("/robots/control", {**CAR, "action": "gripper_position", "value": 0.8})
    got = [m for m in received(CAR) if m["topic"] == "/gripper_open"]
    check("a bool gripper refuses position with 409 no_position and publishes nothing", s == 409 and d.get("code") == "no_position" and not got,
          f"HTTP {s} {d.get('code')} published={got}")


def t_position_gripper_first_command():
    call("/robots/disconnect", POS)
    reset()
    s, d, _, _ = call("/robots/connect", POS)
    path = f"{MOCK}/robots/worker_pubs.jsonl"
    pubs = [json.loads(l)["topic"] for l in open(path) if l.strip()] if os.path.exists(path) else []
    s2, d2, _, _ = call("/robots/control", {**POS, "action": "gripper_position", "value": 0.3})
    got = [m["msg"] for m in received(POS) if m["topic"] == "/gripper/position"]
    check("the position publisher exists before the first command, which arrives",
          d.get("gripper_interface") == "position" and "/gripper/position" in pubs and s2 == 200 and any("0.3" in g for g in got),
          f"interface={d.get('gripper_interface')} publishers={pubs} HTTP {s2} got={got}")


def t_tap_not_falsely_confirmed():
    """Tap forward and stop at once, with no keepalive in between: confirmation must come from a zero line
    that follows the car's motion, never from the earlier idle stop's line."""
    call("/robots/connect", LIDAR)
    call("/robots/drive", {**LIDAR, "action": "stop"})
    time.sleep(0.3)
    call("/robots/drive", {**LIDAR, "action": "forward"})
    s, d, _, _ = call("/robots/drive", {**LIDAR, "action": "stop"})
    early = (d.get("link") or {}).get("stop")
    final = link_after_stop(LIDAR, "confirmed")
    check("a quick tap is not confirmed from a stale zero line, and then confirms for real",
          early in ("pending", "unconfirmed") and final.get("stop") == "confirmed",
          f"link in the stop reply={early} final={final.get('stop')} last_line={final.get('last_line')}")


# ------------------------------------------------------------------ live streams
IMAGE_TOPIC = "/camera/camera/color/image_raw"
PART_HEAD = b"--hiveframe\r\nContent-Type: image/jpeg\r\nContent-Length: "


def open_get(path):
    conn = http.client.HTTPConnection(BASE.split("//")[1].split("/")[0], timeout=10)
    conn.connect()
    conn.request("GET", "/api" + path, headers={"Origin": ORIGIN})
    sock = conn.sock  # http.client drops its own reference once the response will close, so keep ours
    return conn, conn.getresponse(), sock


class Stream:
    """A GET stream read on a thread: SSE events land in .events, MJPEG part times in .parts."""

    def __init__(self, path):
        self.conn, self.resp, self.sock = open_get(path)
        self.status = self.resp.status
        self.events, self.parts, self.error = queue.Queue(), [], None
        if self.status == 200:
            threading.Thread(target=self.pump, daemon=True).start()
        else:
            self.error = json.loads(self.resp.read() or b"{}")

    def pump(self):
        mjpeg = "multipart" in (self.resp.getheader("Content-Type") or "")
        try:
            if mjpeg:
                buf = b""
                while True:
                    chunk = self.resp.read1(65536)
                    if not chunk:
                        return
                    buf += chunk
                    while True:
                        i = buf.find(PART_HEAD)
                        if i < 0:
                            buf = buf[-len(PART_HEAD):]
                            break
                        self.parts.append(time.monotonic())
                        buf = buf[i + len(PART_HEAD):]
            event = None
            for raw in self.resp:
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    event = line[7:]
                elif line.startswith("data:"):
                    self.events.put((event, json.loads(line[6:])))
        except Exception:
            pass

    def wait(self, pred, seconds):
        """Collect events until pred(event, data) is true: returns (matching event or None, all seen)."""
        seen, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                ev = self.events.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            seen.append(ev)
            if pred(*ev):
                return ev, seen
        return None, seen

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        self.conn.close()


def subs_events(robot):
    path = f"{MOCK}/robots/{robot['ip']}_{robot['domain_id']}.subs.jsonl"
    return [json.loads(l) for l in open(path) if l.strip()] if os.path.exists(path) else []


def scan_stream(robot):
    return Stream(f"/robots/stream?ip={robot['ip']}&domain_id={robot['domain_id']}&topics=/scan")


def t_sse_scan():
    call("/robots/connect", LIDAR)
    s = scan_stream(LIDAR)
    try:
        ev, _ = s.wait(lambda e, d: e == "data" and d.get("kind") == "scan", 3)
    finally:
        s.close()
    d = ev[1] if ev else {}
    n = len(d.get("data", {}).get("ranges_mm", []))
    check("SSE delivers a LaserScan with 360 ranges within 3 s", s.status == 200 and n == 360,
          f"HTTP {s.status} kind={d.get('kind')} ranges={n} hz={d.get('hz')} bps={d.get('bytes_per_s')}")


def t_mjpeg_frames():
    call("/robots/connect", CAR)
    s = Stream(f"/robots/camera.mjpg?ip={CAR['ip']}&domain_id={CAR['domain_id']}&topic={IMAGE_TOPIC}")
    time.sleep(2)
    n = len(s.parts)
    ctype = s.resp.getheader("Content-Type") or ""
    s.close()
    check("MJPEG delivers at least 5 JPEG parts in 2 s", s.status == 200 and n >= 5 and "boundary=hiveframe" in ctype,
          f"HTTP {s.status} {n} parts, Content-Type={ctype}")


def t_unwatch_on_disconnect():
    reset()
    call("/robots/connect", LIDAR)
    s = scan_stream(LIDAR)
    s.wait(lambda e, d: e == "data", 3)
    made = [e for e in subs_events(LIDAR) if e["topic"] == "/scan" and e["event"] == "create"]
    s.close()
    t0, destroyed = time.monotonic(), []
    while time.monotonic() - t0 < 2 and not destroyed:
        time.sleep(0.05)
        destroyed = [e for e in subs_events(LIDAR) if e["topic"] == "/scan" and e["event"] == "destroy"]
    check("closing the stream destroys the worker subscription within 2 s", bool(made) and bool(destroyed),
          f"created={len(made)} destroyed={len(destroyed)} after {time.monotonic() - t0:.2f}s")


def t_unwatch_on_instant_disconnect():
    """A client that resets the connection straight after sending its request must not leave the worker watching."""
    reset()
    call("/robots/connect", LIDAR)
    host, port = BASE.split("//")[1].split("/")[0].split(":")
    for _ in range(5):
        raw = socket.create_connection((host, int(port)))
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, __import__("struct").pack("ii", 1, 0))  # close sends RST
        raw.sendall(f"GET /api/robots/stream?ip={LIDAR['ip']}&domain_id={LIDAR['domain_id']}&topics=/scan HTTP/1.0\r\nOrigin: {ORIGIN}\r\n\r\n".encode())
        raw.close()
    t0, ev = time.monotonic(), []
    while time.monotonic() - t0 < 3:
        time.sleep(0.1)
        ev = [e["event"] for e in subs_events(LIDAR) if e["topic"] == "/scan"]
        if ev and ev.count("create") == ev.count("destroy"):
            break
    check("clients that reset at once leave no worker subscription behind", bool(ev) and ev.count("create") == ev.count("destroy"),
          f"subscription events={ev}")


def t_stale_on_drop():
    path = f"{MOCK}/robots.json"
    original = open(path).read()
    key = f"{LIDAR['ip']}:{LIDAR['domain_id']}"
    call("/robots/connect", LIDAR)
    s = scan_stream(LIDAR)
    stale, back, dt, gap, last_data = None, None, 99.0, 99.0, [0.0]
    try:
        s.wait(lambda e, d: e == "data", 3)
        robots = json.loads(original)
        robots[key]["online"] = False
        write_robots(json.dumps(robots))
        t0 = last_data[0] = time.monotonic()

        def stale_scan(e, d):  # note when the last scan came: stale is defined from it (no data for 2 s)
            if e == "data" and d.get("topic") == "/scan":
                last_data[0] = time.monotonic()
            return e == "status" and d.get("stale") is True and d.get("topic") == "/scan"

        stale, _ = s.wait(stale_scan, 5)
        now = time.monotonic()
        dt, gap = now - t0, now - last_data[0]
        write_robots(original)
        back, _ = s.wait(lambda e, d: e == "status" and d.get("stale") is False, 3)
    finally:
        write_robots(original)
        s.close()
    # gap: from the last scan to the stale flag (the spec's 2 s, plus the 0.25 s poll). dt also includes how long
    # the renamed robots.json takes to become visible to the worker on a drive mount, so it is bounded loosely.
    check("a dropped robot makes the stream report stale within 3 s of its last scan, and recovery clears it",
          stale is not None and gap <= 3.0 and dt <= 4.5 and back is not None,
          f"stale {gap:.2f}s after the last scan ({dt:.2f}s after the robot was switched off), recovered={back is not None}")


def t_no_cross_talk_streams():
    call("/robots/connect", LIDAR)
    call("/robots/connect", LIDAR2)
    a, b = scan_stream(LIDAR), scan_stream(LIDAR2)
    try:
        got = {}
        for name, st in (("a", a), ("b", b)):
            _, seen = st.wait(lambda e, d: False, 1.5)
            got[name] = [d["frame_id"] for e, d in seen if e == "data"]
    finally:
        a.close()
        b.close()
    ok = len(got["a"]) >= 3 and len(got["b"]) >= 3 and set(got["a"]) == {LIDAR["ip"]} and set(got["b"]) == {LIDAR2["ip"]}
    check("two cars streaming /scan never see each other's data", ok,
          f"a={sorted(set(got['a']))} x{len(got['a'])} b={sorted(set(got['b']))} x{len(got['b'])}")


def t_drive_tick_under_load():
    reset()
    call("/robots/connect", CAR)
    cam = Stream(f"/robots/camera.mjpg?ip={CAR['ip']}&domain_id={CAR['domain_id']}&topic={IMAGE_TOPIC}")
    time.sleep(0.5)
    call("/robots/drive", {**CAR, "action": "forward"})
    for _ in range(16):                      # 4 s of keepalives
        time.sleep(0.25)
        call("/robots/drive/keepalive", CAR)
    call("/robots/drive", {**CAR, "action": "stop"})
    frames = len(cam.parts)
    cam.close()
    ts = [m["t"] for m in received(CAR) if m["topic"] == "/cmd_vel" and moving(m["msg"])]
    gaps = sorted((b - a) * 1000 for a, b in zip(ts, ts[1:]))
    within = sum(1 for g in gaps if 80 <= g <= 120)
    frac = within / len(gaps) if gaps else 0
    detail = (f"{len(gaps)} intervals, {frac:.1%} in 80-120 ms, p50={gaps[len(gaps) // 2]:.1f} ms "
              f"p99={gaps[int(len(gaps) * 0.99)]:.1f} ms max={gaps[-1]:.1f} ms, {frames} MJPEG frames") if gaps else "no intervals"
    check("the 10 Hz drive tick holds 100 +/- 20 ms for 95% of intervals while a 30 Hz camera streams",
          len(gaps) > 30 and frac >= 0.95 and frames >= 20, detail)


def t_sse_rejects_image():
    call("/robots/connect", CAR)
    s = Stream(f"/robots/stream?ip={CAR['ip']}&domain_id={CAR['domain_id']}&topics={IMAGE_TOPIC}")
    try:
        ev, seen = s.wait(lambda e, d: e == "status" and d.get("topic") == IMAGE_TOPIC, 3)
    finally:
        s.close()
    frames = [d for e, d in seen if e == "data"]
    check("SSE never streams an image topic: it reports use_mjpeg and keeps the stream open for link events",
          s.status == 200 and ev is not None and ev[1].get("error") == "use_mjpeg" and not frames
          and any(e == "link" for e, _ in seen), f"HTTP {s.status} events={[e for e, _ in seen]} status={ev}")


def t_sse_link_only():
    """A page that watches no data topic still gets link events, the initial one and every later one (R27)."""
    call("/robots/connect", LIDAR)
    s = Stream(f"/robots/stream?ip={LIDAR['ip']}&domain_id={LIDAR['domain_id']}&topics=")
    try:
        first, _ = s.wait(lambda e, d: e == "link", 3)
        before = (first[1] if first else {}).get("seq", 0)
        call("/robots/drive", {**LIDAR, "action": "stop"})
        later, seen = s.wait(lambda e, d: e == "link" and d.get("seq", 0) > before and d.get("stop") in ("pending", "confirmed"), 3)
    finally:
        s.close()
    check("a stream with no data topics opens and carries the link, including updates after a stop",
          s.status == 200 and first is not None and later is not None and not any(e == "data" for e, _ in seen),
          f"HTTP {s.status} first={first} later={later}")


def t_link_seq_and_route():
    """R28: links from every route carry a growing seq; /robots/link and /robots/status return the newest."""
    call("/robots/connect", LIDAR)
    seqs = []
    s1, d1, _, _ = call("/robots/drive", {**LIDAR, "action": "forward"})
    seqs.append((d1.get("link") or {}).get("seq"))
    time.sleep(0.2)
    _, d2, _, _ = call("/robots/drive/keepalive", LIDAR)
    seqs.append((d2.get("link") or {}).get("seq"))
    _, d3, _, _ = call("/robots/drive", {**LIDAR, "action": "stop"})
    seqs.append((d3.get("link") or {}).get("seq"))
    time.sleep(0.6)
    s4, d4, _, _ = call("/robots/link", LIDAR)
    seqs.append((d4.get("link") or {}).get("seq"))
    s5, d5, _, _ = call("/robots/status", LIDAR)
    s6, d6, _, _ = call("/robots/link", {"ip": "192.168.1.99", "domain_id": 49})
    ok = (all(isinstance(s, int) for s in seqs) and seqs == sorted(seqs) and len(set(seqs)) == 4
          and s4 == 200 and (d4.get("link") or {}).get("stop") == "confirmed"
          and s5 == 200 and (d5.get("link") or {}).get("seq", 0) >= seqs[-1] and d5.get("worker_replaced") is False
          and s6 == 502 and d6.get("code") == "worker_gone")
    check("link seq grows across drive, keepalive, stop and /robots/link; status carries the newest link", ok,
          f"seqs={seqs} link={d4.get('link')} status={s5} {d5.get('link')} replaced={d5.get('worker_replaced')} unknown robot={s6} {d6.get('code')}")


def t_sse_many_topics():
    """The real camera car has 9+ non-image topics: the stream must open for all of them (final audit #1)."""
    s0, found, _, _ = call("/robots/connect", MANY)
    names = [t["name"] for t in found.get("topics", []) if t.get("kind") != "image"]
    q = f"ip={MANY['ip']}&domain_id={MANY['domain_id']}"
    s = Stream(f"/robots/stream?{q}&topics={','.join(names)}")
    try:
        link, seen = s.wait(lambda e, d: e == "link", 4)
        _, more = s.wait(lambda e, d: False, 0.5)
    finally:
        s.close()
    errors = {d["topic"]: d["error"] for e, d in seen + more if e == "status" and "error" in d}
    check("a car with 12 non-image topics streams all of them on one connection and carries the link",
          s0 == 200 and len(names) >= 12 and s.status == 200 and link is not None
          and set(errors.values()) <= {"watch_failed"} and "/ros_data" not in errors and "/esp_data" not in errors,
          f"connect HTTP {s0} topics={len(names)} stream HTTP {s.status} link={link is not None} errors={errors}")
    fakes = [f"/nope{i}" for i in range(25)]
    big = Stream(f"/robots/stream?{q}&topics={','.join(names[:12] + fakes)}")
    try:
        _, seen = big.wait(lambda e, d: e == "status" and d.get("topic") == fakes[-1], 5)
    finally:
        big.close()
    over = sorted(d["topic"] for e, d in seen if e == "status" and d.get("error") == "too_many")
    check("past 32 topics the extra ones are reported as too_many and the stream still opens",
          big.status == 200 and over == sorted(fakes[-5:]), f"HTTP {big.status} too_many={over}")


def t_stream_route_errors():
    call("/robots/connect", CAR)
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}"
    cases = {  # an unknown SSE topic is a status event on an open stream (t_sse_mixed_topics), not an HTTP error
        "non-image on MJPEG": (f"/robots/camera.mjpg?{q}&topic=/cmd_vel", 400, "use_sse"),
        "unknown MJPEG topic": (f"/robots/camera.mjpg?{q}&topic=/nope", 404, "unknown_topic"),
        "no worker": ("/robots/stream?ip=192.168.1.99&domain_id=49&topics=/scan", 502, "worker_gone"),
        "no MJPEG topic": (f"/robots/camera.mjpg?{q}&topic=", 400, "bad_topics"),
        "topic without a slash": (f"/robots/stream?{q}&topics=scan", 400, "bad_topics"),
        "bad ip": ("/robots/stream?ip=bad%20ip&domain_id=1&topics=/scan", 400, None),
        "bad domain": (f"/robots/stream?ip={CAR['ip']}&domain_id=999&topics=/scan", 400, None),
    }
    bad = []
    for name, (path, status, code) in cases.items():
        s, d, _, _ = call(path)
        if s != status or (code and d.get("code") != code):
            bad.append(f"{name}: HTTP {s} {d}")
    s, d, _, _ = call(f"/robots/stream?{q}&topics=/cmd_vel", origin="http://evil.example")
    if s != 403:
        bad.append(f"foreign origin: HTTP {s}")
    check("stream routes validate topic, kind, worker, input and origin", not bad, "; ".join(bad) or f"{len(cases) + 1} cases")


def t_sse_mixed_topics():
    """One bad topic must not sink the stream: skipped topics are reported as status errors."""
    call("/robots/connect", CAR)
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}"
    s = Stream(f"/robots/stream?{q}&topics=/nope,{IMAGE_TOPIC},/ros_data,/tf")
    try:
        _, seen = s.wait(lambda e, d: e == "status" and d.get("topic") == "/tf", 3)
    finally:
        s.close()
    errors = {d["topic"]: d["error"] for e, d in seen if e == "status" and "error" in d}
    ok = (s.status == 200 and errors.get("/nope") == "unknown_topic" and errors.get(IMAGE_TOPIC) == "use_mjpeg"
          and errors.get("/tf") == "watch_failed" and "/ros_data" not in errors)
    check("a mixed topic list streams the valid topics and reports each bad one as a status error", ok,
          f"HTTP {s.status} errors={errors}")
    s2 = Stream(f"/robots/stream?{q}&topics=/nope,{IMAGE_TOPIC}")
    try:
        _, seen2 = s2.wait(lambda e, d: e == "status" and d.get("topic") == IMAGE_TOPIC, 3)
    finally:
        s2.close()
    errors2 = {d["topic"]: d["error"] for e, d in seen2 if e == "status" and "error" in d}
    check("a list with no valid topic still opens (200), reports each topic, and is not retried as an error",
          s2.status == 200 and errors2 == {"/nope": "unknown_topic", IMAGE_TOPIC: "use_mjpeg"},
          f"HTTP {s2.status} errors={errors2}")


def t_sse_image_stats_while_mjpeg_open():
    call("/robots/connect", CAR)
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}"
    cam = Stream(f"/robots/camera.mjpg?{q}&topic={IMAGE_TOPIC}")
    s = Stream(f"/robots/stream?{q}&topics=/ros_data&images={IMAGE_TOPIC}")
    try:
        ev, seen = s.wait(lambda e, d: e == "image_stats" and d.get("topic") == IMAGE_TOPIC, 4)
        frames = [d for e, d in seen if e == "data" and d.get("kind") == "image"]
    finally:
        s.close()
        cam.close()
    d = ev[1] if ev else {}
    check("image_stats arrives while MJPEG is open, with rate, bandwidth and size, and no frames",
          bool(ev) and d.get("hz", 0) > 0 and d.get("bytes_per_s", 0) > 0 and d.get("width") and d.get("height") and not frames,
          f"HTTP {s.status} {d}")
    # the mock camera sends 640x480 rgb8 at 30 Hz (about 27.6 MB/s raw); its solid-colour JPEG is a few KB
    check("image_stats reports the raw rate from the car, not the JPEG size on the laptop",
          bool(ev) and d.get("bytes_per_s", 0) > 5e6 and 5 <= d.get("hz", 0) <= 40 and 0 < d.get("shown_hz", 0) <= 16,
          f"bytes_per_s={d.get('bytes_per_s')} hz={d.get('hz')} shown_hz={d.get('shown_hz')}")


def param_log(robot):
    path = f"{MOCK}/robots/{robot['ip']}_{robot['domain_id']}.params.jsonl"
    return [json.loads(l) for l in open(path) if l.strip()] if os.path.exists(path) else []


def write_robots(text):
    """Workers re-read robots.json constantly: never leave it half-written (the mock also keeps its last
    good copy if it catches the file mid-replace on a drive mount, where os.replace is not atomic)."""
    path = f"{MOCK}/robots.json"
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


SETTLE = 0.3  # the worker's mock re-reads robots.json at most every 30 ms, and a drive mount can lag


def set_mock_robot(robot_key, **fields):
    """Change one robot's mock config and let it settle; returns a function that restores the original."""
    original = open(f"{MOCK}/robots.json").read()
    robots = json.loads(original)
    robots[robot_key].update(fields)
    write_robots(json.dumps(robots))
    time.sleep(SETTLE)

    def restore():
        write_robots(original)
        time.sleep(SETTLE)

    return restore


def t_camera_tuned_on_connect():
    reset()
    s, d, _, _ = call("/robots/connect", CAR)
    log = param_log(CAR)
    got = [(p["name"], p["value"]) for p in log if p["successful"]]
    cam = d.get("camera") or {}
    check("connect tunes the RealSense: depth off, colour 424x240x15",
          ("enable_depth", False) in got and ("rgb_camera.color_profile", "424x240x15") in got
          and cam.get("applied") is True and cam.get("node") == "/camera/camera" and cam.get("quality") == "medium",
          f"HTTP {s} camera={cam} logged={got}")
    s2, d2, _, _ = call("/robots/connect", LIDAR)
    check("connect to a robot without a RealSense reports camera null", s2 == 200 and "camera" in d2 and d2["camera"] is None, f"HTTP {s2} camera={d2.get('camera')!r}")


def t_camera_quality_change():
    reset()
    call("/robots/connect", CAR)
    s, d, _, _ = call("/robots/camera", {**CAR, "quality": "low", "depth": False})
    cam = d.get("camera", d)
    last = [p["value"] for p in param_log(CAR) if p["name"] == "rgb_camera.color_profile" and p["successful"]]
    s2, d2, _, _ = call("/robots/camera", {**CAR, "quality": "ultra", "depth": False})
    s3, d3, _, _ = call("/robots/camera", {**CAR, "quality": "low", "depth": "yes"})
    check("POST /robots/camera low sets 424x240x6; bad quality and bad depth are 400",
          s == 200 and cam.get("profile") == "424x240x6" and cam.get("applied") is True and last[-1:] == ["424x240x6"]
          and s2 == 400 and d2.get("code") == "bad_quality" and s3 == 400 and d3.get("code") == "bad_depth",
          f"HTTP {s} {cam} last={last} bad_quality={s2}/{d2.get('code')} bad_depth={s3}/{d3.get('code')}")
    call("/robots/camera", {**CAR, "quality": "medium", "depth": False})


def t_camera_reapplied_on_reconnect():
    call("/robots/connect", CAR)
    call("/robots/camera", {**CAR, "quality": "high", "depth": False})
    call("/robots/disconnect", CAR)
    reset()
    s, d, _, _ = call("/robots/connect", CAR)
    got = [(p["name"], p["value"]) for p in param_log(CAR) if p["successful"]]
    check("reconnect re-applies the last chosen camera quality",
          s == 200 and ("enable_depth", False) in got and ("rgb_camera.color_profile", "640x480x15") in got
          and (d.get("camera") or {}).get("quality") == "high",
          f"HTTP {s} camera={d.get('camera')} logged={got}")
    call("/robots/camera", {**CAR, "quality": "medium", "depth": False})


def t_camera_refused_blocks_stream():
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}&topic={IMAGE_TOPIC}"
    restore = set_mock_robot("192.168.1.20:42", param_runtime=False)
    try:
        s, d, _, _ = call("/robots/connect", CAR)
        cam = d.get("camera") or {}
        blocked = Stream(f"/robots/camera.mjpg?{q}")
        status, err = blocked.status, blocked.error or {}
        blocked.close()
        forced = Stream(f"/robots/camera.mjpg?{q}&force_full=1")
        time.sleep(1.0)
        frames = len(forced.parts)
        forced.close()
    finally:
        restore()
    check("a camera that refuses tuning is reported, blocks MJPEG with 409, and force_full=1 streams",
          s == 200 and cam.get("applied") is False and bool(cam.get("reason")) and status == 409
          and err.get("code") == "camera_untuned" and frames >= 3,
          f"connect HTTP {s} camera={cam} mjpeg={status} {err} forced frames={frames}")
    call("/robots/connect", CAR)  # back to a tunable camera for the checks that follow


def t_camera_appearing_after_connect_is_tuned_on_stream():
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}&topic={IMAGE_TOPIC}"
    restore = set_mock_robot("192.168.1.20:42", params={})
    try:
        s, d, _, _ = call("/robots/connect", CAR)
        early = Stream(f"/robots/camera.mjpg?{q}")  # no node answered yet, but the car publishes a RealSense topic
        early_status = early.status
        early.close()
    finally:
        restore()
    cam0 = d.get("camera") or {}
    unresolved_at_connect = s == 200 and cam0.get("applied") is False and cam0.get("node") is None and early_status == 409
    reset()
    restore = set_mock_robot("192.168.1.20:42", param_runtime=False)  # the camera node shows up, but refuses
    try:
        time.sleep(5.3)  # the lazy re-try is limited to once per 5 s
        blocked = Stream(f"/robots/camera.mjpg?{q}")
        status, err = blocked.status, blocked.error or {}
        blocked.close()
    finally:
        restore()
    time.sleep(5.3)
    reset()
    ok_stream = Stream(f"/robots/camera.mjpg?{q}")  # still gated: the last attempt was a refusal, not a miss
    gated_again = ok_stream.status
    ok_stream.close()
    check("a RealSense topic with no camera node yet is gated (fail closed); the node is tuned on a later MJPEG request, and the gate applies if refused",
          unresolved_at_connect and status == 409 and err.get("code") == "camera_untuned" and gated_again == 409,
          f"camera at connect={d.get('camera')!r} mjpeg then={early_status} first mjpeg={status} {err.get('code')} later={gated_again}")
    call("/robots/disconnect", CAR)  # a stale refusal must not outlive the worker
    reset()
    time.sleep(5.3)
    restore = set_mock_robot("192.168.1.20:42", params={})
    try:
        call("/robots/connect", CAR)
    finally:
        restore()
    time.sleep(5.3)
    reset()
    ok = Stream(f"/robots/camera.mjpg?{q}")
    time.sleep(1.0)
    frames = len(ok.parts)
    ok.close()
    got = [(p["name"], p["value"]) for p in param_log(CAR) if p["successful"]]
    check("a camera node that appears after connect and accepts tuning is tuned and then streams",
          ok.status == 200 and frames >= 3 and ("rgb_camera.color_profile", "424x240x15") in got,
          f"mjpeg={ok.status} frames={frames} logged={got}")
    call("/robots/connect", CAR)


def t_camera_lazy_retry_is_bounded():
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}&topic={IMAGE_TOPIC}"
    restore = set_mock_robot("192.168.1.20:42", param_silent=True)  # a camera node that never answers
    try:
        s, d, dt_connect, _ = call("/robots/connect", CAR)
        time.sleep(5.3)  # the lazy re-try is limited to once per 5 s
        t0 = time.monotonic()
        first = Stream(f"/robots/camera.mjpg?{q}")
        took = time.monotonic() - t0
        status = first.status
        first.close()
        results_ = []

        def concurrent():
            time.sleep(5.3)
            t = time.monotonic()
            st = Stream(f"/robots/camera.mjpg?{q}")
            results_.append((st.status, time.monotonic() - t))
            st.close()

        threads = [threading.Thread(target=concurrent) for _ in range(3)]
        [th.start() for th in threads]
        [th.join() for th in threads]
        forced = Stream(f"/robots/camera.mjpg?{q}&force_full=1")
        time.sleep(1.0)
        forced_frames = len(forced.parts)
        forced.close()
    finally:
        restore()
    cam = d.get("camera") or {}
    check("a camera node that never answers fails closed: MJPEG is gated (409) within ~3 s, concurrent requests share one retry, force_full still streams",
          s == 200 and cam.get("applied") is False and cam.get("node") is None and status == 409 and took < 3.5
          and all(st == 409 and t < 3.5 for st, t in results_) and len(results_) == 3 and forced_frames >= 3,
          f"connect HTTP {s} in {dt_connect:.1f}s camera={cam}; first mjpeg {status} in {took:.2f}s; concurrent={results_}; forced frames={forced_frames}")
    call("/robots/connect", CAR)


def t_camera_readback_mismatch_blocks_stream():
    """R30: a camera that answers success but keeps its old profile is not 'applied', and the gate applies."""
    q = f"ip={CAR['ip']}&domain_id={CAR['domain_id']}&topic={IMAGE_TOPIC}"
    restore = set_mock_robot("192.168.1.20:42", param_revert=True)
    try:
        s, d, _, _ = call("/robots/connect", CAR)
        blocked = Stream(f"/robots/camera.mjpg?{q}")
        status, err = blocked.status, blocked.error or {}
        blocked.close()
    finally:
        restore()
    cam = d.get("camera") or {}
    check("a camera whose read-back does not match the request is reported unapplied and gates MJPEG",
          s == 200 and cam.get("applied") is False and cam.get("node") == "/camera/camera" and "640x480x30" in str(cam.get("reason"))
          and status == 409 and err.get("code") == "camera_untuned",
          f"connect HTTP {s} camera={cam} mjpeg={status} {err.get('code')}")
    call("/robots/connect", CAR)


if __name__ == "__main__":
    only = set(sys.argv[3:])
    for fn in [t_health, t_connect_real, t_connect_off, t_connect_nodriver, t_no_subscriber_fast, t_order_stop_wins,
               t_hold_and_deadman, t_stream_rate_and_latency, t_stale_command_dropped, t_channels_independent, t_release_stops, t_gripper, t_command_text, t_cors, t_bad_input,
               t_power_scales_twist, t_keep_last_one, t_stop_burst, t_stop_confirmed, t_stop_unreported,
               t_gripper_trigger, t_no_gripper, t_other_publishers, t_rmw_pinned,
               t_stop_ignores_bad_power, t_bool_gripper_has_no_position, t_position_gripper_first_command, t_tap_not_falsely_confirmed,
               t_sse_scan, t_mjpeg_frames, t_unwatch_on_disconnect, t_unwatch_on_instant_disconnect, t_stale_on_drop, t_no_cross_talk_streams,
               t_drive_tick_under_load, t_sse_rejects_image, t_stream_route_errors, t_sse_mixed_topics,
               t_sse_link_only, t_sse_many_topics, t_link_seq_and_route,
               t_sse_image_stats_while_mjpeg_open,
               t_camera_tuned_on_connect, t_camera_quality_change, t_camera_reapplied_on_reconnect, t_camera_refused_blocks_stream,
               t_camera_appearing_after_connect_is_tuned_on_stream, t_camera_lazy_retry_is_bounded, t_camera_readback_mismatch_blocks_stream]:
        if only and fn.__name__ not in only:
            continue
        try:
            fn()
        except Exception as e:  # a crash is a failure with its reason
            check(fn.__name__, False, f"raised {type(e).__name__}: {e}")
    print(f"\n{sum(results)}/{len(results)} passed")
