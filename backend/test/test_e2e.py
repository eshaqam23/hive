"""End-to-end checks for the Hive backend against the mock ROS 2 (rclpy) in mock_ros/.

Each check prints PASS / FAIL with the evidence. The mock records every message each simulated
robot receives (robots/<ip>_<domain>.jsonl), so ordering and stop behaviour are checked on what the
"robot" actually got, not on what the API said.

    python3 -I hive_e2e.py <mock_root> [base_url]
"""
import glob, json, os, sys, threading, time, urllib.error, urllib.request

MOCK = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8787/api"
ORIGIN = "http://127.0.0.1:5173"
results = []

CAR = {"ip": "192.168.1.20", "domain_id": 42}       # online, driver running
OFF = {"ip": "192.168.1.31", "domain_id": 43}       # powered off
NODRV = {"ip": "192.168.1.44", "domain_id": 44}     # online, but nothing subscribes to /cmd_vel
LIDAR = {"ip": "192.168.1.50", "domain_id": 45}     # /cmd_vel listener, /scan, /ros_data echo
GRIP = {"ip": "192.168.1.60", "domain_id": 46}      # Trigger gripper services, prefixed /ros_data echo


def call(path, body=None, method=None, origin=ORIGIN, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", origin)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), time.time() - t0, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {"raw": raw.decode(errors="replace")}
        return e.code, payload, time.time() - t0, dict(e.headers)


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
    t_quiet = time.time()
    time.sleep(3.0)                            # client vanishes: no keepalive, no stop
    msgs = [m for m in received(CAR) if m["topic"] == "/cmd_vel"]
    moves = [m for m in msgs if moving(m["msg"])]
    after = [m for m in moves if m["t"] > t_quiet + 1.6]
    last = msgs[-1]["msg"] if msgs else None
    check("holding drive streams /cmd_vel continuously", len(moves) >= 8, f"{len(moves)} move messages while held (start HTTP {s})")
    check("deadman stops the robot when the client goes silent", bool(msgs) and not after and not moving(last),
          f"move msgs >1.6 s after silence: {len(after)}; last={last!r}")


def t_stream_rate_and_latency():
    """A connected robot's worker is already running: a press reaches the robot at once, and the
    stream runs at the worker's 10 Hz."""
    reset()
    t_press = time.time()
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
    t_rel = time.time()
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
    t0, link = time.time(), {}
    while time.time() - t0 < limit:
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
    path = f"{MOCK}/robots.json"
    original = open(path).read()
    robots = json.loads(original)
    robots["192.168.1.20:42"]["extra_cmd_vel_publishers"] = 1
    def write_atomic(text):  # workers re-read robots.json constantly: never leave it half-written
        open(path + ".tmp", "w").write(text)
        os.replace(path + ".tmp", path)

    try:
        write_atomic(json.dumps(robots))
        s, d, _, _ = call("/robots/connect", CAR)
    finally:
        write_atomic(original)
    check("connect reports another publisher on /cmd_vel", s == 200 and d.get("other_publishers") == 1 and any("publish" in w for w in d.get("warnings", [])),
          f"HTTP {s} other_publishers={d.get('other_publishers')} warnings={d.get('warnings')}")


def t_rmw_pinned():
    call("/robots/connect", CAR)
    env = json.load(open(f"{MOCK}/robots/worker_env.json"))
    check("the worker's RMW implementation is pinned", env.get("RMW_IMPLEMENTATION") == "rmw_fastrtps_cpp", f"env={env}")


if __name__ == "__main__":
    only = set(sys.argv[3:])
    for fn in [t_health, t_connect_real, t_connect_off, t_connect_nodriver, t_no_subscriber_fast, t_order_stop_wins,
               t_hold_and_deadman, t_stream_rate_and_latency, t_stale_command_dropped, t_channels_independent, t_release_stops, t_gripper, t_command_text, t_cors, t_bad_input,
               t_power_scales_twist, t_keep_last_one, t_stop_burst, t_stop_confirmed, t_stop_unreported,
               t_gripper_trigger, t_no_gripper, t_other_publishers, t_rmw_pinned]:
        if only and fn.__name__ not in only:
            continue
        try:
            fn()
        except Exception as e:  # a crash is a failure with its reason
            check(fn.__name__, False, f"raised {type(e).__name__}: {e}")
    print(f"\n{sum(results)}/{len(results)} passed")
