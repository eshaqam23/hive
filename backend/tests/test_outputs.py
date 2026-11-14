"""Non-blocking worker output and throttled logging."""
import json
import threading
import time

import outputs


def wait_for(pred, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_control_lines_go_before_stream_lines():
    gate, got = threading.Event(), []

    def sink(line):
        gate.wait()
        got.append(json.loads(line))

    w = outputs.LineWriter(sink=sink)
    w.send({"first": 1})           # taken by the writer, which then waits on the gate
    wait_for(lambda: w._busy)
    w.send_stream({"stream": 1})
    w.send({"reply": 2})
    gate.set()
    w.flush(1)
    assert got == [{"first": 1}, {"reply": 2}, {"stream": 1}]


def test_stream_queue_drops_oldest_and_never_blocks_when_sink_is_stuck():
    stuck = threading.Event()
    w = outputs.LineWriter(sink=lambda line: stuck.wait(), stream_max=3)
    t0 = time.monotonic()
    for i in range(50):
        w.send_stream({"n": i})
        w.send({"id": i})
    assert time.monotonic() - t0 < 0.5  # 100 sends against a sink that never returns
    assert w.dropped >= 40
    assert list(w._stream) == [json.dumps({"n": i}) + "\n" for i in (47, 48, 49)]
    stuck.set()


def test_sink_errors_do_not_kill_the_writer():
    got = []

    def sink(line):
        if not got:
            got.append("failed")
            raise BrokenPipeError()
        got.append(line)

    w = outputs.LineWriter(sink=sink)
    w.send({"a": 1})
    w.send({"b": 2})
    w.flush(1)
    assert got[0] == "failed" and len(got) >= 2


def test_default_sink_writes_stdout(capsys):
    w = outputs.LineWriter()
    w.send({"ready": True})
    w.flush(1)
    assert json.loads(capsys.readouterr().out) == {"ready": True}


def test_flush_gives_up_after_timeout():
    w = outputs.LineWriter(sink=lambda line: time.sleep(5))
    w.send({"x": 1})
    t0 = time.monotonic()
    w.flush(0.1)
    assert time.monotonic() - t0 < 0.5


def test_throttled_log_once_per_key_and_type_then_interval():
    t, lines = [0.0], []
    log = outputs.ThrottledLog(lines.append, interval=5.0, clock=lambda: t[0])
    for _ in range(10):
        log.log("k", OSError("a"))
    assert len(lines) == 1
    log.log("k", ValueError("b"))     # a different error type is logged at once
    log.log("other", OSError("c"))    # so is a different key
    assert len(lines) == 3
    t[0] = 5.1
    log.log("k", OSError("a"))
    assert len(lines) == 4 and "OSError: a" in lines[-1]
    log.log("text", "plain message")
    assert lines[-1] == "text: plain message"


def test_throttled_log_survives_a_broken_writer():
    def broken(text):
        raise BrokenPipeError()

    outputs.ThrottledLog(broken).log("k", OSError("x"))


def test_dropped_control_line_is_logged():
    stuck, logged = threading.Event(), []
    w = outputs.LineWriter(sink=lambda line: stuck.wait(), control_max=2, log=lambda k, m: logged.append(m))
    for i in range(6):
        w.send({"id": i})
    stuck.set()
    assert logged and "dropping" in logged[0]
