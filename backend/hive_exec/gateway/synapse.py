"""RobotGateway over the HIVE Synapse bridge (Delivery B).

Executive -> WebSocket -> bridge/server.mjs (imports actual @corebrum/core) -> rosbridge
-> ROS 2 fake action servers. ROS/Synapse code stays in the bridge process.

``verified_transport`` becomes True only after a terminal result has made the full round
trip through the bridge in this process. A lost bridge/rosbridge connection marks the
gateway disconnected; the Executive then blocks new work until a coordinated reset.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Optional

import websockets

from ..contracts import TERMINAL_STATUSES, TaskEnvelope, TaskEvent
from .base import EventSink, GatewayInfo, RobotGateway, SubmitReceipt

log = logging.getLogger("hive.synapse")


class GatewayUnavailable(RuntimeError):
    pass


class SynapseGateway(RobotGateway):
    def __init__(self, url: str, kind: str = "synapse", label: str = "AgenticROS / Corebrum Synapse",
                 request_timeout: float = 15.0):
        self.url = url
        self.kind = kind
        self.label = label
        self.request_timeout = request_timeout
        self._sink: Optional[EventSink] = None
        self._ws = None
        self._reader: Optional[asyncio.Task] = None
        self._connector: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._active: set[str] = set()
        self._bridge_info: dict[str, Any] = {}
        self._ros_connected = False
        self._roundtrips = 0
        self._stopping = False
        self._disconnect_listeners: list = []
        self._events: asyncio.Queue = asyncio.Queue()
        self._consumer: Optional[asyncio.Task] = None
        self.run_id = ""

    # ------------------------------------------------------------ lifecycle
    def info(self) -> GatewayInfo:
        bi = self._bridge_info
        detail = (f"@corebrum/core {bi.get('core_version', '?')} @ Synapse {str(bi.get('synapse_commit', '?'))[:10]}, "
                  f"patch {bi.get('patch', '?')}, rosbridge {bi.get('rosbridge_url', '?')}; "
                  f"{self._roundtrips} round trip(s) this run") if bi else f"bridge {self.url} not connected"
        return GatewayInfo(self.kind, self.label, self._roundtrips > 0, self.connected, detail)

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ros_connected

    def on_disconnect(self, cb) -> None:
        self._disconnect_listeners.append(cb)

    async def start(self, sink: EventSink) -> None:
        self._sink = sink
        self._consumer = asyncio.create_task(self._consume_events())
        self._connector = asyncio.create_task(self._connect_loop())
        for _ in range(100):  # wait up to ~10 s for the first connection
            if self.connected:
                return
            await asyncio.sleep(0.1)
        log.warning("Synapse bridge not connected yet (%s)", self.url)

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._connector, self._consumer):
            if task:
                task.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _connect_loop(self) -> None:
        while not self._stopping:
            try:
                async with websockets.connect(self.url, max_size=4 * 1024 * 1024) as ws:
                    self._ws = ws

                    async def hello():
                        self._bridge_info = await self._request("hello")
                        self._ros_connected = bool(self._bridge_info.get("rosbridge_connected"))

                    hello_task = asyncio.create_task(hello())  # replies arrive via the reader loop below
                    async for raw in ws:
                        await self._on_message(json.loads(raw))
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as exc:
                log.warning("bridge connection failed: %s", exc)
            finally:
                was = self._ws is not None
                self._ws = None
                self._ros_connected = False
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(GatewayUnavailable("bridge disconnected"))
                self._pending.clear()
                if was and self._active:
                    for cb in self._disconnect_listeners:
                        await cb("Synapse bridge disconnected with active tasks; their outcome is uncertain.")
            await asyncio.sleep(1.0)

    async def _on_message(self, msg: dict) -> None:
        op = msg.get("op")
        if op == "reply":
            fut = self._pending.pop(msg.get("req_id"), None)
            if fut and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(GatewayUnavailable(msg.get("error", "bridge error")))
        elif op == "transport":
            was = self._ros_connected
            self._ros_connected = bool(msg.get("connected"))
            if msg.get("info"):
                self._bridge_info = msg["info"]
            if was and not self._ros_connected and self._active:
                for cb in self._disconnect_listeners:
                    await cb("rosbridge disconnected with active tasks; their outcome is uncertain.")
        elif op == "event":
            ev = TaskEvent.model_validate(msg["event"])
            if ev.status in TERMINAL_STATUSES:
                self._active.discard(ev.attempt_id)
                self._roundtrips += 1
            # Never await the Executive from the reader loop: it may be waiting on a reply.
            self._events.put_nowait(ev)

    async def _consume_events(self) -> None:
        while True:
            ev = await self._events.get()
            if self._sink:
                try:
                    await self._sink(ev)
                except Exception:  # noqa: BLE001
                    log.exception("event handler failed for %s", ev.event_id)

    async def _request(self, op: str, **payload: Any) -> Any:
        if self._ws is None:
            raise GatewayUnavailable(f"bridge {self.url} not connected")
        req_id = f"r{next(self._ids)}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps({"op": op, "req_id": req_id, **payload}))
        return await asyncio.wait_for(fut, self.request_timeout)

    # ---------------------------------------------------------- operations
    async def submit(self, envelope: TaskEnvelope) -> SubmitReceipt:
        if not self.connected:
            return SubmitReceipt(False, "Synapse bridge or rosbridge not connected")
        try:
            r = await self._request("submit", envelope=envelope.model_dump())
        except (GatewayUnavailable, asyncio.TimeoutError) as exc:
            # The goal may already be executing: report delivery as unknown, never as a clean failure.
            self._active.add(envelope.attempt_id)
            return SubmitReceipt(False, f"submit not confirmed: {exc!r}", delivery_unknown=True)
        if r.get("accepted_for_delivery"):
            self._active.add(envelope.attempt_id)
        return SubmitReceipt(bool(r.get("accepted_for_delivery")), r.get("detail", ""))

    async def cancel(self, run_id: str, attempt_id: str) -> None:
        try:
            await self._request("cancel", run_id=run_id, attempt_id=attempt_id)
        except (GatewayUnavailable, asyncio.TimeoutError) as exc:
            log.warning("cancel request failed: %s", exc)

    async def status(self) -> dict[str, dict[str, Any]]:
        try:
            r = await self._request("status")
        except (GatewayUnavailable, asyncio.TimeoutError):
            return {}
        return r.get("robots", {})

    async def active_attempts(self) -> list[str]:
        return sorted(self._active)

    async def reset(self, run_id: str, faults: Optional[list[str]] = None) -> None:
        r = await self._request("reset", run_id=run_id, faults=list(faults or []))
        self._active.clear()
        self._roundtrips = 0
        self.run_id = run_id
        if not r.get("settled", True):
            log.warning("fake servers did not settle before reset")

    async def inject_fault(self, fault: str) -> None:
        await self._request("inject_fault", fault=fault)

    async def debug_truth(self) -> Optional[dict[str, Any]]:
        try:
            return await self._request("truth")
        except (GatewayUnavailable, asyncio.TimeoutError):
            return None
