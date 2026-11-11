"""End-to-end checks for the Hive backend against the mock ROS 2 CLI.

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


if __name__ == "__main__":
    only = set(sys.argv[3:])
    for fn in [t_health, t_connect_real, t_connect_off, t_connect_nodriver, t_no_subscriber_fast, t_order_stop_wins,
               t_hold_and_deadman, t_release_stops, t_gripper, t_command_text, t_cors, t_bad_input]:
        if only and fn.__name__ not in only:
            continue
        try:
            fn()
        except Exception as e:  # a crash is a failure with its reason
            check(fn.__name__, False, f"raised {type(e).__name__}: {e}")
    print(f"\n{sum(results)}/{len(results)} passed")
