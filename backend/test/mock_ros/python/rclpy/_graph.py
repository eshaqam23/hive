"""Subscriptions, robot-side publishers, services and parameters of the rclpy mock.

Kept apart from rclpy/__init__.py, which holds the node, discovery and message logging. Delivery to
subscriptions happens inside spin_once: each call hands every subscription at most one message
(the newest that is due, like KeepLast(1)), scheduled from the robot's `hz` in robots.json.
"""
import copy
import hashlib
import json
import os
import time

import rclpy as _r
from rcl_interfaces.srv import ListParameters
from rclpy.serialization import serialize_message

WHEELBASE = 0.2032
MAX_SPEED = 0.5
_param_state = {}  # (robot key, node) -> {name: value} set at runtime


def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def robots_file(key, suffix):
    return os.path.join(_r.ROOT, "robots", f"{key.replace(':', '_')}.{suffix}.jsonl")


def log_worker_publisher(topic, qos):
    reliability = getattr(getattr(qos, "reliability", None), "name", "RELIABLE").lower()
    append_jsonl(os.path.join(_r.ROOT, "robots", "worker_pubs.jsonl"),
                 {"topic": topic, "depth": getattr(qos, "depth", qos), "reliability": reliability})


WORKER_ENV_KEYS = ("ROS_DOMAIN_ID", "ROS_STATIC_PEERS", "ROS_AUTOMATIC_DISCOVERY_RANGE", "RMW_IMPLEMENTATION")


def write_worker_env():
    """Record only the ROS-related environment (never the whole of os.environ: it may hold secrets)."""
    env = {k: os.environ[k] for k in WORKER_ENV_KEYS if k in os.environ}
    env["ROS_LOCALHOST_ONLY_set"] = "ROS_LOCALHOST_ONLY" in os.environ
    os.makedirs(os.path.join(_r.ROOT, "robots"), exist_ok=True)
    fd = os.open(os.path.join(_r.ROOT, "robots", "worker_env.json"), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(env, f)


def log_sub(event, topic, raw):
    domain = os.environ.get("ROS_DOMAIN_ID", "0")
    for peer in [p.strip() for p in os.environ.get("ROS_STATIC_PEERS", "").split(";") if p.strip()]:
        append_jsonl(robots_file(f"{peer}:{domain}", "subs"),
                     {"t": time.time(), "event": event, "topic": topic, "raw": raw, "pid": os.getpid()})


# ---- generated messages ---------------------------------------------------------------------
def _stamp(msg):
    now = time.time()
    msg.header.stamp.sec, msg.header.stamp.nanosec = int(now), int((now % 1) * 1e9)
    return msg


def _make(key, topic, spec):
    from sensor_msgs.msg import Image, LaserScan
    ip = key.split(":")[0]
    if spec["type"] == "sensor_msgs/msg/LaserScan":
        msg = LaserScan()
        msg.angle_min, msg.angle_max, msg.angle_increment = 0.0, 6.2832, 6.2832 / 360
        msg.range_min, msg.range_max = 0.1, 12.0
        msg.ranges = [1.0 + (i % 10) / 10 for i in range(360)]
    else:
        msg = Image()
        msg.width, msg.height = spec.get("width", 640), spec.get("height", 480)
        msg.encoding, msg.step = "rgb8", msg.width * 3
        colour = hashlib.md5(ip.encode()).digest()[:3]
        msg.data = colour * (msg.width * msg.height)
    msg.header.frame_id = ip
    return msg


_cache = {}


def _generated(key, topic, spec):
    if (key, topic) not in _cache:
        _cache[(key, topic)] = _make(key, topic, spec)
    return _stamp(copy.copy(_cache[(key, topic)]))  # shares the big data buffer, own header


class Subscription:
    def __init__(self, msg_type, topic, callback, raw):
        self.msg_type, self.topic, self.callback, self.raw = msg_type, topic, callback, raw
        self.last_delivery = None  # monotonic time of the last scheduled delivery
        self.pending = []  # event-driven messages (/ros_data echoes), delivered oldest first


def _source(topic):
    for key, robot in _r._visible():
        spec = robot.get("publishes", {}).get(topic)
        if spec:
            return key, spec
    return None, None


def _deliver(sub, msg):
    sub.callback(serialize_message(msg) if sub.raw else msg)


def deliver_all(node):
    """One spin pass. Returns how many messages were delivered."""
    now, count = time.monotonic(), 0
    for sub in list(node._subscriptions):
        if sub.pending:
            from std_msgs.msg import String
            msg = String()
            msg.data = sub.pending.pop(0)
            _deliver(sub, msg)
            count += 1
            continue
        key, spec = _source(sub.topic)
        if spec is None:
            continue
        if sub.last_delivery is None or now - sub.last_delivery >= 1.0 / spec.get("hz", 1):
            sub.last_delivery = now
            _deliver(sub, _generated(key, sub.topic, spec))
            count += 1
    return count


def spin(node, timeout_sec):
    end = time.monotonic() + (0.0 if timeout_sec is None else timeout_sec)
    while not deliver_all(node) and time.monotonic() < end:
        time.sleep(0.002)


# ---- /ros_data echo ---------------------------------------------------------------------------
def _side(v):
    pwm, direction = min(int(abs(v) / MAX_SPEED * 255), 255), 0 if v < 0 else 1
    return pwm, direction


def echo_line(robot, twist):
    lin, ang = twist.linear.x, twist.angular.z
    left, right = lin - ang * WHEELBASE / 2, lin + ang * WHEELBASE / 2
    (lp, ld), (rp, rd) = _side(left), _side(right)
    prefix = robot.get("line_prefix")
    return f"{prefix + ',' if prefix else ''}{lp},{ld},{rp},{rd}\n"


def on_publish(node, robot, topic, msg):
    if topic == "/cmd_vel" and robot.get("echo_ros_data"):
        line = echo_line(robot, msg)
        for sub in node._subscriptions:
            if sub.topic == "/ros_data":
                sub.pending.append(line)


def count_publishers(node, topic):
    count = sum(1 for p in node._publishers if p == topic)
    for _, robot in _r._visible():
        count += 1 if topic in robot.get("publishes", {}) else 0
        count += 1 if topic == "/ros_data" and robot.get("echo_ros_data") else 0
        count += robot.get("extra_cmd_vel_publishers", 0) if topic == "/cmd_vel" else 0
    return count


def robot_topics(robot):
    topics = {t: s["type"] for t, s in robot.get("publishes", {}).items()}
    if robot.get("echo_ros_data"):
        topics["/ros_data"] = "std_msgs/msg/String"
    return topics


# ---- services and parameters ------------------------------------------------------------------
def robot_services(robot):
    services = dict(robot.get("services", {}))
    for node in robot.get("params", {}):
        services[f"{node}/set_parameters"] = "rcl_interfaces/srv/SetParameters"
        services[f"{node}/get_parameters"] = "rcl_interfaces/srv/GetParameters"
        services[f"{node}/list_parameters"] = "rcl_interfaces/srv/ListParameters"
    return services


def _list_parameters(robot, name, req):
    node = name[: -len("/list_parameters")]
    names = [n for n in robot.get("params", {}).get(node, {})
             if not req.prefixes or any(n == p or n.startswith(p + ".") for p in req.prefixes)]
    resp = ListParameters.Response()
    resp.result.names = names
    return resp


def service_names_and_types():
    services = {}
    for _, robot in _r._visible():
        for name, typ in robot_services(robot).items():
            services.setdefault(name, [typ])
    return sorted(services.items())


def _set_parameters(key, robot, name, req):
    from rcl_interfaces.msg import SetParametersResult
    node = name[: -len("/set_parameters")]
    declared = robot.get("params", {}).get(node, {})
    results = []
    for p in req.parameters:
        if not robot.get("param_runtime", False):
            ok, reason = False, "parameter cannot be set while the node is running"
        elif p.name not in declared:
            ok, reason = False, "parameter not declared"
        else:
            ok, reason = True, ""
            _param_state.setdefault((key, node), {})[p.name] = p.value.python()
        append_jsonl(robots_file(key, "params"), {"t": time.time(), "node": node, "name": p.name,
                                                  "value": p.value.python(), "successful": ok, "pid": os.getpid()})
        results.append(SetParametersResult(ok, reason))
    return results


def _handle(srv_type, key, robot, name, req):
    from rcl_interfaces.msg import ParameterValue
    resp = srv_type.Response()
    if srv_type._type == "rcl_interfaces/srv/ListParameters":
        return _list_parameters(robot, name, req)
    if srv_type._type == "rcl_interfaces/srv/SetParameters":
        resp.results = _set_parameters(key, robot, name, req)
    elif srv_type._type == "rcl_interfaces/srv/GetParameters":
        node = name[: -len("/get_parameters")]
        values = {**robot.get("params", {}).get(node, {}), **_param_state.get((key, node), {})}
        resp.values = [ParameterValue.of(values[n]) if n in values else ParameterValue() for n in req.names]
    else:
        _r._log(key, name, "{}")
        if hasattr(resp, "success"):
            resp.success, resp.message = True, "ok"
    return resp


class Future:
    def __init__(self, result=None, done=False):
        self._result, self._done = result, done

    def done(self):
        return self._done

    def result(self):
        return self._result


class Client:
    def __init__(self, srv_type, name):
        self.srv_type, self.name = srv_type, name

    def _find(self):
        for key, robot in _r._visible():
            if self.name in robot_services(robot):
                return key, robot
        return None, None

    def service_is_ready(self):
        return self._find()[0] is not None

    def wait_for_service(self, timeout_sec=None):
        end = time.monotonic() + (30.0 if timeout_sec is None else timeout_sec)
        while not self.service_is_ready():
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)
        return True

    def call_async(self, req):
        key, robot = self._find()
        if key is None:
            return Future()  # like a real client with no server: never completes
        return Future(_handle(self.srv_type, key, robot, self.name, req), done=True)
