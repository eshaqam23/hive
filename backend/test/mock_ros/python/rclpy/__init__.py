"""A mock of the parts of rclpy (ROS 2 Jazzy) that Hive's robot worker uses.

Discovery is simulated from the environment the way DDS scopes it: a robot is visible only when
ROS_DOMAIN_ID matches and its IP is in ROS_STATIC_PEERS, and only once a discovery delay has passed
since the node was created. Robots are defined in $HIVE_MOCK_ROOT/robots.json, re-read on every call
so a test can switch one off mid-session. Every message a robot receives is appended to
$HIVE_MOCK_ROOT/robots/<ip>_<domain>.jsonl with a timestamp, so tests can check order and timing.
"""
import json
import os
import random
import time

ROOT = os.environ["HIVE_MOCK_ROOT"]
DISCOVERY_DELAY = float(os.environ.get("HIVE_MOCK_DISCOVERY", "0.8"))  # DDS discovery takes ~1 s
try:  # optional jitter (seconds), read from a file so a running backend picks it up
    DISCOVERY_DELAY += random.uniform(0, float(open(os.path.join(ROOT, "jitter")).read()))
except (OSError, ValueError):
    pass

_created_at = None


def _robots():
    try:
        with open(os.path.join(ROOT, "robots.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _visible():
    if _created_at is None or time.monotonic() - _created_at < DISCOVERY_DELAY:
        return []
    domain = os.environ.get("ROS_DOMAIN_ID", "0")
    peers = [p.strip() for p in os.environ.get("ROS_STATIC_PEERS", "").split(";") if p.strip()]
    return [
        (key, robot)
        for key, robot in _robots().items()
        if robot.get("online", True) and key.split(":")[1] == domain and key.split(":")[0] in peers
    ]


def _log(key, topic, msg):
    with open(os.path.join(ROOT, "robots", f"{key.replace(':', '_')}.jsonl"), "a") as f:
        f.write(json.dumps({"t": time.time(), "topic": topic, "msg": msg, "pid": os.getpid()}) + "\n")


def _split(full_name):
    namespace, _, name = full_name.rpartition("/")
    return name, namespace or "/"


class TopicEndpointInfo:
    def __init__(self, node_name, node_namespace):
        self.node_name, self.node_namespace = node_name, node_namespace


class Publisher:
    def __init__(self, msg_type, topic):
        self.msg_type, self.topic = msg_type, topic

    def publish(self, msg):
        for key, robot in _visible():
            if robot.get("subscribers", {}).get(self.topic, 0):
                _log(key, self.topic, msg._yaml())


class Node:
    def __init__(self, name):
        self._name = name
        self._publishers = {}

    def get_name(self):
        return self._name

    def get_namespace(self):
        return "/"

    def create_publisher(self, msg_type, topic, qos):
        self._publishers[topic] = Publisher(msg_type, topic)
        return self._publishers[topic]

    def _own_topics(self):
        topics = {t: [p.msg_type._type] for t, p in self._publishers.items()}
        topics.update({"/rosout": ["rcl_interfaces/msg/Log"], "/parameter_events": ["rcl_interfaces/msg/ParameterEvent"]})
        return topics

    def get_node_names_and_namespaces(self):
        names = [(self._name, "/")]
        for _, robot in _visible():
            names += [_split(n) for n in robot.get("nodes", [])]
        return names

    def get_topic_names_and_types(self):
        topics = self._own_topics()
        for _, robot in _visible():
            for name, typ in robot.get("topics", {}).items():
                topics.setdefault(name, [typ])
        return sorted(topics.items())

    def _robot_endpoints(self, topic, subscribers):
        endpoints = []
        for _, robot in _visible():
            if topic not in robot.get("topics", {}):
                continue
            listens = robot.get("subscribers", {}).get(topic, 0) > 0
            if listens == subscribers:
                endpoints.append(TopicEndpointInfo(*_split(robot.get("nodes", ["/robot"])[0])))
        return endpoints

    def get_publishers_info_by_topic(self, topic):
        own = [TopicEndpointInfo(self._name, "/")] if topic in self._own_topics() else []
        return own + self._robot_endpoints(topic, subscribers=False)

    def get_subscriptions_info_by_topic(self, topic):
        return self._robot_endpoints(topic, subscribers=True)

    def count_subscribers(self, topic):
        return sum(robot.get("subscribers", {}).get(topic, 0) for _, robot in _visible())

    def destroy_node(self):
        pass


def init(args=None, signal_handler_options=None):
    pass


def create_node(name):
    global _created_at
    _created_at = time.monotonic()
    return Node(name)


def spin_once(node, timeout_sec=None):
    pass


def shutdown():
    pass
