"""In-process fake fleet (Delivery A). Three asyncio agents share one FakeWorld."""

from __future__ import annotations

import asyncio
import itertools
from typing import Any, Optional

from ..contracts import Observation, TaskEnvelope, TaskError, TaskEvent
from ..sim.fake_world import DURATION_S, FakeWorld
from .base import EventSink, GatewayInfo, RobotGateway, SubmitReceipt


class FakeGateway(RobotGateway):
    def __init__(self, time_scale: float = 1.0, progress_steps: int = 4):
        self.time_scale = time_scale
        self.progress_steps = progress_steps
        self.world = FakeWorld()
        self.run_id = ""
        self._sink: Optional[EventSink] = None
        self._busy: dict[str, str] = {}  # robot_id -> attempt_id
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._seq = itertools.count()
        self._evt = itertools.count(1)

    def info(self) -> GatewayInfo:
        return GatewayInfo("fake", "Fake fleet (in-process simulation)", False, True,
                           "Direct simulation; does not exercise AgenticROS/Synapse.")

    async def start(self, sink: EventSink) -> None:
        self._sink = sink

    async def stop(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()

    async def status(self) -> dict[str, dict[str, Any]]:
        return {rid: dict(self.world.status(rid), current_attempt_id=self._busy.get(rid))
                for rid in self.world.robots}

    async def active_attempts(self) -> list[str]:
        return [a for a, t in self._tasks.items() if not t.done()]

    async def inject_fault(self, fault: str) -> None:
        self.world.inject(fault)

    async def debug_truth(self) -> dict[str, Any]:
        return self.world.snapshot()

    async def reset(self, run_id: str, faults: Optional[list[str]] = None) -> None:
        for ev in self._cancel.values():
            ev.set()
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._cancel.clear()
        self._busy.clear()
        self.world = FakeWorld(faults)
        self.run_id = run_id

    async def submit(self, env: TaskEnvelope) -> SubmitReceipt:
        if env.run_id != self.run_id:
            return SubmitReceipt(False, f"stale run {env.run_id}")
        if env.attempt_id in self._tasks:
            return SubmitReceipt(True, "duplicate submission ignored")
        self._cancel[env.attempt_id] = asyncio.Event()
        self._tasks[env.attempt_id] = asyncio.create_task(self._run(env))
        return SubmitReceipt(True, "queued")

    async def cancel(self, run_id: str, attempt_id: str) -> None:
        if run_id == self.run_id and attempt_id in self._cancel:
            self._cancel[attempt_id].set()

    # ----------------------------------------------------------- internals
    async def _emit(self, env: TaskEnvelope, status: str, progress: float = 0.0, **kw: Any) -> None:
        if self._sink is None:
            return
        obs = [Observation(**o) for o in kw.pop("observations", [])]
        err = kw.pop("error", None)
        event = TaskEvent(
            event_id=f"{env.robot_id}-ev{next(self._evt)}", run_id=env.run_id, attempt_id=env.attempt_id,
            robot_id=env.robot_id, seq=next(self._seq), status=status, progress=progress,
            error=TaskError(**err) if err else None, observations=obs, **kw,
        )
        await self._sink(event)

    async def _run(self, env: TaskEnvelope) -> None:
        rid, skill = env.robot_id, env.skill
        cancel = self._cancel[env.attempt_id]
        if rid in self._busy:
            await self._emit(env, "rejected", error={"code": "robot_busy", "message": f"busy with {self._busy[rid]}"})
            return
        problem = self.world.precheck(rid, skill, env.params)
        if problem:
            await self._emit(env, "rejected", error=problem)
            return
        self._busy[rid] = env.attempt_id
        try:
            await self._emit(env, "accepted")
            step = DURATION_S[skill] * self.time_scale / self.progress_steps
            hang = self.world.hangs(skill)
            i = 0
            # Everything before the final step precedes the effect commit point.
            while hang or i < self.progress_steps - 1:
                try:
                    await asyncio.wait_for(cancel.wait(), timeout=max(step, 0.0005))
                except asyncio.TimeoutError:
                    pass
                if cancel.is_set():
                    await self._emit(env, "canceled", progress=i / self.progress_steps,
                                     result={"effect_committed": False})
                    return
                i += 1
                if not hang:
                    await self._emit(env, "running", progress=i / self.progress_steps)
            out = self.world.execute(rid, skill, env.params)
            await self._emit(env, out.status, progress=1.0, result=out.result, error=out.error,
                             observations=out.observations, energy_used_pct=out.energy_used_pct)
        except asyncio.CancelledError:
            raise
        finally:
            if self._busy.get(rid) == env.attempt_id:
                del self._busy[rid]
