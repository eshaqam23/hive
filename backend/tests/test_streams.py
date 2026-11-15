"""StreamHub fan-out, SSE and MJPEG framing, and the two serving loops (backend side of live data)."""
import base64
import json
import threading
import time

import pytest

import streams

KEY = ("10.0.0.1", 7)


def hub_with_log():
    log = []
    hub = streams.StreamHub(on_first=lambda k, t: log.append(("first", k, t)),
                            on_last=lambda k, t: log.append(("last", k, t)))
    return hub, log


def image(topic, data):
    return {"topic": topic, "kind": "image", "jpeg_b64": base64.b64encode(data).decode()}


def test_on_first_once_and_on_last_once():
    hub, log = hub_with_log()
    a, b = hub.subscribe(KEY, "/scan"), hub.subscribe(KEY, "/scan")
    assert log == [("first", KEY, "/scan")]
    assert hub.viewer_count(KEY, "/scan") == 2
    hub.unsubscribe(a)
    assert log == [("first", KEY, "/scan")]
    hub.unsubscribe(b)
    assert log == [("first", KEY, "/scan"), ("last", KEY, "/scan")]
    assert hub.viewer_count(KEY, "/scan") == 0
    hub.unsubscribe(b)  # a second unsubscribe is harmless
    assert len(log) == 2


def test_queue_overflow_keeps_newest_30():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, "/scan")
    for i in range(40):
        hub.publish(KEY, {"topic": "/scan", "kind": "scan", "n": i})
    got = []
    while (e := v.get(0)) is not None:
        got.append(e["data"]["n"])
    assert got == list(range(10, 40))


def test_publish_routes_by_key_and_topic():
    hub, _ = hub_with_log()
    a, b = hub.subscribe(KEY, "/scan"), hub.subscribe(("10.0.0.2", 7), "/scan")
    c = hub.subscribe(KEY, ["/x", "/scan"])
    hub.publish(KEY, {"topic": "/scan", "kind": "scan", "n": 1})
    assert a.get(0)["event"] == "data"
    assert b.get(0) is None
    assert c.get(0)["data"]["n"] == 1
    hub.publish(KEY, {"topic": "/nobody", "kind": "value"})  # no viewers: dropped silently
    assert a.get(0) is None


def test_link_reaches_each_viewer_of_the_key_once():
    hub, _ = hub_with_log()
    multi = hub.subscribe(KEY, ["/a", "/b"])
    other = hub.subscribe(("10.0.0.2", 7), "/a")
    hub.publish_link(KEY, {"stop": "confirmed"})
    assert multi.get(0) == {"event": "link", "data": {"stop": "confirmed"}}
    assert multi.get(0) is None
    assert other.get(0) is None


def test_get_times_out_and_wakes_on_publish():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, "/a")
    t0 = time.monotonic()
    assert v.get(0.1) is None and time.monotonic() - t0 >= 0.09
    threading.Timer(0.05, lambda: hub.publish(KEY, {"topic": "/a", "kind": "value"})).start()
    assert v.get(1.0)["event"] == "data"


def test_images_store_decoded_jpeg_and_skip_viewers():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, "/cam")
    assert hub.latest_jpeg(KEY, "/cam") is None
    hub.publish(KEY, image("/cam", b"JPEG1"))
    hub.publish(KEY, image("/cam", b"JPEG2"))
    frame, seq = hub.latest_jpeg(KEY, "/cam")
    assert frame == b"JPEG2" and seq == 2
    assert v.get(0) is None  # image payloads never go to the viewer queue


def test_bad_base64_is_dropped():
    hub, _ = hub_with_log()
    hub.subscribe(KEY, "/cam")
    hub.publish(KEY, {"topic": "/cam", "kind": "image", "jpeg_b64": "!!notbase64"})
    hub.publish(KEY, {"topic": "/cam", "kind": "image"})
    assert hub.latest_jpeg(KEY, "/cam") is None


def test_wait_jpeg_returns_newer_seq():
    hub, _ = hub_with_log()
    hub.subscribe(KEY, "/cam")
    hub.publish(KEY, image("/cam", b"A"))
    assert hub.wait_jpeg(KEY, "/cam", 0, 0.1) == (b"A", 1)
    assert hub.wait_jpeg(KEY, "/cam", 1, 0.05) is None
    threading.Timer(0.05, lambda: hub.publish(KEY, image("/cam", b"B"))).start()
    assert hub.wait_jpeg(KEY, "/cam", 1, 1.0) == (b"B", 2)


def test_last_viewer_leaving_clears_the_frame_but_seq_keeps_growing():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, "/cam")
    hub.publish(KEY, image("/cam", b"A"))
    hub.unsubscribe(v)
    assert hub.latest_jpeg(KEY, "/cam") is None
    hub.subscribe(KEY, "/cam")
    hub.publish(KEY, image("/cam", b"B"))
    assert hub.latest_jpeg(KEY, "/cam") == (b"B", 2)


def test_on_first_failure_rolls_back_and_raises():
    log = []

    def first(key, topic):
        if topic == "/bad":
            raise RuntimeError("worker said no")
        log.append(("first", topic))

    hub = streams.StreamHub(on_first=first, on_last=lambda k, t: log.append(("last", t)))
    with pytest.raises(RuntimeError):
        hub.subscribe(KEY, ["/ok", "/bad"])
    assert log == [("first", "/ok"), ("last", "/ok")]
    assert hub.viewer_count(KEY, "/ok") == 0 and hub.viewer_count(KEY, "/bad") == 0


def test_on_last_failure_is_swallowed():
    def boom(key, topic):
        raise RuntimeError("worker gone")

    hub = streams.StreamHub(on_first=lambda k, t: None, on_last=boom)
    v = hub.subscribe(KEY, "/a")
    hub.unsubscribe(v)
    assert hub.viewer_count(KEY, "/a") == 0


def test_hub_without_callbacks():
    hub = streams.StreamHub()
    hub.unsubscribe(hub.subscribe(KEY, "/a"))


def test_sse_event_bytes():
    assert streams.sse_event("data", {"a": 1}) == b'event: data\ndata: {"a": 1}\n\n'


def test_mjpeg_part_bytes():
    part = streams.mjpeg_part(b"abc")
    assert part.startswith(b"--hiveframe\r\nContent-Type: image/jpeg\r\nContent-Length: ")
    assert part.endswith(b"\r\n\r\nabc\r\n")
    assert b"Content-Length: 3\r\n" in part


# ---- serving loops -------------------------------------------------------------------------------
class Sink:
    def __init__(self, fail_after=None):
        self.chunks, self.fail_after = [], fail_after

    def __call__(self, data):
        if self.fail_after is not None and len(self.chunks) >= self.fail_after:
            raise BrokenPipeError()
        self.chunks.append(data)

    def events(self):
        out = []
        for c in self.chunks:
            if c.startswith(b"event:"):
                head, data = c.decode().split("\n")[:2]
                out.append((head[7:], json.loads(data[6:])))
        return out


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_sse_sends_data_link_stale_then_recovers():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, ["/a", "/b"])
    sink, clock, state = Sink(), Clock(), {"step": 0}

    def gone():
        state["step"] += 1
        s = state["step"]
        clock.t = s * 0.5
        if s == 2:  # /a delivers; /b never does
            hub.publish(KEY, {"topic": "/a", "kind": "value", "n": 1})
        if 12 <= s <= 22:  # from 6 s on, /a delivers every step again
            hub.publish(KEY, {"topic": "/a", "kind": "value", "n": s})
        return s > 23

    reason = streams.serve_sse(sink, hub, v, alive=lambda: True, client_gone=gone, link={"stop": "idle"}, clock=clock, poll=0)
    assert reason == "client_gone"
    events = sink.events()
    assert events[0] == ("link", {"stop": "idle"})
    stale = [(d["topic"], d["stale"]) for e, d in events if e == "status"]
    assert stale.count(("/b", True)) == 1  # sent once
    assert stale.count(("/a", True)) == 1 and stale.count(("/a", False)) == 1
    assert stale.index(("/a", True)) < stale.index(("/a", False))
    assert [d["n"] for e, d in events if e == "data"][:2] == [1, 12]
    assert any(c == b": ping\n\n" for c in sink.chunks)


def test_sse_ends_when_worker_gone_or_client_write_fails():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, "/a")
    sink = Sink()
    assert streams.serve_sse(sink, hub, v, alive=lambda: False, client_gone=lambda: False, poll=0) == "worker_gone"
    assert sink.events()[-1][0] == "status"
    hub.publish(KEY, {"topic": "/a", "kind": "value"})
    assert streams.serve_sse(Sink(fail_after=0), hub, v, alive=lambda: True, client_gone=lambda: False, poll=0) == "client_gone"


def test_mjpeg_sends_new_frames_only_and_stops():
    hub, _ = hub_with_log()
    hub.subscribe(KEY, "/cam")
    sink, state = Sink(), {"n": 0}
    hub.publish(KEY, image("/cam", b"F1"))

    def gone():
        state["n"] += 1
        if state["n"] == 2:
            hub.publish(KEY, image("/cam", b"F2"))
        return state["n"] > 4

    assert streams.serve_mjpeg(sink, hub, KEY, "/cam", alive=lambda: True, client_gone=gone, wait=0.01) == "client_gone"
    assert sink.chunks == [streams.mjpeg_part(b"F1"), streams.mjpeg_part(b"F2")]
    assert streams.serve_mjpeg(Sink(), hub, KEY, "/cam", alive=lambda: False, client_gone=lambda: False, wait=0.01) == "worker_gone"
    assert streams.serve_mjpeg(Sink(fail_after=0), hub, KEY, "/cam", alive=lambda: True, client_gone=lambda: False, wait=0.01) == "client_gone"


def test_on_last_failure_is_logged_not_silent(capsys):
    def boom(key, topic):
        raise RuntimeError("timed out")

    hub = streams.StreamHub(on_first=lambda k, t: None, on_last=boom)
    for _ in range(3):
        hub.unsubscribe(hub.subscribe(KEY, "/a"))
    out = capsys.readouterr().out
    assert out.count("RuntimeError: timed out") == 1 and "/a" in out  # logged once, then throttled


# ---- tolerant subscribe, image stats --------------------------------------------------------------
def test_subscribe_with_errors_skips_failing_topics_and_keeps_the_rest():
    def on_first(key, topic):
        if topic == "/bad":
            raise RuntimeError("no such message package")

    hub = streams.StreamHub(on_first=on_first)
    errors = []
    viewer = hub.subscribe(KEY, ["/a", "/bad", "/b"], errors=errors)
    assert viewer.topics == ("/a", "/b")
    assert [t for t, _ in errors] == ["/bad"]
    assert hub.viewer_count(KEY, "/bad") == 0


def test_sse_reports_topic_errors_first():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, ["/a"])
    sink = Sink()
    streams.serve_sse(sink, hub, v, alive=lambda: True, client_gone=lambda: True, errors=[("/x", "unknown_topic")], poll=0)
    assert sink.events()[0] == ("status", {"topic": "/x", "error": "unknown_topic"})


def sized_image(topic, n, width=640, height=480):
    return {**image(topic, b"x" * n), "width": width, "height": height}


def test_image_stats_sse_emits_rate_size_and_stale_without_frames():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, [])
    v.image_topics = ("/cam",)
    sink, clock, state = Sink(), Clock(), {"step": 0}

    def gone():
        state["step"] += 1
        s = state["step"]
        clock.t = s * 0.25
        if s <= 8:  # 4 frames a second for 2 s, then silence
            hub.publish(KEY, sized_image("/cam", 1000))
        if 22 <= s <= 26:
            hub.publish(KEY, sized_image("/cam", 1000))
        return s > 32

    streams.serve_sse(sink, hub, v, alive=lambda: True, client_gone=gone, clock=clock, poll=0)
    events = sink.events()
    stats = [d for e, d in events if e == "image_stats"]
    assert stats and stats[0]["topic"] == "/cam" and stats[0]["width"] == 640 and stats[0]["height"] == 480
    assert stats[0]["hz"] == pytest.approx(4.0, rel=0.3) and stats[0]["bytes_per_s"] > 0
    assert not any(e == "data" for e, _ in events)
    flags = [d["stale"] for e, d in events if e == "status" and "stale" in d]
    assert flags[:2] == [True, False]


def test_image_stats_counts_frames_even_without_viewers():
    hub, _ = hub_with_log()
    hub.publish(KEY, sized_image("/cam", 10))
    hub.publish(KEY, {**sized_image("/cam", 20), "bytes_per_s": 9000, "hz": 30.0})
    assert hub.image_stats(KEY, "/cam") == {"frames": 2, "bytes": 30, "width": 640, "height": 480, "bytes_per_s": 9000, "hz": 30.0}
    assert hub.image_stats(KEY, "/other") is None


# ---- link events for every viewer, raw camera rate (final audit #8, #9) ------------------------------
def test_link_reaches_a_viewer_with_no_data_topics():
    hub, log = hub_with_log()
    bare = hub.subscribe(KEY, [])
    other = hub.subscribe(("10.0.0.2", 7), [])
    hub.publish_link(KEY, {"stop": "pending", "seq": 3})
    assert bare.get(0) == {"event": "link", "data": {"stop": "pending", "seq": 3}}
    assert other.get(0) is None
    assert log == []  # nothing to watch: the worker is never asked
    hub.unsubscribe(bare)
    hub.publish_link(KEY, {"stop": "confirmed", "seq": 4})
    assert bare.get(0) is None  # a viewer that left gets nothing more


def test_link_reaches_a_viewer_whose_topics_all_failed():
    def on_first(key, topic):
        raise RuntimeError("no such message package")

    hub = streams.StreamHub(on_first=on_first)
    errors = []
    viewer = hub.subscribe(KEY, ["/a", "/b"], errors=errors)
    assert viewer.topics == () and len(errors) == 2
    hub.publish_link(KEY, {"stop": "idle", "seq": 1})
    assert viewer.get(0)["event"] == "link"


def test_a_failed_subscription_leaves_no_link_viewer_behind():
    def on_first(key, topic):
        raise RuntimeError("worker said no")

    hub = streams.StreamHub(on_first=on_first)
    with pytest.raises(RuntimeError):
        hub.subscribe(KEY, ["/a"])
    hub.publish_link(KEY, {"stop": "idle"})
    assert hub.link_viewer_count(KEY) == 0


def raw_image(topic, jpeg_size, bytes_per_s, hz, width=640, height=480):
    return {**sized_image(topic, jpeg_size, width, height), "bytes_per_s": bytes_per_s, "hz": hz}


def test_image_stats_report_the_raw_rate_from_the_car_not_the_jpeg_size():
    hub, _ = hub_with_log()
    v = hub.subscribe(KEY, [])
    v.image_topics = ("/cam",)
    sink, clock, state = Sink(), Clock(), {"step": 0}

    def gone():
        state["step"] += 1
        clock.t = state["step"] * 0.25
        hub.publish(KEY, raw_image("/cam", 1000, 4_600_000, 14.8))
        return state["step"] > 8

    streams.serve_sse(sink, hub, v, alive=lambda: True, client_gone=gone, clock=clock, poll=0)
    stats = [d for e, d in sink.events() if e == "image_stats"]
    assert stats and all(s["bytes_per_s"] == 4_600_000 and s["hz"] == 14.8 for s in stats)
    assert all(s["shown_hz"] == pytest.approx(4.0, rel=0.3) for s in stats)  # frames that reached the laptop
