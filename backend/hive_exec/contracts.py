"""Typed HIVE Executive contracts (Pydantic v2, strict, versioned).

Model-facing contract: ``PlanProposal`` (schema ``hive.plan-proposal.v1``). Its JSON
Schema is generated from these models and sent to vLLM as a structured-output
constraint. Everything else here is backend-owned: IDs, revisions, approval,
envelopes, events and trusted timestamps are never accepted from the model.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import LABEL_PATTERN, canonical_label

PLAN_PROPOSAL_SCHEMA = "hive.plan-proposal.v1"
TASK_ENVELOPE_SCHEMA = "hive.task-envelope.v1"
TASK_EVENT_SCHEMA = "hive.task-event.v1"
EXECUTION_PLAN_SCHEMA = "hive.execution-plan.v1"
RUN_EXPORT_SCHEMA = "hive.run-export.v1"

MAX_TASKS = 16

# Closed vocabularies for the first-delivery world. Keep in sync with catalog.py
# (a test asserts they match).
RobotId = Literal["camera_01", "lidar_01", "gripper_01"]
LocationId = Literal["camera_post", "lidar_post", "pickup_zone", "dropoff_zone", "work_area"]
AreaId = Literal["work_area", "pickup_zone", "dropoff_zone"]
RouteId = Literal["pickup_corridor"]
# Object labels are open vocabulary (lowercase snake_case); catalog.OBJECT_PROFILES says
# which labels have a trusted profile (mass known) and may therefore be carried.
ObjectLabel = Annotated[str, Field(pattern=LABEL_PATTERN)]
SkillKind = Literal[
    "navigate_to", "inspect_area", "capture_image", "scan_route", "pick_object", "place_object"
]

ObjectId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}_[0-9]{1,3}$")]
TaskId = Annotated[str, Field(pattern=r"^t[0-9]{1,2}$")]
BindingId = Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=280)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class ObjectQuery(Strict):
    label: ObjectLabel

    # The one place labels are canonicalised (aliases such as red_box -> red_block). It runs
    # before the pattern check, so hashes, validation, matching and envelopes see canonical labels.
    @field_validator("label", mode="before")
    @classmethod
    def _canonical(cls, v: Any) -> Any:
        return canonical_label(v) if isinstance(v, str) else v


# --------------------------------------------------------------------- actions


class NavigateTo(Strict):
    kind: Literal["navigate_to"]
    location_id: LocationId
    route_id: Optional[RouteId] = None


class InspectArea(Strict):
    kind: Literal["inspect_area"]
    area_id: AreaId
    object_query: ObjectQuery


class CaptureImage(Strict):
    kind: Literal["capture_image"]
    area_id: AreaId


class ScanRoute(Strict):
    kind: Literal["scan_route"]
    route_id: RouteId


class _ObjectRef(Strict):
    object_id: Optional[ObjectId] = None
    object_query: Optional[ObjectQuery] = None

    @model_validator(mode="after")
    def _exactly_one_ref(self):
        if (self.object_id is None) == (self.object_query is None):
            raise ValueError("exactly one of object_id or object_query is required")
        return self


class PickObject(_ObjectRef):
    kind: Literal["pick_object"]


class PlaceObject(_ObjectRef):
    kind: Literal["place_object"]
    location_id: LocationId


Action = Annotated[
    Union[NavigateTo, InspectArea, CaptureImage, ScanRoute, PickObject, PlaceObject],
    Field(discriminator="kind"),
]

# ----------------------------------------------------------------------- goals


class ObjectAtGoal(Strict):
    kind: Literal["object_at"]
    object_query: ObjectQuery
    location_id: LocationId


class ObjectLocatedGoal(Strict):
    kind: Literal["object_located"]
    object_query: ObjectQuery
    area_id: AreaId


class RouteObservedGoal(Strict):
    kind: Literal["route_observed"]
    route_id: RouteId


class ImageCapturedGoal(Strict):
    kind: Literal["image_captured"]
    area_id: AreaId


Goal = Annotated[
    Union[ObjectAtGoal, ObjectLocatedGoal, RouteObservedGoal, ImageCapturedGoal],
    Field(discriminator="kind"),
]

# -------------------------------------------------------------------- proposal


class TaskSpec(Strict):
    task_id: TaskId
    binding_id: BindingId
    action: Action
    depends_on: list[TaskId] = Field(default_factory=list, max_length=MAX_TASKS)
    preferred_robot_id: Optional[RobotId] = None

    @model_validator(mode="after")
    def _unique_deps(self):
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on entries must be unique")
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        return self


PreviewText = Annotated[str, Field(min_length=1, max_length=160)]


class NextStagePreview(Strict):
    """The likely next stage of an observation stage, shown to the operator and NEVER run.

    It is not allocated, dispatched or approved with the revision. Its tasks may name objects by
    ``object_query`` because the ids are not known yet; task ids and dependencies live in the
    preview's own namespace. The backend only checks it to attach warning/info notes.
    """

    summary: ShortText
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=MAX_TASKS)
    # What evidence the next stage depends on ("camera_01 sighting of red_block").
    waits_for: list[PreviewText] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _own_ids(self):
        ids = [t.task_id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("preview task_id values must be unique")
        for t in self.tasks:
            for d in t.depends_on:
                if d not in ids:
                    raise ValueError(f"preview task {t.task_id} depends on unknown preview task {d}")
        return self


class PlanProposal(Strict):
    """What the planner (Qwen or Baseline) may propose. Nothing here is trusted."""

    schema_version: Literal["hive.plan-proposal.v1"]
    disposition: Literal["plan", "clarify", "reject", "answer"]
    scope: Optional[Literal["full", "observation_stage"]] = None
    summary: ShortText
    mission_goals: list[Goal] = Field(default_factory=list, max_length=4)
    stage_goals: list[Goal] = Field(default_factory=list, max_length=4)
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=MAX_TASKS)
    issues: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=8)
    # Why these robots, how a robot without sensors reaches its target, key assumptions. Optional so older
    # outputs stay valid; the validator notes a plan that moves a sensorless robot without one.
    rationale: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=4)
    clarification_question: Optional[ShortText] = None
    clarification_options: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=4)
    answer: Optional[Annotated[str, Field(min_length=1, max_length=600)]] = None
    # An observation stage may show what the next stage would likely be. Display only, never executable.
    next_stage_preview: Optional[NextStagePreview] = None
    # On a continuation: what the new evidence changed against the previous preview (model's own words;
    # the backend computes its own preview_diff and never trusts these).
    preview_changes: list[PreviewText] = Field(default_factory=list, max_length=4)

    @field_validator("next_stage_preview", mode="before")
    @classmethod
    def _empty_preview_is_none(cls, v):
        """A preview with no tasks says nothing the operator can read; drop it rather than fail the plan."""
        if isinstance(v, dict) and not v.get("tasks"):
            return None
        return v

    @field_validator("clarification_options", mode="before")
    @classmethod
    def _unique_options(cls, v):
        """Repeated options (ignoring case and edge spaces) are dropped, the first one kept."""
        if not isinstance(v, list):
            return v
        seen: set = set()
        out = []
        for opt in v:
            if isinstance(opt, str):
                key = opt.strip().casefold()
                if key in seen:
                    continue
                seen.add(key)
            out.append(opt)
        return out

    @model_validator(mode="after")
    def _disposition_rules(self):
        if self.disposition == "plan":
            if self.scope is None:
                raise ValueError("scope is required when disposition is plan")
            if not self.tasks:
                raise ValueError("a plan needs at least one task")
            if not self.mission_goals:
                raise ValueError("a plan needs at least one mission goal")
        else:
            if self.scope is not None:
                raise ValueError("scope must be null unless disposition is plan")
            if self.tasks:
                raise ValueError("clarify/reject/answer proposals cannot contain executable tasks")
        if self.disposition == "clarify" and not self.clarification_question:
            raise ValueError("clarify requires clarification_question")
        if self.disposition != "clarify" and self.clarification_question:
            raise ValueError("clarification_question only allowed for clarify")
        if self.disposition != "clarify" and self.clarification_options:
            raise ValueError("clarification_options only allowed for clarify")
        if self.disposition == "answer":
            if not self.answer:
                raise ValueError("answer requires answer text")
            if self.mission_goals or self.stage_goals:
                raise ValueError("an answer cannot carry goals")
        elif self.answer:
            raise ValueError("answer text only allowed for disposition answer")
        if self.next_stage_preview is not None and (self.disposition != "plan"
                                                    or self.scope != "observation_stage"):
            raise ValueError("next_stage_preview is only allowed for a plan with scope observation_stage")
        if self.preview_changes and self.disposition != "plan":
            raise ValueError("preview_changes is only allowed for disposition plan")
        ids = [t.task_id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task_id values must be unique")
        return self


def plan_proposal_json_schema() -> dict[str, Any]:
    return PlanProposal.model_json_schema()


def task_envelope_json_schema() -> dict[str, Any]:
    """Saved as schemas/task_envelope.v1.json: what an instance agent receives in envelope_json."""
    return TaskEnvelope.model_json_schema()


# ------------------------------------------------------- backend-owned models


class ValidationFinding(Strict):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    task_id: Optional[str] = None
    binding_id: Optional[str] = None


class ValidationReport(Strict):
    ok: bool
    findings: list[ValidationFinding] = Field(default_factory=list)
    # Notes about the next-stage preview only (warning/info, never errors): they never affect ``ok``.
    preview_findings: list[ValidationFinding] = Field(default_factory=list)
    world_version: int
    projected_energy_pct: dict[str, float] = Field(default_factory=dict)


class AllocatedTask(Strict):
    task_id: str
    binding_id: str
    robot_id: Optional[str]
    action: Action
    depends_on: list[str]
    skill: SkillKind


#: Planner kinds. "qwen" is the exec_v2 name of "llm", still accepted on input.
PLANNER_KIND_ALIASES = {"qwen": "llm"}


def normalize_planner_kind(kind: Optional[str]) -> Optional[str]:
    return PLANNER_KIND_ALIASES.get(kind, kind) if isinstance(kind, str) else kind


class PlannerRecord(Strict):
    """Provenance of a proposal. ``kind`` is never inferred: it states which planner ran.

    ``latency_ms`` is total planning time (the Executive overwrites it, repairs
    included); ``provider_latency_ms`` is the one HTTP call that answered.
    """

    kind: Literal["llm", "baseline"]
    label: str
    model: Optional[str] = None
    endpoint: Optional[str] = None
    prompt_version: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    latency_ms: Optional[float] = None
    attempts: int = 1
    repaired: bool = False
    first_pass_ok: Optional[bool] = None
    input_tokens_est: Optional[int] = None
    usage: Optional[dict[str, Any]] = None
    errors: list[str] = Field(default_factory=list)
    provider_id: Optional[str] = None
    provider_kind: Optional[str] = None
    fallback_used: bool = False
    failovers: list[str] = Field(default_factory=list)
    provider_latency_ms: Optional[float] = None

    @field_validator("kind", mode="before")
    @classmethod
    def _alias_kind(cls, value: Any) -> Any:
        return normalize_planner_kind(value)


class PreviewDiffItem(Strict):
    """One line of a preview diff. ``what`` is the actual task (or the previewed one for removed)."""

    preview_task: Optional[str] = None
    task: Optional[str] = None
    what: str
    was: Optional[str] = None  # changed: the previewed form
    differences: list[str] = Field(default_factory=list)  # changed: "location_id: pickup_zone -> dropoff_zone"


class PreviewDiff(Strict):
    """Backend-computed comparison of a continuation revision against the previous stage's preview."""

    previous_revision: int
    matches_preview: bool
    kept: list[PreviewDiffItem] = Field(default_factory=list)
    changed: list[PreviewDiffItem] = Field(default_factory=list)
    added: list[PreviewDiffItem] = Field(default_factory=list)
    removed: list[PreviewDiffItem] = Field(default_factory=list)


class ExecutionPlan(Strict):
    schema_version: Literal["hive.execution-plan.v1"] = EXECUTION_PLAN_SCHEMA
    mission_id: str
    revision: int
    kind: Literal["initial", "continuation", "recovery", "edit", "clarification", "feedback"]
    proposal_hash: str
    run_id: str
    proposal: PlanProposal
    allocations: dict[str, Optional[str]]
    tasks: list[AllocatedTask]
    operator_constraints: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    world_version: int
    validation: ValidationReport
    planner: PlannerRecord
    created_at: float
    status: Literal[
        "awaiting_approval", "approved", "rejected", "superseded", "executing", "finished",
        "invalid", "not_executable",
    ] = "awaiting_approval"
    approved_at: Optional[float] = None
    approved_by: Optional[str] = None
    # A continuation revision compared with the previous stage's preview (None when there was none).
    preview_diff: Optional[PreviewDiff] = None
    # An observation stage's preview is answered once: the number of the approved revision that compared
    # itself with it (set at approval; None while unanswered).
    preview_consumed_by: Optional[int] = None
    # Why a superseded revision can no longer run: "operator_message" (an operator message replaced it),
    # "replanned" (a newer revision replaced it) or "stage_ended" (its stage ended before approval); the
    # revision that replaced it, once one exists.
    superseded_reason: Optional[Literal["operator_message", "replanned", "stage_ended"]] = None
    superseded_by: Optional[int] = None


class TaskEnvelope(Strict):
    schema_version: Literal["hive.task-envelope.v1"] = TASK_ENVELOPE_SCHEMA
    run_id: str
    mission_id: str
    revision: int
    task_id: str
    attempt_id: str
    robot_id: str
    skill: SkillKind
    params: dict[str, Any]
    timeout_s: float = Field(gt=0, le=600)


TaskStatus = Literal[
    "accepted", "rejected", "running", "succeeded", "failed", "canceled", "uncertain"
]
TERMINAL_STATUSES = {"rejected", "succeeded", "failed", "canceled", "uncertain"}


class TaskError(Strict):
    code: str
    message: str


class Observation(Strict):
    """A report from a robot. ``observed_at`` is stamped by the backend on acceptance."""

    kind: Literal["object_seen", "object_absent", "route_clear", "route_blocked", "image"]
    entity_id: str
    area_id: Optional[str] = None
    route_id: Optional[str] = None
    location_id: Optional[str] = None
    label: Optional[str] = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class TaskEvent(Strict):
    schema_version: Literal["hive.task-event.v1"] = TASK_EVENT_SCHEMA
    event_id: str
    run_id: str
    attempt_id: str
    robot_id: str
    seq: int = Field(ge=0)
    status: TaskStatus
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    result: Optional[dict[str, Any]] = None
    error: Optional[TaskError] = None
    observations: list[Observation] = Field(default_factory=list)
    energy_used_pct: Optional[float] = Field(default=None, ge=0, le=100)
