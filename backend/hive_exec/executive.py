"""HIVE Executive: mission state machine, approval, scheduler and event reconciliation.

One active mission, immutable revisions, approval bound to (revision, proposal
hash), dispatch only after dependency success, effects applied only from
accepted results, dedupe of events, run-scoped IDs, confirmed cancellation and
one bounded recovery proposal.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .agent_commands import describe_command, write_command
from .allocator import Allocator, CapabilityAllocator
from .catalog import BATTERY_RESERVE_PCT, LOCATIONS, ROBOTS, SKILLS, area_contains, catalog_dict, route_between
from .context import ContextBudgetExceeded, build_context
from .contracts import (
    RUN_EXPORT_SCHEMA,
    TERMINAL_STATUSES,
    AllocatedTask,
    ExecutionPlan,
    Goal,
    Observation,
    PlannerRecord,
    PlanProposal,
    PreviewDiff,
    TaskEnvelope,
    TaskEvent,
    TaskSpec,
    normalize_planner_kind,
)
from .gateway.base import RobotGateway
from .planner.base import Planner, PlannerError
from .planner.preview import compute_preview_diff
from .validator import ValidationResult, validate, validate_preview
from .world import ObservedWorld, RobotState, initial_robot_states

log = logging.getLogger(__name__)

SIM_WORLDS = ("fixed", "random")
UNAPPLIED_NOTE = "Mission ended before your message was applied; send it again as a new mission."
HELD_REQUEUED_NOTE = "Live replan failed; your message is queued for the next planning step."
# A context over the input budget: retrying the same mission repeats the same overflow, so it is not retryable.
# I2: the note a plan-less planning reply leaves on the messages its context included.
REPLY_NOTES = {"answer": "The planner answered.", "clarify": "The planner asked a question.",
               "reject": "The planner rejected the request."}
BUDGET_REASON = ("This request is too long for the planner. Start a new mission with a shorter command or fewer "
                 "messages.")
TERMINAL_MISSION = {"completed", "blocked", "rejected", "canceled", "planner_error", "answered"}
MAX_CLARIFICATIONS = 3  # clarify rounds per mission; one more ends the mission blocked
CLARIFY_CAP_REASON = f"The planner still could not resolve the request after {MAX_CLARIFICATIONS} questions."
MAX_STAGES = 4
PRE_DISPATCH_FAIL = "precondition_failed"


class ExecError(Exception):
    def __init__(self, message: str, status: int = 409, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


@dataclass
class TaskRuntime:
    task_id: str
    revision: int
    robot_id: Optional[str]
    # pending|dispatched|accepted|running|canceling|succeeded|failed|rejected|canceled|uncertain|skipped|
    # superseded (never dispatched; an approved operator-message revision replaced its revision - not a failure)
    state: str = "pending"
    attempt_ids: list[str] = field(default_factory=list)
    progress: float = 0.0
    result: Optional[dict] = None
    error: Optional[dict] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class Attempt:
    attempt_id: str
    revision: int
    task_id: str
    robot_id: str
    envelope: dict
    status: str = "dispatched"
    last_seq: int = -1
    effects_applied: bool = False
    submitted_at: float = 0.0
    task_stop_requested: bool = False  # operator stopped this one task (not the mission)
    stopped_after: Optional[str] = None  # the executive canceled it because this task failed (_on_task_failure)
    command_file: Optional[str] = None  # agent_commands/<robot>/<task>-r<rev>-a<n>.json, when recorded


@dataclass
class Mission:
    mission_id: str
    command: str
    planner_kind: str
    created_at: float
    status: str = "planning"
    canonical_goals: Optional[list[Goal]] = None
    revisions: list[ExecutionPlan] = field(default_factory=list)
    active_revision: Optional[int] = None
    tasks: dict[str, TaskRuntime] = field(default_factory=dict)  # key f"{rev}:{task_id}"
    stage_count: int = 0
    recovery_used: bool = False
    clarification_question: Optional[str] = None
    clarifications: list[dict] = field(default_factory=list)  # {question, options, answer, phase}, oldest first
    outcome: Optional[dict] = None
    history: list[str] = field(default_factory=list)
    planning_log: list[dict] = field(default_factory=list)
    last_proposal: Optional[dict] = None
    # Operator messages, oldest first: {id, text, mode, status: queued|held|delivered|canceled, created_at,
    # delivered_at, delivered_to_revision, delivered_as: feedback|answer, note?}. Replaced, never mutated.
    operator_messages: list[dict] = field(default_factory=list)
    # Message invariant. message_context: revision -> ids of the delivered messages its planning context included;
    # approving it applies those not yet applied (I1). consumed_messages: ids a plan-less reply answered (I2).
    # When the mission ends, a delivered feedback message that is neither is canceled (I3, _close_messages).
    message_context: dict[int, frozenset[str]] = field(default_factory=dict)
    consumed_messages: frozenset[str] = frozenset()
    # The planning phase/kind/failure of the current stage; a feedback replan keeps the stage it revises.
    stage_phase: str = "initial"
    stage_rev_kind: str = "initial"
    stage_failure: Optional[dict] = None
    revising: Optional[dict] = None  # brief of the proposal a feedback replan revises (previous_proposal)


MESSAGE_MAX_CHARS = 500
OPEN_MESSAGE = ("queued", "held")


# Display-only parts of a proposal: approval binds what can run, so these stay out of the hash.
HASH_EXCLUDED = {"next_stage_preview", "preview_changes"}


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Executive:
    def __init__(self, gateway: RobotGateway, planners: dict[str, Planner], allocator: Optional[Allocator] = None,
                 clock: Callable[[], float] = time.time, default_planner: str = "llm",
                 runs_dir: Optional[Path] = None, sim_world: str = "fixed"):
        if sim_world not in SIM_WORLDS:
            raise ValueError(f"unknown sim world {sim_world!r} (expected one of {list(SIM_WORLDS)})")
        self.gateway = gateway
        self.sim_world = sim_world  # default world kind for resets that do not name one
        self.sim: dict[str, Any] = {"world": "fixed", "seed": None}  # the world the fleet reports it built
        self.runs_dir = Path(runs_dir) if runs_dir is not None else None
        self.planners = {normalize_planner_kind(k): p for k, p in planners.items()}  # "qwen" -> "llm"
        self.allocator = allocator or CapabilityAllocator()
        self.clock = clock
        self.default_planner = normalize_planner_kind(default_planner)
        self.lock = asyncio.Lock()
        self.run_id = ""
        self.scenario = "unknown"
        self.faults: list[str] = []
        self.world = ObservedWorld(clock)
        self.robots: dict[str, RobotState] = initial_robot_states(clock)
        self.mission: Optional[Mission] = None
        self.attempts: dict[str, Attempt] = {}
        self.seen_events: set[str] = set()
        self.raw_events: list[dict] = []
        self.timeline: list[dict] = []
        self._tl = itertools.count(1)
        self._mission_seq = itertools.count(1)
        self._planning: Optional[asyncio.Task] = None
        # A live feedback replan (operator message while executing); at most one at a time.
        self._live: Optional[asyncio.Task] = None
        self._gen = 0  # bumps on reset/cancel/abandoned live replan; stale async results are discarded
        # The first failure of the stage being settled: it, not a later one, is the recovery failure.
        self._stage_failed: Optional[TaskRuntime] = None

    # ================================================================ setup
    async def start(self, scenario: str = "unknown") -> None:
        if hasattr(self.gateway, "on_disconnect"):
            self.gateway.on_disconnect(self._on_transport_lost)
        await self.gateway.start(self.handle_event)
        try:
            # A coordinated reset also stops work a previous Executive process may have left running.
            await self.reset(scenario=scenario)
        except Exception as exc:  # noqa: BLE001 - keep serving; the UI shows the transport as disconnected
            self.log("transport", f"Initial coordinated reset failed: {exc}. Reset again once the transport is up.")

    async def _on_transport_lost(self, reason: str) -> None:
        async with self.lock:
            m = self.mission
            if m and m.status not in TERMINAL_MISSION:
                self._block(m, f"{reason} New work is blocked until a coordinated reset.")

    def log(self, kind: str, message: str, **data: Any) -> None:
        self.timeline.append({"seq": next(self._tl), "t": self.clock(), "kind": kind, "message": message,
                              "run_id": self.run_id, **({"data": data} if data else {})})

    async def refresh_status(self, sync_location: bool = False) -> None:
        status = await self.gateway.status()
        for rid, st in status.items():
            r = self.robots.get(rid)
            if r is None:
                continue
            if sync_location and st.get("location_id") in LOCATIONS:
                r.location_id = st["location_id"]  # a random world can start a robot elsewhere
            r.battery_pct = float(st.get("battery_pct", r.battery_pct))
            r.health = st.get("health", r.health)
            r.available = bool(st.get("available", r.available))
            r.last_update = self.clock()

    # ================================================================ reset
    async def reset(self, scenario: str = "unknown", faults: Optional[list[str]] = None,
                    world: Optional[str] = None, seed: Optional[int] = None) -> dict:
        """Coordinated reset. Settles old work first; prior-run events are discarded afterwards.

        world: "fixed" (demo world) or "random"; None uses the executive default (HIVE_SIM_WORLD).
        A random world without a seed gets a fresh one, recorded so the run can be replayed.
        """
        world = world or self.sim_world
        if world not in SIM_WORLDS:
            raise ExecError(f"unknown sim world {world!r}", 422)
        if world == "fixed":
            seed = None
        elif seed is None:
            seed = random.SystemRandom().randrange(2 ** 32)
        self._gen += 1
        for task in (self._planning, self._live):
            if task and not task.done():
                task.cancel()
        self._live = None
        async with self.lock:
            active = [a for a in self.attempts.values() if a.status not in TERMINAL_STATUSES]
            if self.mission and self.mission.status not in TERMINAL_MISSION:
                self.mission.status = "canceling"
            for a in active:
                await self.gateway.cancel(self.run_id, a.attempt_id)
        settled = await self._wait_settled(5.0)
        async with self.lock:
            if not settled:
                self.log("reset", "Old work did not confirm termination; forcing coordinated fake-world reset.")
            self.run_id = f"run-{uuid.uuid4().hex[:8]}"
            self.scenario = scenario
            self.faults = list(faults or [])
            try:
                reported = await self.gateway.reset(self.run_id, self.faults, world=world, seed=seed)
            except Exception as exc:  # noqa: BLE001
                self.log("reset", f"Coordinated reset failed: {exc}")
                raise ExecError(f"Coordinated reset failed: {exc}", 503) from exc
            self.world = ObservedWorld(self.clock)
            reported = reported if isinstance(reported, dict) else {}
            self.sim = {"world": reported.get("world") or "fixed", "seed": reported.get("seed")}
            self.robots = initial_robot_states(self.clock)
            await self.refresh_status(sync_location=True)
            self.mission = None
            self.attempts.clear()
            self._stage_failed = None
            self.raw_events.clear()
            self.timeline.clear()
            sim = self.sim["world"] + (f" seed={self.sim['seed']}" if self.sim["seed"] is not None else "")
            self.log("reset", f"New run {self.run_id} (scenario={scenario}, faults={self.faults or 'none'}, "
                              f"world={sim}).")
            if self.sim["world"] != world or (world == "random" and self.sim["seed"] != seed):
                self.log("reset", f"Requested world={world} seed={seed}, but the fleet ignored it and runs "
                                  f"world={sim}. Rebuild/restart the fake fleet to get world support.")
            if scenario == "known":
                self._seed_known()
        return {"run_id": self.run_id}

    def _seed_known(self) -> None:
        """Operator-provided prior survey for the known-object story (not simulator truth)."""
        self.world.accept(Observation(kind="object_seen", entity_id="red_block_1", label="red_block",
                                      area_id="work_area", location_id="pickup_zone",
                                      attrs={"note": "prior survey"}), source="operator_seed")
        self.world.accept(Observation(kind="route_clear", entity_id="pickup_corridor", route_id="pickup_corridor"),
                          source="operator_seed")
        self.log("world", "Seeded prior survey: red_block_1 at pickup_zone; pickup_corridor clear.")

    async def _wait_settled(self, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not any(a.status not in TERMINAL_STATUSES for a in self.attempts.values()):
                return True
            await asyncio.sleep(0.02)
        return False

    async def inject_fault(self, fault: str) -> None:
        async with self.lock:
            await self.gateway.inject_fault(fault)
            self.faults.append(fault)
            await self.refresh_status()
            self.log("fault", f"Injected simulator fault: {fault}")

    # ============================================================ missions
    async def create_mission(self, command: str, planner_kind: Optional[str] = None) -> Mission:
        planner_kind = normalize_planner_kind(planner_kind) or self.default_planner
        if planner_kind not in self.planners:
            raise ExecError(f"Unknown planner {planner_kind}", 400)
        async with self.lock:
            if self.mission and self.mission.status not in TERMINAL_MISSION:
                raise ExecError(f"Mission {self.mission.mission_id} is still {self.mission.status}; "
                                "cancel it or reset first.")
            if self.mission is not None:
                raise ExecError("Reset the scenario before another run (one mission per run).")
            self.mission = Mission(f"m{next(self._mission_seq)}-{uuid.uuid4().hex[:6]}", command.strip(),
                                   planner_kind, self.clock())
            self.log("mission", f"Mission created with {planner_kind} planner.", command=command)
        await self._plan("initial", "initial")
        return self.mission

    async def answer_clarification(self, answer: str) -> None:
        m = self._require_mission()
        if m.status != "clarification":
            raise ExecError("No clarification is pending.")
        self.log("operator", "Clarification answered.", answer=answer)
        if m.clarifications and m.clarifications[-1].get("answer") is None:
            m.clarifications = [*m.clarifications[:-1], {**m.clarifications[-1], "answer": answer}]
        phase = "continuation" if m.canonical_goals else "clarification_answer"
        await self._plan(phase, "clarification", clarification={"question": m.clarification_question or "",
                                                                 "answer": answer})

    async def replan(self) -> None:
        m = self._require_mission()
        if m.status not in ("awaiting_approval", "planner_error"):
            raise ExecError("Re-planning is only available while awaiting approval or after a planner error.")
        if m.status == "planner_error" and (m.outcome or {}).get("retryable") is False:
            raise ExecError(f"This planner error cannot be retried: {m.outcome.get('reason')}")
        if m.status == "planner_error":
            m.status = "planning"
        phase = "continuation" if m.canonical_goals else "initial"
        await self._plan(phase, "continuation" if m.canonical_goals else "initial")

    # ==================================================== operator messages
    async def post_message(self, text: str, mode: str) -> dict:
        """An operator message to the planner. Untrusted text: length-capped here, never executed, and only
        ever rendered inside the prompt's delimited OPERATOR FEEDBACK block (or used as a clarification
        answer). "now" replans at once (or holds while a planning is in flight); "queue" waits for the next
        planning point."""
        text = (text or "").strip()
        if not 1 <= len(text) <= MESSAGE_MAX_CHARS:
            raise ExecError(f"A message must be 1 to {MESSAGE_MAX_CHARS} characters.", 400)
        if mode not in ("now", "queue"):
            raise ExecError(f"Unknown message mode {str(mode)[:20]!r} (expected now or queue).", 400)
        async with self.lock:
            m = self._message_mission()
            mid = f"msg-{len(m.operator_messages) + 1}"
            m.operator_messages = [*m.operator_messages, {
                "id": mid, "text": text, "mode": mode, "status": "queued", "created_at": self.clock(),
                "delivered_at": None, "delivered_to_revision": None, "delivered_as": None}]
            self.log("operator", f"Operator message {mid} ({mode}) while {m.status}.", text=text)
            answer = self._route_message(m, mid, mode)
        if answer:  # nothing awaits between the lock and this call, so the clarification is still pending
            await self.answer_clarification(text)
        return self._message(m, mid)

    async def send_message_now(self, message_id: str) -> dict:
        async with self.lock:
            m = self._message_mission()
            msg = self._message(m, message_id)
            if msg["status"] != "queued":
                raise ExecError(f"Message {message_id} is {msg['status']}; only a queued message can be sent now.")
            self.log("operator", f"Operator sent queued message {message_id} now.")
            answer = self._route_message(m, message_id, "now")
        if answer:
            await self.answer_clarification(msg["text"])
        return self._message(m, message_id)

    async def cancel_message(self, message_id: str) -> dict:
        async with self.lock:
            m = self.mission
            if m is None:
                raise ExecError("No active mission.")
            msg = self._message(m, message_id)
            if msg["status"] not in OPEN_MESSAGE:
                raise ExecError(f"Message {message_id} is {msg['status']}; only a queued or held message can be "
                                "removed.")
            self._update_messages(m, {message_id}, status="canceled", note="Removed by the operator.")
            self.log("operator", f"Operator removed message {message_id}.")
            return self._message(m, message_id)

    def _message_mission(self) -> Mission:
        m = self.mission
        if m is None:
            raise ExecError("No active mission; send the text as a new mission instead.")
        if m.status in TERMINAL_MISSION or m.status == "canceling":
            raise ExecError(f"Mission is {m.status}; start a new mission instead.")
        return m

    @staticmethod
    def _message(m: Mission, message_id: str) -> dict:
        for x in m.operator_messages:
            if x["id"] == message_id:
                return dict(x)
        raise ExecError(f"No message {message_id}.", 404)

    @staticmethod
    def _update_messages(m: Mission, ids: set[str], **changes: Any) -> None:
        m.operator_messages = [{**x, **changes} if x["id"] in ids else x for x in m.operator_messages]

    def _deliver(self, m: Mission, ids: set[str], as_: str = "feedback") -> None:
        if ids:
            now = self.clock()
            m.operator_messages = [
                {**{k: v for k, v in x.items() if k != "note"}, "status": "delivered", "delivered_at": now,
                 "delivered_as": as_} if x["id"] in ids else x for x in m.operator_messages]

    def _close_messages(self, m: Mission, why: str) -> None:
        """Queued and held messages never outlive the mission (STOP ALL, cancel, any terminal outcome). I3: a
        delivered message that no approved revision applied (I1) and no plan-less reply consumed (I2) is canceled
        too, so the operator knows to resend it."""
        ids = {x["id"] for x in m.operator_messages if x["status"] in OPEN_MESSAGE}
        if ids:
            self._update_messages(m, ids, status="canceled", note=f"Not delivered: {why}"[:300])
        self._update_messages(m, self._unapplied(m), status="canceled", note=UNAPPLIED_NOTE)

    @staticmethod
    def _unapplied(m: Mission) -> set[str]:
        """Delivered feedback that is neither applied to an approved revision nor consumed by a reply."""
        return {x["id"] for x in m.operator_messages if x["status"] == "delivered" and x["delivered_as"] == "feedback"
                and x["delivered_to_revision"] is None and x["id"] not in m.consumed_messages}

    @staticmethod
    def _apply_messages(m: Mission, plan: ExecutionPlan) -> None:
        """I1: approving a revision applies the messages its planning context included, unless an earlier
        approved revision already has them (the first one keeps them)."""
        used = m.message_context.get(plan.revision, frozenset())
        m.operator_messages = [
            {**{k: v for k, v in x.items() if k != "note"}, "delivered_to_revision": plan.revision}
            if x["id"] in used and x["status"] == "delivered" and x["delivered_to_revision"] is None else x
            for x in m.operator_messages]

    def _consume(self, m: Mission, ids: frozenset[str], note: str) -> None:
        """I2: a planning reply that is not a plan answered these messages (the operator got a response). One a
        revision still awaiting approval carries stays with that revision (approving it applies the message)."""
        ids = frozenset(ids) - self._pending_context(m)
        m.consumed_messages = m.consumed_messages | ids
        self._update_messages(m, ids & self._open_feedback(m), note=note[:300])

    @staticmethod
    def _open_feedback(m: Mission) -> set[str]:
        return {x["id"] for x in m.operator_messages if x["status"] == "delivered"
                and x["delivered_as"] == "feedback" and x["delivered_to_revision"] is None}

    def _note_delivered(self, m: Mission, note: str) -> None:
        """Explain a delivered message whose planning produced no revision (the running plan continues). One that
        a revision still awaiting approval carries keeps its state: approving that revision applies it."""
        self._update_messages(m, self._unapplied(m) - self._pending_context(m), note=note[:300])

    @staticmethod
    def _pending_context(m: Mission) -> frozenset[str]:
        """Message ids in the context of a revision that is still awaiting approval."""
        return frozenset().union(*(m.message_context.get(p.revision, frozenset()) for p in m.revisions
                                   if p.status == "awaiting_approval"))

    @staticmethod
    def _feedback_texts(m: Mission) -> list[str]:
        return [x["text"] for x in m.operator_messages if x["status"] == "delivered" and x["delivered_as"] == "feedback"]

    def _route_message(self, m: Mission, mid: str, mode: str) -> bool:
        """Apply a message under the lock. True: it answers the pending clarification (caller answers it)."""
        if m.status == "clarification":
            self._deliver(m, {mid}, "answer")
            return True
        if mode == "now":
            self._send_now(m, mid)
        return False

    def _send_now(self, m: Mission, mid: str) -> None:
        if m.status == "awaiting_approval":
            latest = m.revisions[-1] if m.revisions else None
            if latest is not None and latest.status in ("awaiting_approval", "invalid"):
                self._supersede(latest, "operator_message")  # never approvable from here on
            m.revising = self._brief(latest.proposal, latest) if latest is not None else None
            self._deliver(m, {mid})
            m.status = "planning"  # set now, so a second message before the planning starts is held
            self.log("planning", "Operator message: the pending revision is superseded; re-planning with it.")
            self._spawn_planning("feedback", "feedback")
        elif m.status == "executing" and not self._live_in_flight():
            pending = self._pending_live(m)
            base = pending or self._active_plan(m)
            if pending is not None:
                self._supersede(pending, "operator_message")
            m.revising = self._brief(base.proposal, base)
            self._deliver(m, {mid})
            self.log("planning", f"Operator message: re-planning while revision {m.active_revision} keeps running.")
            self._spawn_planning("feedback", "feedback", live=True)
        else:  # a planning is in flight (or about to start): the message waits for it to land
            self._update_messages(m, {mid}, status="held")

    def _live_in_flight(self) -> bool:
        return self._live is not None and not self._live.done()

    @staticmethod
    def _pending_live(m: Mission) -> Optional[ExecutionPlan]:
        """The revision proposed while another one executes, still waiting for approval (or invalid)."""
        latest = m.revisions[-1] if m.revisions else None
        if latest is None or m.active_revision is None or latest.revision <= m.active_revision:
            return None
        return latest if latest.status in ("awaiting_approval", "invalid") else None

    @staticmethod
    def _supersede(plan: ExecutionPlan, reason: str) -> None:
        plan.status = "superseded"
        plan.superseded_reason = reason

    def _abandon_live(self, m: Mission) -> None:
        """The running stage ended (finished, failed, blocked, canceled): a live replan in flight is discarded
        and a live revision still waiting for approval can never run."""
        if self._live is not None and not self._live.done():
            self._gen += 1  # its result is stale; nothing else plans while a stage executes
            self._live.cancel()
            self.log("planning", "The running stage ended; the live re-plan in flight is discarded.")
        self._live = None
        for p in m.revisions:
            if m.active_revision is not None and p.revision > m.active_revision \
                    and p.status in ("awaiting_approval", "invalid"):
                self._supersede(p, "stage_ended")
                self.log("revision", f"Revision {p.revision} superseded: its stage ended before approval.")

    @staticmethod
    def _brief(proposal: PlanProposal, plan: Optional[ExecutionPlan] = None) -> dict:
        """The proposal an operator message revises, compact, for the planner's previous_proposal."""
        allocations = plan.allocations if plan is not None else {}
        tasks = []
        for t in proposal.tasks:
            row = {"task_id": t.task_id, "binding_id": t.binding_id, "depends_on": list(t.depends_on),
                   "action": t.action.model_dump(exclude_none=True)}
            robot = allocations.get(t.binding_id) or t.preferred_robot_id
            tasks.append({**row, "robot_id": robot} if robot else row)
        return {**({"revision": plan.revision} if plan is not None else {}), "scope": proposal.scope,
                "summary": proposal.summary, "tasks": tasks}

    def _require_mission(self) -> Mission:
        if self.mission is None:
            raise ExecError("No active mission.", 404)
        return self.mission

    # ============================================================= planning
    async def _plan(self, phase: str, rev_kind: str, failure: Optional[dict] = None,
                    clarification: Optional[dict] = None, live: bool = False, gen: Optional[int] = None) -> None:
        """One planning call. ``live``: a feedback replan while the active revision keeps executing; the
        mission status is left alone and failures never end the mission (_live_failed). ``gen``: the
        generation a spawned planning was requested in; a reset/cancel/abandon since then makes it a no-op."""
        m = self._require_mission()
        if gen is None:
            gen = self._gen
        elif gen != self._gen:
            return  # canceled (or the stage ended) before this spawned planning started
        planner = self.planners[m.planner_kind]
        fail = self._live_failed if live else self._planner_failed
        # Every planning call is a planning point: queued and held messages are delivered to it.
        self._deliver(m, {x["id"] for x in m.operator_messages if x["status"] in OPEN_MESSAGE})
        if not live:
            m.status = "planning"
        if phase != "feedback":
            m.stage_phase, m.stage_rev_kind, m.stage_failure = phase, rev_kind, failure
            m.revising = None
        elif m.stage_phase == "recovery":
            failure = m.stage_failure  # a feedback replan of a recovery still sees the failure
        # Superseded revisions were never approved, so they are never the preview source (WS4 F5).
        previous = (self._previous_observation(m)
                    if phase == "continuation" or (phase == "feedback" and not live
                                                   and m.stage_phase == "continuation") else None)
        # I1/I2: the messages this planning context includes (a revision it makes applies them on approval).
        read = frozenset(x["id"] for x in m.operator_messages if x["status"] == "delivered")
        ctx = build_context(phase=phase, command=m.command, robots=self.robots, world=self.world,
                            canonical_goals=m.canonical_goals, stage_history=m.history,
                            failure=failure, clarification=clarification,
                            clarifications=[c for c in m.clarifications if c.get("answer") is not None],
                            previous_preview=self._preview_for_context(previous),
                            operator_feedback=self._feedback_texts(m),
                            previous_proposal=m.revising if phase == "feedback" else None)
        self.log("planning", f"Requesting {phase} proposal from {planner.kind}"
                 + (" (the active revision keeps running)." if live else "."))
        attempts, first_ok, repaired = 0, None, False
        feedback: Optional[list[str]] = None
        raw: Optional[str] = None
        out = None
        vres: Optional[ValidationResult] = None
        alloc = None
        record: Optional[PlannerRecord] = None
        t_start = time.perf_counter()
        while attempts < 2:  # first pass + at most one repair
            attempts += 1
            try:
                out = await planner.propose(ctx, feedback, raw)
            except ContextBudgetExceeded as exc:
                self.log("planning", f"Planning context over the input budget: {exc}")
                return fail(m, gen, BUDGET_REASON, None, attempts, retryable=False, detail=str(exc))
            except PlannerError as exc:
                record = exc.record
                raw = getattr(exc, "raw", None)
                if raw is not None and attempts < 2:  # malformed output: one repair attempt
                    first_ok = False
                    feedback = (record.errors if record and record.errors else [str(exc)])
                    self.log("planning", f"Proposal rejected by schema; repair attempt. ({exc})")
                    continue
                return fail(m, gen, str(exc), record, attempts)
            if gen != self._gen:
                return
            record = out.record
            if out.proposal.preview_changes and ctx.previous_preview is None:
                # preview_changes answers a previous preview; with none in the context it is noise.
                log.debug("dropped preview_changes %r: the planning context had no previous_preview",
                          out.proposal.preview_changes)
                out.proposal = out.proposal.model_copy(update={"preview_changes": []})
            if out.proposal.disposition != "plan":
                first_ok = first_ok if first_ok is not None else True
                break
            try:
                alloc = self.allocator.allocate(out.proposal, self.robots, {})
                vres = validate(out.proposal, alloc, self.robots, self.world, m.canonical_goals)
            except Exception as exc:  # noqa: BLE001 - a malformed proposal must not strand the mission in planning
                return fail(m, gen, f"Plan checks could not run: {type(exc).__name__}: "
                                f"{str(exc)[:300]}", record, attempts)
            if first_ok is None:
                first_ok = vres.report.ok
            if vres.report.ok or attempts >= 2 or planner.kind == "baseline":
                break
            feedback = [f"{f.code}: {f.message}" for f in vres.report.findings if f.severity == "error"]
            raw = out.proposal.model_dump_json()
            self.log("planning", "Proposal failed deterministic validation; one repair attempt.", findings=feedback)
        repaired = attempts > 1
        record = record.model_copy(update={"attempts": attempts, "repaired": repaired, "first_pass_ok": first_ok,
                                           "latency_ms": round((time.perf_counter() - t_start) * 1000, 1)})
        m.planning_log.append({"phase": phase, "planner": record.model_dump(), "disposition": out.proposal.disposition,
                               "ok": bool(vres.report.ok) if vres else None})
        m.last_proposal = out.proposal.model_dump()
        async with self.lock:
            if gen != self._gen or self.mission is not m:
                return
            if live and m.status != "executing":
                return  # the stage ended under it (_abandon_live normally got here first)
            if any(x["status"] == "held" for x in m.operator_messages):
                # The operator spoke while this planning ran: the landed proposal is never shown; one feedback
                # replan carries every held message (they are delivered when it starts).
                m.planning_log[-1] = {**m.planning_log[-1], "superseded_by_message": True}
                m.revising = self._brief(out.proposal)
                self.log("planning", "Operator message arrived during planning; the landed proposal is superseded "
                                     "unseen and re-planned with it.")
                self._spawn_planning("feedback", "feedback", live=live)
                return
            if live:
                return self._apply_live(m, out.proposal, record, vres, alloc, read)
            self._apply_proposal(m, out.proposal, record, vres, alloc, phase, rev_kind, previous, read)

    def _apply_live(self, m: Mission, proposal: PlanProposal, record: PlannerRecord,
                    vres: Optional[ValidationResult], alloc, read: frozenset[str] = frozenset()) -> None:
        """A live feedback proposal: a plan becomes a revision waiting for approval while the active revision
        keeps running; anything else is noted on the messages and changes nothing."""
        if proposal.disposition != "plan" or vres is None:
            what = proposal.answer or proposal.clarification_question or proposal.summary
            self._consume(m, read, f"The planner did not re-plan ({proposal.disposition}: {what}); revision "
                                   f"{m.active_revision} keeps running.")
            self.log("planning", f"Live re-plan returned {str(proposal.disposition)[:40]}; revision "
                                 f"{m.active_revision} keeps running.")
            return
        try:
            self._make_revision(m, proposal, record, vres, alloc, "feedback", {}, context_ids=read)
        except Exception as exc:  # noqa: BLE001 - the running revision is unaffected
            self._live_failed(m, self._gen, f"Revision could not be built: {type(exc).__name__}: "
                                            f"{str(exc)[:300]}", record, 1)

    def _live_failed(self, m: Mission, gen: int, message: str, record: Optional[PlannerRecord], attempts: int,
                     retryable: bool = True, detail: Optional[str] = None):
        """A live replan failed: the active revision keeps running; the messages say why and any held ones go
        back to the queue for the next planning point."""
        if gen != self._gen:
            return
        m.planning_log.append({"phase": "error", "planner": record.model_dump() if record else None,
                               "error": detail or message, "attempts": attempts, "live": True})
        self._note_delivered(m, f"Planner error: {message[:200]}; revision {m.active_revision} keeps running.")
        held = {x["id"] for x in m.operator_messages if x["status"] == "held"}
        self._update_messages(m, held, status="queued", note=HELD_REQUEUED_NOTE)
        self.log("planning", f"Live re-plan failed ({message[:200]}); revision {m.active_revision} keeps running.")

    @staticmethod
    def _previous_observation(m: Mission) -> Optional[ExecutionPlan]:
        """The observation stage whose preview a continuation answers, or None.

        It is the last revision that was approved (so it directly precedes this continuation), it finished,
        it is an observation stage, and no approved revision has compared itself with it yet. A full stage
        that ran (even a failed one) or a recovery after it ends the preview's life; an observation stage
        without a preview hides an older one."""
        ran = [p for p in m.revisions if p.approved_at is not None]
        last = ran[-1] if ran else None
        if (last is None or last.status != "finished" or last.proposal.scope != "observation_stage"
                or last.preview_consumed_by is not None):
            return None
        return last

    @staticmethod
    def _preview_for_context(previous: Optional[ExecutionPlan]) -> Optional[dict]:
        preview = previous.proposal.next_stage_preview if previous is not None else None
        if preview is None:
            return None
        return {"from_revision": previous.revision, **preview.model_dump(exclude_none=True)}

    def _planner_failed(self, m: Mission, gen: int, message: str, record: Optional[PlannerRecord], attempts: int,
                        retryable: bool = True, detail: Optional[str] = None):
        """retryable=False: Try again would repeat the same failure (a context over the input budget)."""
        if gen != self._gen:
            return
        m.planning_log.append({"phase": "error", "planner": record.model_dump() if record else None,
                               "error": detail or message, "attempts": attempts})
        m.status = "planner_error"
        m.outcome = {"status": "planner_error", "reason": message, **({} if retryable else {"retryable": False})}
        self._close_messages(m, "planner error")  # fail closed: a held message never re-plans by itself
        self.log("planner_error", message)

    def _apply_proposal(self, m: Mission, proposal: PlanProposal, record: PlannerRecord,
                        vres: Optional[ValidationResult], alloc, phase: str, rev_kind: str,
                        previous: Optional[ExecutionPlan] = None, read: frozenset[str] = frozenset()) -> None:
        # A feedback replan revises the current stage, so it ends the way that stage's planning would.
        stage = m.stage_phase if phase == "feedback" else phase
        if proposal.disposition in REPLY_NOTES:
            self._consume(m, read, REPLY_NOTES[proposal.disposition])  # I2
        if proposal.disposition == "clarify":
            self._ask(m, proposal.clarification_question or "", proposal.clarification_options, phase,
                      issues=proposal.issues)
            return
        if proposal.disposition == "answer":
            if stage == "initial" or stage == "clarification_answer":
                m.status, m.outcome = "answered", {"status": "answered", "reason": proposal.answer}
                self._close_messages(m, "mission answered")
                self.log("answer", proposal.answer or "")
            else:
                self._block(m, f"No continuation: {proposal.answer}")
            return
        if proposal.disposition == "reject":
            reason = proposal.summary + (f" ({'; '.join(proposal.issues)})" if proposal.issues else "")
            if stage == "initial" or stage == "clarification_answer":
                m.status, m.outcome = "rejected", {"status": "rejected", "reason": reason}
                self._close_messages(m, "planner rejected the request")
                self.log("rejected", f"Planner rejected the request: {reason}")
            else:
                self._block(m, f"No continuation: {reason}")
            return
        if proposal.disposition != "plan" or vres is None:
            # Fail closed: a disposition this code does not know never becomes a revision.
            return self._block(m, f"Unsupported planner disposition {str(proposal.disposition)[:40]!r}; "
                                  "nothing was planned.")
        try:
            plan = self._make_revision(m, proposal, record, vres, alloc, rev_kind, {}, preview_from=previous,
                                       context_ids=read)
        except Exception as exc:  # noqa: BLE001 - never leave the mission "planning"; nothing was dispatched
            return self._planner_failed(m, self._gen, f"Revision could not be built: {type(exc).__name__}: "
                                        f"{str(exc)[:300]}", record, 1)
        if not plan.validation.ok and phase in ("continuation", "recovery"):
            reasons = "; ".join(f.message for f in plan.validation.findings if f.severity == "error")
            plan.status = "not_executable"
            self._block(m, f"{phase.capitalize()} proposal is not executable: {reasons}")
            return
        m.status = "awaiting_approval"

    def _make_revision(self, m: Mission, proposal: PlanProposal, record: PlannerRecord,
                       vres: ValidationResult, alloc, kind: str, constraints: dict[str, str],
                       preview_from: Optional[ExecutionPlan] = None,
                       context_ids: frozenset[str] = frozenset()) -> ExecutionPlan:
        if proposal.disposition != "plan" or vres is None:
            raise ExecError("Only a validated plan proposal can become a revision.", 500)
        if m.canonical_goals is not None:
            proposal = proposal.model_copy(update={"mission_goals": list(m.canonical_goals)})
        evidence = sorted({e for b in self.world.objects.values() for e in b.evidence}
                          | {r.evidence for r in self.world.routes.values()})
        # The next-stage preview is display only: it is noted (warning/info, never blocking) and never allocated.
        report = vres.report.model_copy(update={"preview_findings": validate_preview(
            proposal, self.allocator, self.robots, self.world)})
        previewed = preview_from.proposal.next_stage_preview if preview_from is not None else None
        diff: Optional[PreviewDiff] = (compute_preview_diff(preview_from.revision, previewed, vres.tasks)
                                       if previewed is not None else None)
        plan = ExecutionPlan(
            mission_id=m.mission_id, revision=len(m.revisions) + 1, kind=kind,
            proposal_hash=_hash(proposal.model_dump(exclude=HASH_EXCLUDED)), run_id=self.run_id, proposal=proposal,
            allocations=dict(alloc.robots), tasks=vres.tasks, operator_constraints=dict(constraints),
            evidence_ids=evidence, world_version=self.world.version, validation=report,
            planner=record, created_at=self.clock(),
            status="awaiting_approval" if report.ok else "invalid", preview_diff=diff,
        )
        for old in m.revisions:  # only once the new revision exists
            if old.status == "awaiting_approval" or old.status == "invalid":
                self._supersede(old, "operator_message" if kind == "feedback" else "replanned")
            if old.status == "superseded" and old.superseded_by is None \
                    and old.superseded_reason in ("operator_message", "replanned"):
                old.superseded_by = plan.revision
        m.revisions.append(plan)
        # I1: approving this revision applies these messages (an edit keeps its base revision's context).
        m.message_context = {**m.message_context, plan.revision: frozenset(context_ids)}
        self.log("revision", f"Revision {plan.revision} ({kind}, {proposal.scope}) proposed by {record.label}; "
                 f"validation {'passed' if plan.validation.ok else 'FAILED'}.",
                 findings=[f.model_dump() for f in plan.validation.findings])
        return plan

    # ======================================================= operator ops
    def _latest(self, m: Mission, revision: int) -> ExecutionPlan:
        if not m.revisions or m.revisions[-1].revision != revision:
            raise ExecError(f"Revision {revision} is not the latest revision (stale approval).")
        return m.revisions[-1]

    async def approve(self, revision: int, proposal_hash: str) -> ExecutionPlan:
        async with self.lock:
            m = self._require_mission()
            live = m.status == "executing" and self._pending_live(m) is not None
            if m.status != "awaiting_approval" and not live:
                raise ExecError(f"Mission is {m.status}, not awaiting approval.")
            plan = self._latest(m, revision)
            if plan.proposal_hash != proposal_hash:
                raise ExecError("Proposal hash does not match this revision (stale approval).")
            if plan.status != "awaiting_approval":
                raise ExecError(f"Revision {revision} is {plan.status} and cannot be approved.")
            await self.refresh_status()
            # Recheck against the current world: approval authorizes this exact revision only if still valid.
            proposal = plan.proposal
            alloc = self.allocator.allocate(proposal, self.robots, plan.operator_constraints)
            vres = validate(proposal, alloc, self.robots, self.world, m.canonical_goals, plan.operator_constraints)
            if not vres.report.ok or dict(alloc.robots) != plan.allocations:
                plan.status = "invalid"
                self.log("approval", f"Approval of revision {revision} refused: state changed since validation.")
                raise ExecError("State changed since validation; re-plan required (stale approval).", 409,
                                [f.model_dump() for f in vres.report.findings])
            old = self._active_plan(m) if live else None
            if old is not None:
                # Replace the running revision: its never-dispatched tasks are superseded (not failed);
                # dispatched ones finish, and the stage ends once (_tick waits for every live attempt).
                for t in old.tasks:
                    ort = m.tasks[f"{old.revision}:{t.task_id}"]
                    if ort.state == "pending":
                        ort.state = "superseded"
                self._supersede(old, "operator_message")
                old.superseded_by = revision
                self.log("revision", f"Revision {old.revision} superseded by {revision}; its dispatched tasks "
                                     "finish, the rest never run.")
            plan.status = "executing"
            plan.approved_at = self.clock()
            plan.approved_by = "operator"
            self._apply_messages(m, plan)  # I1
            if m.canonical_goals is None:
                m.canonical_goals = list(proposal.mission_goals)
            m.active_revision = revision
            m.status = "executing"
            kind = m.stage_rev_kind if plan.kind == "feedback" else plan.kind
            if not live and (proposal.scope == "observation_stage" or kind in ("continuation", "initial")):
                m.stage_count += 1  # a live replacement is the same stage, already counted
            for t in plan.tasks:
                m.tasks[f"{revision}:{t.task_id}"] = TaskRuntime(t.task_id, revision, t.robot_id)
            if plan.preview_diff is not None and plan.preview_diff.previous_revision <= len(m.revisions):
                m.revisions[plan.preview_diff.previous_revision - 1].preview_consumed_by = revision
            self.log("approval", f"Operator approved revision {revision} ({plan.proposal_hash}).")
            await self._tick()
            return plan

    async def reject(self, revision: int, reason: str = "") -> None:
        async with self.lock:
            m = self._require_mission()
            if m.status == "executing" and self._pending_live(m) is not None:
                # Rejecting a live replan only drops it: the running revision continues unchanged.
                plan = self._latest(m, revision)
                plan.status = "rejected"
                self.log("approval", f"Operator rejected live revision {revision}; revision {m.active_revision} "
                                     f"keeps running. {reason}".strip())
                return
            if m.status != "awaiting_approval":
                raise ExecError(f"Mission is {m.status}, not awaiting approval.")
            plan = self._latest(m, revision)
            plan.status = "rejected"
            m.status = "rejected"
            m.outcome = {"status": "rejected", "reason": f"Operator rejected revision {revision}. {reason}".strip()}
            self._close_messages(m, "mission rejected")
            self.log("approval", m.outcome["reason"])

    async def edit(self, revision: int, edits: list[dict]) -> ExecutionPlan:
        """Simple typed edits: set_robot, remove_task, set_param. Produces a new revision needing approval."""
        async with self.lock:
            m = self._require_mission()
            if m.status != "awaiting_approval":
                raise ExecError(f"Mission is {m.status}, not awaiting approval.")
            base = self._latest(m, revision)
            self._check_edit_targets(base, edits)
            data = base.proposal.model_dump()
            constraints = dict(base.operator_constraints)
            for e in edits:
                op = e.get("op")
                if op == "set_robot":
                    if e.get("robot_id") not in ROBOTS:
                        raise ExecError(f"Unknown robot {e.get('robot_id')}", 400)
                    constraints[str(e["binding_id"])] = e["robot_id"]
                elif op == "clear_robot":
                    constraints.pop(str(e["binding_id"]), None)
                elif op == "remove_task":
                    tid = e["task_id"]
                    data["tasks"] = [t for t in data["tasks"] if t["task_id"] != tid]
                    for t in data["tasks"]:
                        t["depends_on"] = [d for d in t["depends_on"] if d != tid]
                elif op == "set_param":
                    for t in data["tasks"]:
                        if t["task_id"] == e["task_id"]:
                            if e["field"] == "kind":
                                raise ExecError("Changing the skill kind is not a simple edit.", 400)
                            t["action"][e["field"]] = e["value"]
                elif op == "set_depends_on":
                    for t in data["tasks"]:
                        if t["task_id"] == e["task_id"]:
                            t["depends_on"] = list(e["value"])
                else:
                    raise ExecError(f"Unsupported edit op {op}", 400)
            try:
                proposal = PlanProposal.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                raise ExecError(f"Edited plan is not a valid proposal: {exc}", 400) from exc
            if _hash(proposal.model_dump(exclude=HASH_EXCLUDED)) == base.proposal_hash \
                    and constraints == base.operator_constraints:
                raise ExecError("Edit changes nothing; no new revision was made.", 400)
            await self.refresh_status()
            alloc = self.allocator.allocate(proposal, self.robots, constraints)
            vres = validate(proposal, alloc, self.robots, self.world, m.canonical_goals, constraints)
            record = base.planner.model_copy(update={"label": base.planner.label + " + operator edit"})
            # An edit of a continuation is still compared with the preview that stage answered.
            plan = self._make_revision(m, proposal, record, vres, alloc, "edit", constraints,
                                       preview_from=self._previous_observation(m) if base.preview_diff else None,
                                       context_ids=m.message_context.get(base.revision, frozenset()))
            self.log("operator", f"Edited revision {revision} -> {plan.revision}.", edits=edits)
            return plan

    @staticmethod
    def _check_edit_targets(base: ExecutionPlan, edits: list[dict]) -> None:
        """An edit may only name tasks and bindings that can run in this revision. The next-stage preview has
        its own tasks; naming one (or something that does not exist) would change nothing real."""
        tasks = {t.task_id for t in base.tasks}
        bindings = {t.binding_id for t in base.tasks}
        preview = base.proposal.next_stage_preview
        in_preview_tasks = {t.task_id for t in preview.tasks} if preview else set()
        in_preview_bindings = {t.binding_id for t in preview.tasks} if preview else set()
        for e in edits:
            op = e.get("op")
            if op in ("remove_task", "set_param", "set_depends_on"):
                tid, known, in_preview, what = e.get("task_id"), tasks, in_preview_tasks, "task"
            elif op in ("set_robot", "clear_robot"):
                tid, known, in_preview, what = e.get("binding_id"), bindings, in_preview_bindings, "binding"
            else:
                continue  # unsupported ops keep their own error below
            if tid not in known:
                hint = "; the next-stage preview cannot be edited" if tid in in_preview else ""
                raise ExecError(f"{tid} is not a {what} in revision {base.revision}{hint}.", 400)

    async def cancel(self) -> None:
        async with self.lock:
            m = self._require_mission()
            if m.status in TERMINAL_MISSION:
                raise ExecError(f"Mission already {m.status}.")
            self._gen += 1  # discard in-flight planning results
            self._abandon_live(m)
            self._close_messages(m, "mission stopped")
            active = [a for a in self.attempts.values() if a.status not in TERMINAL_STATUSES]
            for rt in m.tasks.values():
                if rt.state == "pending":
                    rt.state = "skipped"
            for plan in m.revisions:
                if plan.status in ("awaiting_approval", "invalid"):
                    plan.status = "superseded"
            m.status = "canceling"
            self.log("cancel", f"Cancel requested; stopping dispatch and canceling {len(active)} active task(s).")
            for a in active:
                await self.gateway.cancel(self.run_id, a.attempt_id)
            self._check_cancel_settled(m)

    async def cancel_task(self, task_id: str, revision: Optional[int] = None) -> TaskRuntime:
        """Stop one task's live attempt. Task ids repeat across revisions, so the caller names the revision
        (default: the active one). The task shows "canceling" until the robot confirms; a confirmed stop of an
        active-revision task then takes the task-failure path (bounded recovery) while other tasks keep running;
        a stop of a task left running by a replaced revision is not a fault (see _handle_event)."""
        async with self.lock:
            m = self._require_mission()
            if m.status not in ("executing", "recovering"):
                raise ExecError(f"Mission is {m.status}; no task is running.")
            rev = m.active_revision if revision is None else revision
            rt = m.tasks.get(f"{rev}:{task_id}")
            if rt is None:
                raise ExecError(f"No task {task_id} in revision {rev}.", 404)
            att = self.attempts.get(rt.attempt_ids[-1]) if rt.attempt_ids else None
            if att is None or att.status in TERMINAL_STATUSES:
                raise ExecError(f"Task {task_id} is {rt.state}; nothing to stop.")
            if att.task_stop_requested:
                return rt
            att.task_stop_requested = True
            rt.state = "canceling"
            self.log("cancel", f"Stop requested for {task_id} on {att.robot_id}; waiting for the robot to confirm.",
                     attempt_id=att.attempt_id)
            await self.gateway.cancel(self.run_id, att.attempt_id)
            return rt

    def _check_cancel_settled(self, m: Mission) -> None:
        if m.status != "canceling":
            return
        if any(a.status not in TERMINAL_STATUSES for a in self.attempts.values()):
            return
        m.status = "canceled"
        m.outcome = {"status": "canceled", "reason": "Operator canceled; all active tasks confirmed terminal."}
        self.log("cancel", "All active tasks confirmed terminal; mission canceled.")

    # ============================================================ scheduler
    def _active_plan(self, m: Mission) -> Optional[ExecutionPlan]:
        if m.active_revision is None:
            return None
        return m.revisions[m.active_revision - 1]

    def _robot_busy(self, rid: str) -> bool:
        return any(a.robot_id == rid and a.status not in TERMINAL_STATUSES for a in self.attempts.values())

    def _precheck(self, m: Mission, plan: ExecutionPlan, task) -> Optional[str]:
        r = self.robots.get(task.robot_id)
        if r is None or not r.available or r.health != "ok":
            return f"{task.robot_id} unavailable"
        remaining = sum(SKILLS[t.skill].energy_pct for t in plan.tasks if t.robot_id == task.robot_id
                        and m.tasks[f"{plan.revision}:{t.task_id}"].state in ("pending",))
        if r.battery_pct - remaining < BATTERY_RESERVE_PCT:
            return (f"{task.robot_id} battery {r.battery_pct:.1f}% < remaining {remaining:.1f}% + "
                    f"{BATTERY_RESERVE_PCT:.0f}% reserve")
        a = task.action
        if a.kind == "navigate_to" and a.location_id != r.location_id:
            route = route_between(r.location_id, a.location_id)
            if route is None:
                return f"no route from {r.location_id} to {a.location_id}"
            if self.world.route_status(route.route_id) != "clear":
                return f"{route.route_id} not observed clear"
        if a.kind == "pick_object":
            b = self.world.objects.get(a.object_id)
            if b is None or b.held_by or b.location_id != r.location_id:
                return f"{a.object_id} not observed at {r.location_id}"
            if r.carried_object:
                return f"{task.robot_id} already holds {r.carried_object}"
        if a.kind == "place_object" and r.carried_object != a.object_id:
            return f"{task.robot_id} does not hold {a.object_id}"
        return None

    async def _tick(self) -> None:
        m = self.mission
        if m is None or m.status != "executing":
            return
        plan = self._active_plan(m)
        if not self.gateway.info().connected:
            self._block(m, "Robot transport is disconnected; new work is blocked until a coordinated reset.")
            return
        await self.refresh_status()
        for task in plan.tasks:
            rt = m.tasks[f"{plan.revision}:{task.task_id}"]
            if rt.state != "pending":
                continue
            deps_ok = all(m.tasks[f"{plan.revision}:{d}"].state == "succeeded" for d in task.depends_on)
            if not deps_ok or self._robot_busy(task.robot_id):
                continue
            problem = self._precheck(m, plan, task)
            if problem:
                rt.state, rt.error, rt.finished_at = "failed", {"code": PRE_DISPATCH_FAIL, "message": problem}, self.clock()
                self.log("dispatch", f"{task.task_id} not dispatched: {problem}")
                await self._on_task_failure(m, rt, "failed")
                return
            attempt_id = f"{plan.revision}.{task.task_id}.a{len(rt.attempt_ids) + 1}-{uuid.uuid4().hex[:4]}"
            params = task.action.model_dump(exclude={"kind"}, exclude_none=True)
            env = TaskEnvelope(run_id=self.run_id, mission_id=m.mission_id, revision=plan.revision,
                               task_id=task.task_id, attempt_id=attempt_id, robot_id=task.robot_id,
                               skill=task.skill, params=params, timeout_s=SKILLS[task.skill].timeout_s)
            att = Attempt(attempt_id, plan.revision, task.task_id, task.robot_id, env.model_dump(),
                          submitted_at=self.clock())
            self.attempts[attempt_id] = att
            rt.attempt_ids.append(attempt_id)
            att.command_file = self._record_command(att.envelope, len(rt.attempt_ids))
            rt.state, rt.started_at = "dispatched", self.clock()
            self.robots[task.robot_id].current_attempt_id = attempt_id
            self.log("dispatch", f"{task.task_id} -> {task.robot_id}: {task.skill} {params}", attempt_id=attempt_id)
            receipt = await self.gateway.submit(env)
            if att.status in TERMINAL_STATUSES:
                continue  # events already settled this attempt while the receipt was in flight
            if not receipt.accepted_for_delivery:
                status = "uncertain" if receipt.delivery_unknown else "failed"
                att.status = status
                rt.state = status
                rt.error = {"code": "delivery_unknown" if receipt.delivery_unknown else "submit_failed",
                            "message": receipt.detail}
                self.robots[task.robot_id].current_attempt_id = None
                self.log("dispatch", f"{task.task_id} submit {'not confirmed' if receipt.delivery_unknown else 'failed'}:"
                         f" {receipt.detail}")
                await self._on_task_failure(m, rt, status)
                return
        # The stage ends once: every task of the active revision is done AND no attempt of any revision is
        # still live (a revision replaced mid-stage leaves its dispatched tasks running; their results count).
        if all(m.tasks[f"{plan.revision}:{t.task_id}"].state in TERMINAL_STATUSES | {"skipped", "superseded"}
               for t in plan.tasks) and not any(a.status not in TERMINAL_STATUSES for a in self.attempts.values()):
            await self._finish_revision(m, plan)

    def _record_command(self, envelope: dict, attempt_no: int) -> Optional[str]:
        """Write the envelope exactly as it goes out; a full disk must never stop dispatch."""
        try:
            path = write_command(self.runs_dir, envelope, attempt_no)
        except OSError as exc:
            self.log("agent_command", f"Could not record the command for {envelope['task_id']}: {exc}")
            return None
        if path is None:
            return None
        return Path(os.path.relpath(path, self.runs_dir.parent)).as_posix()

    # ======================================================= event intake
    async def handle_event(self, ev: TaskEvent) -> None:
        async with self.lock:
            await self._handle_event(ev)

    async def _handle_event(self, ev: TaskEvent) -> None:
        if ev.run_id != self.run_id:
            self.log("event_discarded", f"Discarded event {ev.event_id} from old run {ev.run_id}.")
            return
        if ev.event_id in self.seen_events:
            self.log("event_duplicate", f"Duplicate event {ev.event_id} ignored.")
            return
        att = self.attempts.get(ev.attempt_id)
        if att is None:
            self.log("event_discarded", f"Event {ev.event_id} for unknown attempt {ev.attempt_id}.")
            return
        if ev.seq <= att.last_seq:
            self.log("event_stale", f"Out-of-order event {ev.event_id} (seq {ev.seq} <= {att.last_seq}) ignored.")
            return
        if att.status in TERMINAL_STATUSES:
            if att.status == "uncertain" and ev.status in ("succeeded", "failed", "canceled") and not att.effects_applied:
                # Credible late result for an uncertain attempt: reconcile effects, never advance the plan.
                self.seen_events.add(ev.event_id)
                self.raw_events.append(ev.model_dump())
                att.last_seq = ev.seq
                self._apply_effects(att, ev)
                self.log("reconcile", f"Late {ev.status} for uncertain attempt {att.attempt_id} reconciled into "
                         "the observed world; the mission stays blocked.")
                return
            self.log("event_late", f"Event {ev.event_id} after terminal status {att.status} ignored.")
            return
        self.seen_events.add(ev.event_id)
        self.raw_events.append(ev.model_dump())
        att.last_seq = ev.seq
        att.status = ev.status
        m = self.mission
        rt = m.tasks.get(f"{att.revision}:{att.task_id}") if m else None
        if rt:
            rt.progress = ev.progress
            if ev.status in ("accepted", "running") and not att.task_stop_requested:
                rt.state = ev.status  # a stopped task keeps showing "canceling" until confirmed
        if ev.status in ("running",):
            return
        if ev.status == "accepted":
            self.log("event", f"{att.robot_id} accepted {att.task_id}.")
            return

        # terminal
        robot = self.robots.get(att.robot_id)
        if robot and robot.current_attempt_id == att.attempt_id:
            robot.current_attempt_id = None
        self._apply_effects(att, ev)
        if rt:
            rt.state = ev.status
            rt.result, rt.finished_at = ev.result, self.clock()
            rt.error = ev.error.model_dump() if ev.error else None
            if ev.status == "canceled" and att.task_stop_requested and rt.error is None:
                rt.error = {"code": "operator_stop", "message": f"Operator stopped {att.task_id}; robot confirmed."}
        self.log("event", f"{att.robot_id} {ev.status} {att.task_id}"
                 + (f": {ev.error.code} - {ev.error.message}" if ev.error else ""),
                 attempt_id=att.attempt_id, result=ev.result)
        if m is None:
            return
        if m.status == "canceling":
            self._check_cancel_settled(m)
            return
        if ev.status == "canceled" and att.stopped_after is not None and rt is not None:
            # A task the executive stopped because another one failed: recorded, never a failure of its own.
            m.history.append(f"rev {rt.revision}: {rt.task_id} on {rt.robot_id} canceled "
                             f"(stopped after {att.stopped_after})")
            if m.status == "recovering":
                await self._maybe_recover(m)
            return
        obsolete = m.active_revision != att.revision or m.status not in ("executing", "recovering")
        if obsolete:
            self.log("reconcile", f"Late result for revision {att.revision} reconciled without advancing the plan.")
            fault = rt is not None and (ev.status in ("failed", "rejected", "uncertain")
                                        or (ev.status == "canceled" and not att.task_stop_requested))
            if m.status == "recovering" and fault:
                # A task a replaced revision left running failed while the stage settles: the same path as a
                # failure of the active revision (history line; uncertain blocks; first fault stays the failure).
                await self._on_task_failure(m, rt, "failed" if ev.status == "canceled" else ev.status)
            elif m.status == "recovering":
                await self._maybe_recover(m)
            elif m.status in ("planning", "awaiting_approval") and fault:
                # Recovery (or the next stage) is already being planned; this result still counts. An uncertain
                # pick/place blocks: the planning in flight is discarded and no pending revision can be approved.
                status = "failed" if ev.status == "canceled" else ev.status
                m.history.append(self._failure_line(self._active_plan(m), rt, status))
                if status == "uncertain":
                    self._gen += 1
                    self._block(m, self._uncertain_reason(rt))
                else:
                    self.log("failure", f"rev {rt.revision} {rt.task_id} {status} after its stage ended; recorded.")
            elif m.status == "executing" and rt is not None and att.task_stop_requested \
                    and ev.status == "canceled":
                # The operator stopped a task a replaced revision left running: not a fault, no recovery.
                m.history.append(f"rev {rt.revision} (replaced by rev {m.active_revision}): {rt.task_id} on "
                                 f"{rt.robot_id} stopped by the operator")
                self.log("cancel", f"{rt.task_id} of replaced revision {rt.revision} on {rt.robot_id} stopped "
                                   "by the operator; the active revision continues.")
                await self._tick()
            elif m.status == "executing" and rt is not None and ev.status in ("failed", "rejected", "uncertain",
                                                                              "canceled"):
                # A task a replaced revision left running failed: same failure path as the active revision
                # (one bounded recovery); an unrequested cancel counts as a failure there too.
                await self._on_task_failure(m, rt, "failed" if ev.status == "canceled" else ev.status)
            elif m.status == "executing":
                await self._tick()  # its robot is free again and the stage-end check may now pass
            return
        if ev.status in ("failed", "rejected", "uncertain"):
            await self._on_task_failure(m, rt, ev.status)
            return
        if ev.status == "canceled" and att.task_stop_requested:
            # Same path as a failed task, except live siblings are left running.
            await self._on_task_failure(m, rt, "canceled", cancel_siblings=False)
            return
        if ev.status == "canceled":
            await self._on_task_failure(m, rt, "failed")
            return
        if att.task_stop_requested:
            self.log("cancel", f"{att.task_id} reached {ev.status} before the stop landed; result kept.")
        if m.status == "recovering":
            # A sibling settled after the failure (e.g. it passed its commit point before the cancel
            # landed). _tick() only runs while executing, so re-check whether recovery can start.
            await self._maybe_recover(m)
            return
        await self._tick()

    def _apply_effects(self, att: Attempt, ev: TaskEvent) -> None:
        """Apply effects from accepted terminal results exactly once per attempt."""
        if att.effects_applied:
            return
        if ev.status != "uncertain":
            att.effects_applied = True
        robot = self.robots.get(att.robot_id)
        # Battery comes from robot telemetry (gateway status), which already reflects the work;
        # energy_used_pct is recorded for the timeline only so completed work is never charged twice.
        for obs in ev.observations:
            rec = self.world.accept(obs, att.robot_id, att.attempt_id)
            self.log("observation", f"{att.robot_id} reported {obs.kind} {obs.entity_id}", obs_id=rec.obs_id)
        if ev.status != "succeeded" or robot is None:
            if ev.result and robot and ev.result.get("location_id"):
                robot.location_id = ev.result["location_id"]
            return
        skill = att.envelope["skill"]
        params = att.envelope["params"]
        res = ev.result or {}
        if skill == "navigate_to":
            robot.location_id = res.get("location_id", params["location_id"])
            if robot.carried_object:
                self.world.set_possession(robot.carried_object, robot.robot_id, robot.location_id,
                                          robot.robot_id, att.attempt_id)
        elif skill == "pick_object" and res.get("held"):
            robot.carried_object = res["object_id"]
            self.world.set_possession(res["object_id"], robot.robot_id, robot.location_id, robot.robot_id, att.attempt_id)
            self.log("effect", f"Possession confirmed: {robot.robot_id} holds {res['object_id']}.")
        elif skill == "place_object" and res.get("released"):
            robot.carried_object = None
            self.world.set_possession(res["object_id"], None, res["location_id"], robot.robot_id, att.attempt_id)
            self.log("effect", f"Placement confirmed: {res['object_id']} at {res['location_id']}.")

    # ===================================================== failure/recovery
    async def _on_task_failure(self, m: Mission, rt: TaskRuntime, status: str, cancel_siblings: bool = True) -> None:
        plan = self._active_plan(m)
        if m.status == "recovering":
            # A failure already ended this stage and is the recovery failure; a later one is history only (an
            # uncertain pick/place still blocks: no automatic retry of it).
            if status == "uncertain":
                m.outcome = {"status": "blocked", "reason": self._uncertain_reason(rt)}
            if status != "canceled" and (self._stage_failed is not None
                                         and (self._stage_failed.error or {}).get("code") == "operator_stop"):
                # A real fault outranks the operator stop that ended the stage: it becomes the recovery failure
                # (the stop stays a history line).
                self._stage_failed = rt
            m.history.append(self._failure_line(plan, rt, status))
            self.log("failure", f"{rt.task_id} {status} while the stage settles; {self._failed_label(plan)} is "
                                "the recovery failure.")
            if cancel_siblings:  # e.g. after an operator stop of one task the others kept running: settle them now
                await self._stop_siblings(rt, status)
            await self._maybe_recover(m)
            return
        self._stage_failed = rt
        self._abandon_live(m)  # the stage ended; recovery planning sees any delivered feedback
        for other in m.tasks.values():
            if other.revision == plan.revision and other.state == "pending":
                other.state = "skipped"
        plan.status = "finished"
        if status == "uncertain":
            m.status = "recovering"
            m.outcome = {"status": "blocked", "reason": self._uncertain_reason(rt)}
        else:
            m.status = "recovering"
        self.log("failure", f"{rt.task_id} {status}; new dispatch stopped, "
                 + ("settling active tasks." if cancel_siblings else "other active tasks keep running."))
        m.history.append(self._failure_line(plan, rt, status))
        if cancel_siblings:
            await self._stop_siblings(rt, status)
        await self._maybe_recover(m, failed=rt)

    async def _stop_siblings(self, rt: TaskRuntime, status: str) -> None:
        """Cancel every live attempt (all belong to this stage: a stage ends only once none is live, including
        tasks a replaced revision left running), marked so their "canceled" is recorded, not taken as a failure.
        One the operator is already stopping keeps its own meaning (operator_stop)."""
        for a in self.attempts.values():
            if a.status in TERMINAL_STATUSES or a.stopped_after is not None:
                continue
            if not a.task_stop_requested:
                failed = rt.task_id if a.revision == rt.revision else f"rev {rt.revision} {rt.task_id}"
                a.stopped_after = f"{failed} {status}"
            await self.gateway.cancel(self.run_id, a.attempt_id)

    @staticmethod
    def _uncertain_reason(rt: TaskRuntime) -> str:
        return (f"{rt.task_id} on {rt.robot_id} has an uncertain outcome; no automatic retry of pick/place. "
                "Manual reconciliation or reset required.")

    def _failed_label(self, plan: ExecutionPlan) -> str:
        f = self._stage_failed
        return f"rev {f.revision} {f.task_id}" if f is not None else f"rev {plan.revision}"

    @staticmethod
    def _failure_line(plan: ExecutionPlan, rt: TaskRuntime, status: str) -> str:
        code = (rt.error or {}).get("code", "")
        if rt.revision == plan.revision:
            return f"rev {plan.revision}: {rt.task_id} {status} ({code})"
        return (f"rev {rt.revision} (replaced by rev {plan.revision}): {rt.task_id} on {rt.robot_id} "
                f"{status} ({code})")

    async def _maybe_recover(self, m: Mission, failed: Optional[TaskRuntime] = None) -> None:
        if any(a.status not in TERMINAL_STATUSES for a in self.attempts.values()):
            return  # wait for settle; called again on the next terminal event
        if m.status != "recovering":
            return
        if m.outcome and m.outcome.get("status") == "blocked":
            m.status = "blocked"
            self._close_messages(m, "mission blocked")
            self.log("blocked", m.outcome["reason"])
            return
        if m.recovery_used:
            self._block(m, "Recovery already used for this mission; no further automatic proposals.")
            return
        m.recovery_used = True
        rt = failed or self._stage_failed or next(
            (t for t in reversed(list(m.tasks.values())) if t.state in ("failed", "rejected")
             or (t.error or {}).get("code") == "operator_stop"), None)
        self._stage_failed = None
        failure = {"task_id": rt.task_id if rt else None, "robot_id": rt.robot_id if rt else None,
                   "error": rt.error if rt else None,
                   "completed_effects": {r.robot_id: {"location_id": r.location_id, "carrying": r.carried_object}
                                         for r in self.robots.values()}}
        self.log("recovery", "Requesting one bounded recovery proposal.", failure=failure)
        self._spawn_planning("recovery", "recovery", failure=failure)

    def _spawn_planning(self, phase: str, kind: str, live: bool = False, **kw: Any) -> None:
        gen = self._gen

        async def run():
            try:
                await self._plan(phase, kind, live=live, gen=gen, **kw)
            except Exception as exc:  # noqa: BLE001
                if self.mission:
                    fail = self._live_failed if live else self._planner_failed
                    fail(self.mission, gen, f"{type(exc).__name__}: {exc}", None, 0)
            finally:
                if live and self._live is asyncio.current_task():
                    self._live = None
        task = asyncio.create_task(run())
        if live:
            self._live = task
        else:
            self._planning = task

    def _ask(self, m: Mission, question: str, options: list[str], phase: str, **data: Any) -> None:
        """Put a question to the operator, or end the mission once the clarify budget is spent."""
        if len(m.clarifications) >= MAX_CLARIFICATIONS:
            return self._block(m, CLARIFY_CAP_REASON)
        opts = list(options)[:4]
        m.status = "clarification"
        m.clarification_question = question
        m.clarifications = [*m.clarifications, {"question": question, "options": opts, "answer": None,
                                                "phase": phase}]
        self.log("clarification", question, **({"options": opts} if opts else {}), **data)

    def _block(self, m: Mission, reason: str) -> None:
        self._abandon_live(m)
        m.status = "blocked"
        m.outcome = {"status": "blocked", "reason": reason}
        self._close_messages(m, "mission blocked")
        self.log("blocked", reason)

    # ========================================================== evaluation
    def goal_satisfied(self, g: Goal, since: float) -> bool:
        if g.kind == "object_at":
            return any(b.label == g.object_query.label and b.location_id == g.location_id and b.held_by is None
                       and b.source in ROBOTS for b in self.world.objects.values())
        if g.kind == "object_located":
            # A fresh fleet report made during the mission settles a find goal, found or absent;
            # a prior survey (operator_seed) or an older report does not.
            return bool(self._located(g.object_query.label, g.area_id, since)) or \
                self._searched(g.area_id, g.object_query.label, since) is not None
        if g.kind == "route_observed":
            return self.world.route_status(g.route_id) is not None
        if g.kind == "image_captured":
            return self.world.image_for(g.area_id, since) is not None
        return False

    def _located(self, label: str, area_id: str, since: float) -> list:
        """Fresh beliefs of ``label`` in ``area_id`` reported by a fleet robot at or after ``since``."""
        return [b for b in self.world.instances(label, area_id) if b.source in ROBOTS and b.observed_at >= since]

    def _searched(self, area_id: str, label: str, since: float):
        """The fresh fleet absence report for ``label`` covering ``area_id``, made at or after ``since``."""
        for (rec_area, rec_label), rec in self.world.absences.items():
            if rec_label == label and rec.source in ROBOTS and rec.observed_at >= since \
                    and self.world.is_fresh(rec.fresh_until) \
                    and (rec_area == area_id or area_contains(rec_area, area_id)):
                return rec
        return None

    def _completion_reason(self, goals: list[Goal], since: float) -> str:
        """Names what find goals found (from beliefs); other goals keep the generic sentence."""
        parts = []
        for g in goals:
            if g.kind != "object_located":
                continue
            label, area = g.object_query.label, g.area_id
            found = self._located(label, area, since)
            if found:
                where = [f"{b.object_id} held by {b.held_by}" if b.held_by else f"{b.object_id} in {b.location_id}"
                         for b in found]
                parts.append("Found " + ", ".join(where) + ".")
            else:
                rec = self._searched(area, label, since)
                parts.append(f"Searched {rec.area_id if rec else area}: no {label} found.")
        if not parts:
            return "All mission goals satisfied by confirmed results."
        if any(g.kind != "object_located" for g in goals):
            parts.append("All mission goals satisfied by confirmed results.")
        return " ".join(parts)

    async def _finish_revision(self, m: Mission, plan: ExecutionPlan) -> None:
        self._abandon_live(m)  # a live replan of a finished stage is moot; its messages reach the next step
        plan.status = "finished"
        m.status = "evaluating"
        m.history.append(f"rev {plan.revision} ({plan.proposal.scope}) finished: {plan.proposal.summary}")
        goals = m.canonical_goals or []
        if goals and all(self.goal_satisfied(g, m.created_at) for g in goals):
            m.status = "completed"
            m.outcome = {"status": "completed", "reason": self._completion_reason(goals, m.created_at)}
            self._close_messages(m, "mission completed")
            self.log("completed", m.outcome["reason"])
            return
        for g in goals:
            if g.kind in ("object_at", "object_located"):
                label = g.object_query.label
                inst = self.world.instances(label)
                held = [b for b in inst if b.held_by]
                if g.kind == "object_at" and not inst and self.world.absence("work_area", label):
                    return self._block(m, f"Inspection found no {label} in the work area.")
                if len(inst) > 1 and not held:
                    ids = [b.object_id for b in inst]
                    return self._ask(m, f"Several {label} instances were observed ({', '.join(ids)}). Which one?",
                                     ids, "evaluation")
            if g.kind == "object_at" and self.world.route_status("pickup_corridor") == "blocked":
                return self._block(m, "The pickup corridor is observed blocked; transport cannot proceed.")
        if m.stage_count >= MAX_STAGES:
            return self._block(m, f"Stage limit ({MAX_STAGES}) reached without satisfying the goals.")
        self.log("evaluation", "Stage evidence accepted; requesting next stage.")
        self._spawn_planning("continuation", "continuation")

    # ============================================================ views
    def _serialize_plan(self, m: Mission, p: ExecutionPlan) -> dict:
        d = p.model_dump()
        d["runtime"] = {t.task_id: asdict(m.tasks[f"{p.revision}:{t.task_id}"]) for t in p.tasks
                        if f"{p.revision}:{t.task_id}" in m.tasks}
        for t, td in zip(p.tasks, d["tasks"]):
            td["agent_command"] = self._agent_command(m, p, t)
        return d

    def _agent_command(self, m: Mission, p: ExecutionPlan, task: AllocatedTask) -> dict:
        """What the instance agent gets for this task: the latest envelope sent, or (before dispatch) the
        one-line form of what would be sent."""
        rt = m.tasks.get(f"{p.revision}:{task.task_id}")
        att = self.attempts.get(rt.attempt_ids[-1]) if rt and rt.attempt_ids else None
        if att is not None:
            env = att.envelope
            return {"line": describe_command(env["skill"], env["params"]), "envelope": env,
                    "attempt_id": att.attempt_id, "attempt": len(rt.attempt_ids), "file": att.command_file}
        params = task.action.model_dump(exclude={"kind"}, exclude_none=True)
        return {"line": describe_command(task.skill, params), "envelope": None, "attempt_id": None,
                "attempt": None, "file": None}

    def state(self) -> dict:
        m = self.mission
        gi = self.gateway.info()
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "faults": self.faults,
            "sim": dict(self.sim),
            "gateway": asdict(gi),
            "planners": {k: p.status() for k, p in self.planners.items()},
            "default_planner": self.default_planner,
            "catalog": catalog_dict(),
            "robots": [dict(ROBOTS[rid].as_dict(), state=asdict(s)) for rid, s in self.robots.items()],
            "world": self.world.snapshot(),
            "mission": None if m is None else {
                "mission_id": m.mission_id, "command": m.command, "planner_kind": m.planner_kind,
                "status": m.status, "created_at": m.created_at,
                "canonical_goals": [g.model_dump() for g in m.canonical_goals] if m.canonical_goals else None,
                "active_revision": m.active_revision, "stage_count": m.stage_count,
                "recovery_used": m.recovery_used, "clarification_question": m.clarification_question,
                "clarifications": [dict(c) for c in m.clarifications],
                "outcome": m.outcome, "history": m.history, "planning_log": m.planning_log,
                "last_proposal": m.last_proposal,
                "operator_messages": [dict(x) for x in m.operator_messages],
                "revisions": [self._serialize_plan(m, p) for p in m.revisions],
            },
            "timeline": self.timeline[-300:],
        }

    def export(self) -> dict:
        s = self.state()
        s.pop("catalog", None)
        return {
            "schema_version": RUN_EXPORT_SCHEMA,
            "exported_at": self.clock(),
            "catalog": catalog_dict(),
            **s,
            "timeline": self.timeline,
            "attempts": [asdict(a) for a in self.attempts.values()],
            "events": self.raw_events,
        }
