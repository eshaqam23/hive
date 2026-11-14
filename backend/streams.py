"""Backend side of live data: a fan-out hub from worker payloads to browser viewers, SSE and MJPEG framing,
and the loops that serve one SSE or MJPEG connection. Stdlib only. Nothing here blocks the worker reader."""
import base64
import binascii
import collections
import json
import threading
import time
from typing import Any, Callable, Iterable

from outputs import ThrottledLog

BOUNDARY = "hiveframe"
QUEUE_MAX = 30
STALE_SECONDS = 2.0
PING_SECONDS = 10.0
IMAGE_STATS_SECONDS = 0.5


def sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def mjpeg_part(jpeg: bytes) -> bytes:
    return f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode() + jpeg + b"\r\n"


class Viewer:
    """One browser connection: a bounded queue (oldest dropped) of {"event", "data"} items."""

    def __init__(self, key: tuple, topics: tuple[str, ...]) -> None:
        self.key, self.topics = key, topics
        self.image_topics: tuple[str, ...] = ()  # image topics this page shows: stats and stale only, never frames
        self._queue: collections.deque = collections.deque(maxlen=QUEUE_MAX)
        self._cond = threading.Condition()

    def push(self, item: dict) -> None:
        with self._cond:
            self._queue.append(item)
            self._cond.notify()

    def get(self, timeout: float) -> dict | None:
        with self._cond:
            if not self._queue and timeout > 0:
                self._cond.wait(timeout)
            return self._queue.popleft() if self._queue else None


class StreamHub:
    def __init__(self, on_first: Callable[[tuple, str], Any] | None = None,
                 on_last: Callable[[tuple, str], Any] | None = None) -> None:
        self.on_first, self.on_last = on_first, on_last
        self.log = ThrottledLog(lambda text: print(f"[hive-backend] stream: {text}", flush=True))
        self._ops = threading.Lock()       # serialises subscribe/unsubscribe, which call the worker
        self._cond = threading.Condition()  # guards the data below, and wakes MJPEG waiters
        self._viewers: dict[tuple, list[Viewer]] = {}  # (key, topic) -> viewers
        self._jpeg: dict[tuple, tuple[bytes, int]] = {}
        self._seq: dict[tuple, int] = {}
        self._img: dict[tuple, tuple[int, int, int, int]] = {}  # (key, topic) -> frames, bytes, width, height

    # -- viewers
    def subscribe(self, key: tuple, topics: str | Iterable[str], errors: list | None = None) -> Viewer:
        """With errors a list, a topic whose watch fails is appended there as (topic, error) and skipped
        instead of failing the whole subscription."""
        wanted = (topics,) if isinstance(topics, str) else tuple(dict.fromkeys(topics))
        viewer = Viewer(key, wanted)
        with self._ops:
            started: list[str] = []
            try:
                for topic in wanted:
                    with self._cond:
                        first = not self._viewers.get((key, topic))
                    if first and self.on_first:
                        try:
                            self.on_first(key, topic)
                        except Exception as error:  # noqa: BLE001
                            if errors is None:
                                raise
                            errors.append((topic, error))
                            continue
                    started.append(topic)
                    with self._cond:
                        self._viewers.setdefault((key, topic), []).append(viewer)
            except Exception:
                for topic in started:
                    self._remove(viewer, key, topic)
                raise
            viewer.topics = tuple(started)
        return viewer

    def unsubscribe(self, viewer: Viewer) -> None:
        with self._ops:
            for topic in viewer.topics:
                self._remove(viewer, viewer.key, topic)

    def _remove(self, viewer: Viewer, key: tuple, topic: str) -> None:
        with self._cond:
            viewers = self._viewers.get((key, topic), [])
            if viewer not in viewers:
                return
            viewers.remove(viewer)
            last = not viewers
            if last:
                self._viewers.pop((key, topic), None)
                self._jpeg.pop((key, topic), None)  # a later viewer must not see this old frame
                self._cond.notify_all()
        if last and self.on_last:
            try:
                self.on_last(key, topic)
            except Exception as error:  # noqa: BLE001 - the worker may already be gone; leave a trace anyway
                self.log.log(f"unwatch {key[0]}:{key[1]} {topic}", error)

    def viewer_count(self, key: tuple, topic: str) -> int:
        with self._cond:
            return len(self._viewers.get((key, topic), []))

    # -- publishing
    def publish(self, key: tuple, payload: dict) -> None:
        topic = payload.get("topic")
        if payload.get("kind") == "image":
            try:
                jpeg = base64.b64decode(payload["jpeg_b64"], validate=True)
            except (KeyError, TypeError, ValueError, binascii.Error):
                return
            with self._cond:
                seq = self._seq.get((key, topic), 0) + 1
                self._seq[(key, topic)] = seq
                frames, size, _, _ = self._img.get((key, topic), (0, 0, 0, 0))
                self._img[(key, topic)] = (frames + 1, size + len(jpeg), int(payload.get("width") or 0), int(payload.get("height") or 0))
                if self._viewers.get((key, topic)):
                    self._jpeg[(key, topic)] = (jpeg, seq)
                self._cond.notify_all()
            return
        with self._cond:
            viewers = list(self._viewers.get((key, topic), []))
        for viewer in viewers:
            viewer.push({"event": "data", "data": payload})

    def publish_link(self, key: tuple, link: dict) -> None:
        with self._cond:
            seen = {id(v): v for (k, _), vs in self._viewers.items() if k == key for v in vs}
        for viewer in seen.values():
            viewer.push({"event": "link", "data": link})

    def image_stats(self, key: tuple, topic: str) -> tuple[int, int, int, int] | None:
        """Running (frames, bytes, width, height) of an image topic; callers diff two readings for a rate."""
        with self._cond:
            return self._img.get((key, topic))

    # -- camera frames
    def latest_jpeg(self, key: tuple, topic: str) -> tuple[bytes, int] | None:
        with self._cond:
            return self._jpeg.get((key, topic))

    def wait_jpeg(self, key: tuple, topic: str, after_seq: int, timeout: float) -> tuple[bytes, int] | None:
        end = time.monotonic() + timeout
        with self._cond:
            while True:
                frame = self._jpeg.get((key, topic))
                if frame and frame[1] > after_seq:
                    return frame
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)


# ------------------------------------------------------------------ serving one connection
def serve_sse(write: Callable[[bytes], Any], hub: StreamHub, viewer: Viewer, *, alive: Callable[[], bool],
              client_gone: Callable[[], bool], link: dict | None = None, clock: Callable[[], float] = time.monotonic,
              poll: float = 0.25, errors: Iterable[tuple[str, str]] = ()) -> str:
    """Write SSE events until the client or the worker is gone. Returns why it ended."""
    try:
        if link:
            write(sse_event("link", link))
        for topic, code in errors:
            write(sse_event("status", {"topic": topic, "error": code}))
        start = clock()
        images = {}
        for topic in viewer.image_topics:
            reading = hub.image_stats(viewer.key, topic) or (0, 0, 0, 0)
            images[topic] = {"frames": reading[0], "bytes": reading[1], "emit": start, "change": start, "stale": False}
        last = {t: start for t in viewer.topics}
        stale = {t: False for t in viewer.topics}
        last_ping = start
        while True:
            if not alive():
                write(sse_event("status", {"worker_gone": True}))
                return "worker_gone"
            if client_gone():
                return "client_gone"
            item = viewer.get(poll)
            now = clock()
            if item:
                write(sse_event(item["event"], item["data"]))
                topic = item["data"].get("topic") if item["event"] == "data" else None
                if topic in last:
                    last[topic] = now
                    if stale[topic]:
                        stale[topic] = False
                        write(sse_event("status", {"topic": topic, "stale": False}))
            for topic in viewer.topics:
                if not stale[topic] and now - last[topic] > STALE_SECONDS:
                    stale[topic] = True
                    write(sse_event("status", {"topic": topic, "stale": True}))
            for topic, st in images.items():
                reading = hub.image_stats(viewer.key, topic) or (st["frames"], st["bytes"], 0, 0)
                moved = reading[0] != st["frames"]
                if moved:
                    st["change"] = now
                    if st["stale"]:
                        st["stale"] = False
                        write(sse_event("status", {"topic": topic, "stale": False}))
                if now - st["emit"] >= IMAGE_STATS_SECONDS:
                    span = now - st["emit"]
                    if moved:
                        write(sse_event("image_stats", {
                            "topic": topic, "hz": round((reading[0] - st["frames"]) / span, 2),
                            "bytes_per_s": int((reading[1] - st["bytes"]) / span), "width": reading[2], "height": reading[3]}))
                    st["frames"], st["bytes"], st["emit"] = reading[0], reading[1], now
                if not st["stale"] and now - st["change"] > STALE_SECONDS:
                    st["stale"] = True
                    write(sse_event("status", {"topic": topic, "stale": True}))
            if now - last_ping >= PING_SECONDS:
                last_ping = now
                write(b": ping\n\n")
    except OSError:  # BrokenPipeError, ConnectionResetError: the browser left
        return "client_gone"


def serve_mjpeg(write: Callable[[bytes], Any], hub: StreamHub, key: tuple, topic: str, *,
                alive: Callable[[], bool], client_gone: Callable[[], bool], wait: float = 0.5) -> str:
    seq = 0
    try:
        while True:
            if not alive():
                return "worker_gone"
            if client_gone():
                return "client_gone"
            frame = hub.wait_jpeg(key, topic, seq, wait)
            if frame:
                seq = frame[1]
                write(mjpeg_part(frame[0]))
    except OSError:
        return "client_gone"
