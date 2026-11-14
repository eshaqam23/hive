"""Worker side of live data: on-demand raw subscriptions to any topic.

The ROS callback runs on the worker's drive thread, so it only stores the newest raw bytes and updates
two rate averages. Deserialising, adapting and JPEG encoding happen on a converter thread, at a capped
rate per kind, and the result goes out through `emit` (one JSON line on the worker's stdout)."""
import base64
import importlib
import threading
import time
from typing import Any, Callable

from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.serialization import deserialize_message as deserialize

import adapters

RELIABLE_TOPICS = {"/ros_data", "/esp_data"}  # the robot publishes these reliably: match it
EMA = 0.2
TICK = 0.01


class _Entry:
    def __init__(self, topic: str, type_name: str, kind: str, cls: Any) -> None:
        self.topic, self.type, self.kind, self.cls = topic, type_name, kind, cls
        self.sub: Any = None
        self.raw: tuple[bytes, float, float] | None = None  # (bytes, wall time, EMA hz at arrival) not yet converted
        self.hz = 0.0
        self.bps = 0.0
        self.last_cb: float | None = None
        self.last_emit = 0.0
        self.active = True


class SensorWatcher:
    def __init__(self, node: Any, emit: Callable[[dict], None], caps: dict[str, float]) -> None:
        self.node, self.emit, self.caps = node, emit, caps
        self.callbacks = 0  # raw messages received; the worker's drain loop watches it
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._convert_loop, name="sensor-converter", daemon=True)
        self._thread.start()

    # -- worker thread
    def watch(self, topic: str) -> dict:
        with self._lock:
            entry = self._entries.get(topic)
        if entry is None:
            types = dict(self.node.get_topic_names_and_types()).get(topic) if isinstance(topic, str) else None
            if not types:
                raise KeyError(topic)
            type_name = types[0]
            package, _, name = type_name.split("/")
            try:
                cls = getattr(importlib.import_module(f"{package}.msg"), name)
            except (ImportError, AttributeError):
                raise KeyError(topic) from None
            entry = _Entry(topic, type_name, adapters.kind_for_type(type_name), cls)
            qos = (QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST)
                   if topic in RELIABLE_TOPICS else qos_profile_sensor_data)
            entry.sub = self.node.create_subscription(cls, topic, lambda data, e=entry: self._on_raw(e, data), qos, raw=True)
            with self._lock:
                self._entries[topic] = entry
        return {"topic": topic, "type": entry.type, "kind": entry.kind}

    def unwatch(self, topic: str) -> None:
        with self._lock:
            entry = self._entries.pop(topic, None)
        if entry is not None:
            entry.active = False
            self.node.destroy_subscription(entry.sub)

    def watched(self) -> set[str]:
        with self._lock:
            return set(self._entries)

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=2)
        for topic in list(self.watched()):
            self.unwatch(topic)

    def _on_raw(self, entry: _Entry, data: bytes) -> None:
        now = time.monotonic()
        if entry.last_cb is not None and now > entry.last_cb:
            dt = now - entry.last_cb
            entry.hz = (1 / dt) if entry.hz == 0 else entry.hz * (1 - EMA) + EMA / dt
            entry.bps = (len(data) / dt) if entry.bps == 0 else entry.bps * (1 - EMA) + EMA * len(data) / dt
        entry.last_cb = now
        entry.raw = (data, time.time(), 0.0)
        self.callbacks += 1

    # -- converter thread
    def _convert_loop(self) -> None:
        while not self._stopped.wait(TICK):
            with self._lock:
                entries = list(self._entries.values())
            now = time.monotonic()
            for entry in entries:
                raw = entry.raw
                cap = self.caps.get(entry.kind, 5.0)
                if raw is None or not entry.active or now - entry.last_emit < 1.0 / cap:
                    continue
                entry.raw = None
                entry.last_emit = now
                try:
                    self.emit({"stream": self._payload(entry, raw[0], raw[1])})
                except Exception:  # noqa: BLE001 - a failed write or conversion must not end the thread
                    pass

    def _payload(self, entry: _Entry, data: bytes, stamp: float) -> dict:
        try:
            msg = deserialize(data, entry.cls)
            body = adapters.adapt(entry.type, msg)
            frame_id = str(getattr(getattr(msg, "header", None), "frame_id", "") or "")
        except Exception as exc:  # noqa: BLE001
            body, frame_id = {"kind": "generic", "data": {}, "note": f"{type(exc).__name__}: {exc}"}, ""
        payload = {"topic": entry.topic, "type": entry.type, "hz": round(entry.hz, 2),
                   "bytes_per_s": int(entry.bps), "stamp": stamp, "frame_id": frame_id, **body}
        jpeg = payload.pop("jpeg", None)
        if jpeg is not None:
            payload["jpeg_b64"] = base64.b64encode(jpeg).decode("ascii")
        return payload
