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
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from .allocator import Allocator, CapabilityAllocator
from .catalog import BATTERY_RESERVE_PCT, ROBOTS, SKILLS, catalog_dict, route_between
from .context import ContextBudgetExceeded, build_context
from .contracts import (
    RUN_EXPORT_SCHEMA,
    TERMINAL_STATUSES,
    ExecutionPlan,
    Goal,
    Observation,
    PlannerRecord,
    PlanProposal,
    TaskEnvelope,
    TaskEvent,
    TaskSpec,
)
from .gateway.base import RobotGateway
from .planner.base import Planner, PlannerError
from .validator import ValidationResult, validate
from .world import ObservedWorld, RobotState, initial_robot_states

TERMINAL_MISSION = {"completed", "blocked", "rejected", "canceled", "planner_error"}
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
    state: str = "pending"  # pending|dispatched|accepted|running|succeeded|failed|rejected|canceled|uncertain|skipped
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
    outcome: Optional[dict] = None
    history: list[str] = field(default_factory=list)
    planning_log: list[dict] = field(default_factory=list)
    last_proposal: Optional[dict] = None


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Executive:
    def __init__(self, gateway: RobotGateway, planners: dict[str, Planner], allocator: Optional[Allocator] = None,
                 clock: Callable[[], float] = time.time, default_planner: str = "qwen"):
        self.gateway = gateway
        self.planners = planners
        self.allocator = allocator or CapabilityAllocator()
        self.clock = clock
        self.default_planner = default_planner
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
        self._gen = 0  # bumps on reset/new mission; stale async results are discarded

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

    async def refresh_status(self) -> None:
        status = await self.gateway.status()
        for rid, st in status.items():
            r = self.robots.get(rid)
            if r is None:
                continue
            r.battery_pct = float(st.get("battery_pct", r.battery_pct))
            r.health = st.get("health", r.health)
            r.available = bool(st.get("available", r.available))
            r.last_update = self.clock()

    # ================================================================ reset
    async def reset(self, scenario: str = "unknown", faults: Optional[list[str]] = None) -> dict:
        """Coordinated reset. Settles old work first; prior-run events are discarded afterwards."""
        self._gen += 1
        if self._planning and not self._planning.done():
            self._planning.cancel()
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
                await self.gateway.reset(self.run_id, self.faults)
            except Exception as exc:  # noqa: BLE001
                self.log("reset", f"Coordinated reset failed: {exc}")
                raise ExecError(f"Coordinated reset failed: {exc}", 503) from exc
            self.world = ObservedWorld(self.clock)
            self.robots = initial_robot_states(self.clock)
            await self.refresh_status()
            self.mission = None
            self.attempts.clear()
            self.raw_events.clear()
            self.timeline.clear()
            self.log("reset", f"New run {self.run_id} (scenario={scenario}, faults={self.faults or 'none'}).")
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
        planner_kind = planner_kind or self.default_planner
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
        phase = "continuation" if m.canonical_goals else "clarification_answer"
        await self._plan(phase, "clarification", clarification={"question": m.clarification_question or "",
                                                                 "answer": answer})

    async def replan(self) -> None:
        m = self._require_mission()
        if m.status not in ("awaiting_approval", "planner_error"):
            raise ExecError("Re-planning is only available while awaiting approval or after a planner error.")
        if m.status == "planner_error":
            m.status = "planning"
        phase = "continuation" if m.canonical_goals else "initial"
        await self._plan(phase, "continuation" if m.canonical_goals else "initial")

    def _require_mission(self) -> Mission:
        if self.mission is None:
            raise ExecError("No active mission.", 404)
        return self.mission

    # ============================================================= planning
    async def _plan(self, phase: str, rev_kind: str, failure: Optional[dict] = None,
                    clarification: Optional[dict] = None) -> None:
        m = self._require_mission()
        gen = self._gen
        planner = self.planners[m.planner_kind]
        prev_status = m.status
        m.status = "planning"
        ctx = build_context(phase=phase, command=m.command, robots=self.robots, world=self.world,
                            canonical_goals=m.canonical_goals, stage_history=m.history,
                            failure=failure, clarification=clarification)
        self.log("planning", f"Requesting {phase} proposal from {planner.kind}.")
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
                return self._planner_failed(m, gen, str(exc), None, attempts)
            except PlannerError as exc:
                record = exc.record
                raw = getattr(exc, "raw", None)
                if raw is not None and attempts < 2:  # malformed output: one repair attempt
                    first_ok = False
                    feedback = (record.errors if record and record.errors else [str(exc)])
                    self.log("planning", f"Proposal rejected by schema; repair attempt. ({exc})")
                    continue
                return self._planner_failed(m, gen, str(exc), record, attempts)
            if gen != self._gen:
                return
            record = out.record
            if out.proposal.disposition != "plan":
                first_ok = first_ok if first_ok is not None else True
                break
            alloc = self.allocator.allocate(out.proposal, self.robots, {})
            vres = validate(out.proposal, alloc, self.robots, self.world, m.canonical_goals)
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
            self._apply_proposal(m, out.proposal, record, vres, alloc, phase, rev_kind)

    def _planner_failed(self, m: Mission, gen: int, message: str, record: Optional[PlannerRecord], attempts: int):
        if gen != self._gen:
            return
        m.planning_log.append({"phase": "error", "planner": record.model_dump() if record else None,
                               "error": message, "attempts": attempts})
        m.status = "planner_error"
        m.outcome = {"status": "planner_error", "reason": message}
        self.log("planner_error", message)

    def _apply_proposal(self, m: Mission, proposal: PlanProposal, record: PlannerRecord,
                        vres: Optional[ValidationResult], alloc, phase: str, rev_kind: str) -> None:
        if proposal.disposition == "clarify":
            m.status = "clarification"
            m.clarification_question = proposal.clarification_question
            self.log("clarification", proposal.clarification_question or "", issues=proposal.issues)
            return
        if proposal.disposition == "reject":
            reason = proposal.summary + (f" ({'; '.join(proposal.issues)})" if proposal.issues else "")
            if phase == "initial" or phase == "clarification_answer":
                m.status, m.outcome = "rejected", {"status": "rejected", "reason": reason}
                self.log("rejected", f"Planner rejected the request: {reason}")
            else:
                self._block(m, f"No continuation: {reason}")
            return
        plan = self._make_revision(m, proposal, record, vres, alloc, rev_kind, {})
        if not plan.validation.ok and phase in ("continuation", "recovery"):
            reasons = "; ".join(f.message for f in plan.validation.findings if f.severity == "error")
            plan.status = "not_executable"
            self._block(m, f"{phase.capitalize()} proposal is not executable: {reasons}")
            return
        m.status = "awaiting_approval"

    def _make_revision(self, m: Mission, proposal: PlanProposal, record: PlannerRecord,
                       vres: ValidationResult, alloc, kind: str, constraints: dict[str, str]) -> ExecutionPlan:
        if m.canonical_goals is not None:
            proposal = proposal.model_copy(update={"mission_goals": list(m.canonical_goals)})
        for old in m.revisions:
            if old.status == "awaiting_approval" or old.status == "invalid":
                old.status = "superseded"
        evidence = sorted({e for b in self.world.objects.values() for e in b.evidence}
                          | {r.evidence for r in self.world.routes.values()})
        plan = ExecutionPlan(
            mission_id=m.mission_id, revision=len(m.revisions) + 1, kind=kind,
            proposal_hash=_hash(proposal.model_dump()), run_id=self.run_id, proposal=proposal,
            allocations=dict(alloc.robots), tasks=vres.tasks, operator_constraints=dict(constraints),
            evidence_ids=evidence, world_version=self.world.version, validation=vres.report,
            planner=record, created_at=self.clock(),
            status="awaiting_approval" if vres.report.ok else "invalid",
        )
        m.revisions.append(plan)
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
            if m.status != "awaiting_approval":
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
            plan.status = "executing"
            plan.approved_at = self.clock()
            plan.approved_by = "operator"
            if m.canonical_goals is None:
                m.canonical_goals = list(proposal.mission_goals)
            m.active_revision = revision
            m.status = "executing"
            if proposal.scope == "observation_stage" or plan.kind in ("continuation", "initial"):
                m.stage_count += 1
            for t in plan.tasks:
                m.tasks[f"{revision}:{t.task_id}"] = TaskRuntime(t.task_id, revision, t.robot_id)
            self.log("approval", f"Operator approved revision {revision} ({plan.proposal_hash}).")
            await self._tick()
            return plan

    async def reject(self, revision: int, reason: str = "") -> None:
        async with self.lock:
            m = self._require_mission()
            if m.status != "awaiting_approval":
                raise ExecError(f"Mission is {m.status}, not awaiting approval.")
            plan = self._latest(m, revision)
            plan.status = "rejected"
            m.status = "rejected"
            m.outcome = {"status": "rejected", "reason": f"Operator rejected revision {revision}. {reason}".strip()}
            self.log("approval", m.outcome["reason"])

    async def edit(self, revision: int, edits: list[dict]) -> ExecutionPlan:
        """Simple typed edits: set_robot, remove_task, set_param. Produces a new revision needing approval."""
        async with self.lock:
            m = self._require_mission()
            if m.status != "awaiting_approval":
                raise ExecError(f"Mission is {m.status}, not awaiting approval.")
            base = self._latest(m, revision)
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
            await self.refresh_status()
            alloc = self.allocator.allocate(proposal, self.robots, constraints)
            vres = validate(proposal, alloc, self.robots, self.world, m.canonical_goals, constraints)
            record = base.planner.model_copy(update={"label": base.planner.label + " + operator edit"})
            plan = self._make_revision(m, proposal, record, vres, alloc, "edit", constraints)
            self.log("operator", f"Edited revision {revision} -> {plan.revision}.", edits=edits)
            return plan

    async def cancel(self) -> None:
        async with self.lock:
            m = self._require_mission()
            if m.status in TERMINAL_MISSION:
                raise ExecError(f"Mission already {m.status}.")
            self._gen += 1  # discard in-flight planning results
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
        if all(m.tasks[f"{plan.revision}:{t.task_id}"].state in TERMINAL_STATUSES | {"skipped"} for t in plan.tasks):
            await self._finish_revision(m, plan)

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
            if ev.status in ("accepted", "running"):
                rt.state = ev.status
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
        self.log("event", f"{att.robot_id} {ev.status} {att.task_id}"
                 + (f": {ev.error.code} - {ev.error.message}" if ev.error else ""),
                 attempt_id=att.attempt_id, result=ev.result)
        if m is None:
            return
        if m.status == "canceling":
            self._check_cancel_settled(m)
            return
        obsolete = m.active_revision != att.revision or m.status not in ("executing", "recovering")
        if obsolete:
            self.log("reconcile", f"Late result for revision {att.revision} reconciled without advancing the plan.")
            if m.status == "recovering":
                await self._maybe_recover(m)
            return
        if ev.status in ("failed", "rejected", "uncertain"):
            await self._on_task_failure(m, rt, ev.status)
            return
        if ev.status == "canceled":
            await self._on_task_failure(m, rt, "failed")
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
    async def _on_task_failure(self, m: Mission, rt: TaskRuntime, status: str) -> None:
        plan = self._active_plan(m)
        for other in m.tasks.values():
            if other.revision == plan.revision and other.state == "pending":
                other.state = "skipped"
        plan.status = "finished"
        if status == "uncertain":
            m.status = "recovering"
            m.outcome = {"status": "blocked", "reason": f"{rt.task_id} on {rt.robot_id} has an uncertain outcome; "
                         "no automatic retry of pick/place. Manual reconciliation or reset required."}
        else:
            m.status = "recovering"
        self.log("failure", f"{rt.task_id} {status}; new dispatch stopped, settling active tasks.")
        for a in self.attempts.values():
            if a.status not in TERMINAL_STATUSES and a.revision == plan.revision:
                await self.gateway.cancel(self.run_id, a.attempt_id)
        m.history.append(f"rev {plan.revision}: {rt.task_id} {status} ({(rt.error or {}).get('code', '')})")
        await self._maybe_recover(m, failed=rt)

    async def _maybe_recover(self, m: Mission, failed: Optional[TaskRuntime] = None) -> None:
        if any(a.status not in TERMINAL_STATUSES for a in self.attempts.values()):
            return  # wait for settle; called again on the next terminal event
        if m.status != "recovering":
            return
        if m.outcome and m.outcome.get("status") == "blocked":
            m.status = "blocked"
            self.log("blocked", m.outcome["reason"])
            return
        if m.recovery_used:
            self._block(m, "Recovery already used for this mission; no further automatic proposals.")
            return
        m.recovery_used = True
        rt = failed or next((t for t in reversed(list(m.tasks.values())) if t.state in ("failed", "rejected")), None)
        failure = {"task_id": rt.task_id if rt else None, "robot_id": rt.robot_id if rt else None,
                   "error": rt.error if rt else None,
                   "completed_effects": {r.robot_id: {"location_id": r.location_id, "carrying": r.carried_object}
                                         for r in self.robots.values()}}
        self.log("recovery", "Requesting one bounded recovery proposal.", failure=failure)
        self._spawn_planning("recovery", "recovery", failure=failure)

    def _spawn_planning(self, phase: str, kind: str, **kw: Any) -> None:
        async def run():
            try:
                await self._plan(phase, kind, **kw)
            except Exception as exc:  # noqa: BLE001
                if self.mission:
                    self._planner_failed(self.mission, self._gen, f"{type(exc).__name__}: {exc}", None, 0)
        self._planning = asyncio.create_task(run())

    def _block(self, m: Mission, reason: str) -> None:
        m.status = "blocked"
        m.outcome = {"status": "blocked", "reason": reason}
        self.log("blocked", reason)

    # ========================================================== evaluation
    def goal_satisfied(self, g: Goal, since: float) -> bool:
        if g.kind == "object_at":
            return any(b.label == g.object_query.label and b.location_id == g.location_id and b.held_by is None
                       and b.source in ROBOTS for b in self.world.objects.values())
        if g.kind == "object_located":
            return bool(self.world.instances(g.object_query.label, g.area_id))
        if g.kind == "route_observed":
            return self.world.route_status(g.route_id) is not None
        if g.kind == "image_captured":
            return self.world.image_for(g.area_id, since) is not None
        return False

    async def _finish_revision(self, m: Mission, plan: ExecutionPlan) -> None:
        plan.status = "finished"
        m.status = "evaluating"
        m.history.append(f"rev {plan.revision} ({plan.proposal.scope}) finished: {plan.proposal.summary}")
        goals = m.canonical_goals or []
        if goals and all(self.goal_satisfied(g, m.created_at) for g in goals):
            m.status = "completed"
            m.outcome = {"status": "completed", "reason": "All mission goals satisfied by confirmed results."}
            self.log("completed", m.outcome["reason"])
            return
        for g in goals:
            if g.kind in ("object_at", "object_located"):
                label = g.object_query.label
                inst = self.world.instances(label)
                held = [b for b in inst if b.held_by]
                if not inst and self.world.absence("work_area", label):
                    return self._block(m, f"Inspection found no {label} in the work area.")
                if len(inst) > 1 and not held:
                    m.status = "clarification"
                    ids = ", ".join(b.object_id for b in inst)
                    m.clarification_question = f"Several {label} instances were observed ({ids}). Which one?"
                    self.log("clarification", m.clarification_question)
                    return
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
        return d

    def state(self) -> dict:
        m = self.mission
        gi = self.gateway.info()
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "faults": self.faults,
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
                "outcome": m.outcome, "history": m.history, "planning_log": m.planning_log,
                "last_proposal": m.last_proposal,
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
