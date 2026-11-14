"""Non-blocking output for the robot worker, and throttled logging. No ROS imports.

The worker's stdout is a pipe to the backend. If the backend stops reading, a write blocks, and a blocked
write on the drive thread would stop the 10 Hz tick, the deadman and the stop repeats. So one writer thread
owns the pipe and everyone else only enqueues."""
import collections
import json
import sys
import threading
import time
from typing import Any, Callable


class LineWriter:
    def __init__(self, sink: Callable[[str], Any] | None = None, control_max: int = 1000, stream_max: int = 8,
                 log: Callable[[Any, Any], Any] | None = None) -> None:
        self.sink = sink or self._stdout
        self.log = log or ThrottledLog(lambda text: print(f"[hive-worker] {text}", file=sys.stderr, flush=True)).log
        self._control: collections.deque[str] = collections.deque(maxlen=control_max)  # replies, link: sent first
        self._stream: collections.deque[str] = collections.deque(maxlen=stream_max)    # sensor payloads: oldest dropped
        self._cond = threading.Condition()
        self._busy = False
        self.dropped = 0
        threading.Thread(target=self._run, name="stdout-writer", daemon=True).start()

    @staticmethod
    def _stdout(line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()

    def send(self, obj: dict) -> None:
        """A control line (reply, link, ready). Never blocks."""
        self._put(self._control, obj)

    def send_stream(self, obj: dict) -> None:
        """A sensor payload. Never blocks; drops the oldest when the pipe is behind."""
        self._put(self._stream, obj)

    def _put(self, queue: collections.deque, obj: dict) -> None:
        line = json.dumps(obj) + "\n"
        lost_control = False
        with self._cond:
            if len(queue) == queue.maxlen:
                self.dropped += 1
                lost_control = queue is self._control
            queue.append(line)
            self._cond.notify()
        if lost_control:  # a reply or link line was lost: the pipe has been stuck for a long time
            self.log("stdout", "control queue full, dropping the oldest reply or link line")

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._control and not self._stream:
                    self._cond.wait()
                line = (self._control or self._stream).popleft()
                self._busy = True
            try:
                self.sink(line)
            except (OSError, ValueError):
                pass  # the backend is gone; the worker ends when its stdin closes
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def flush(self, timeout: float) -> None:
        """Wait (bounded) for queued lines to leave, so the last replies are not lost at exit."""
        end = time.monotonic() + timeout
        with self._cond:
            while (self._control or self._stream or self._busy) and time.monotonic() < end:
                self._cond.wait(max(0.0, end - time.monotonic()))


class ThrottledLog:
    """Log a repeating error once per (key, error type), then at most every `interval` seconds."""

    def __init__(self, write: Callable[[str], Any], interval: float = 5.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.write, self.interval, self.clock = write, interval, clock
        self._last: dict[Any, float] = {}
        self._lock = threading.Lock()

    def log(self, key: Any, error: BaseException | str) -> None:
        kind = error if isinstance(error, str) else type(error).__name__
        now = self.clock()
        with self._lock:
            last = self._last.get((key, kind))
            if last is not None and now - last < self.interval:
                return
            self._last[(key, kind)] = now
        try:
            self.write(f"{key}: {kind}" if isinstance(error, str) else f"{key}: {kind}: {error}")
        except (OSError, ValueError):  # a log line must never break the caller
            pass
