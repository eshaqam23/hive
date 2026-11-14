"""Typed HIVE Executive contracts (Pydantic v2, strict, versioned).

Model-facing contract: ``PlanProposal`` (schema ``hive.plan-proposal.v1``). Its JSON
Schema is generated from these models and sent to vLLM as a structured-output
constraint. Everything else here is backend-owned: IDs, revisions, approval,
envelopes, events and trusted timestamps are never accepted from the model.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
ObjectLabel = Literal["red_block", "blue_block", "green_block"]
SkillKind = Literal[
    "navigate_to", "inspect_area", "capture_image", "scan_route", "pick_object", "place_object"
]

ObjectId = Annotated[str, Field(pattern=r"^[a-z_]{1,24}_[0-9]{1,3}$")]
TaskId = Annotated[str, Field(pattern=r"^t[0-9]{1,2}$")]
BindingId = Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=280)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class ObjectQuery(Strict):
    label: ObjectLabel


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


class PlanProposal(Strict):
    """What the planner (Qwen or Baseline) may propose. Nothing here is trusted."""

    schema_version: Literal["hive.plan-proposal.v1"]
    disposition: Literal["plan", "clarify", "reject"]
    scope: Optional[Literal["full", "observation_stage"]] = None
    summary: ShortText
    mission_goals: list[Goal] = Field(default_factory=list, max_length=4)
    stage_goals: list[Goal] = Field(default_factory=list, max_length=4)
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=MAX_TASKS)
    issues: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=8)
    clarification_question: Optional[ShortText] = None

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
                raise ValueError("clarify/reject proposals cannot contain executable tasks")
        if self.disposition == "clarify" and not self.clarification_question:
            raise ValueError("clarify requires clarification_question")
        if self.disposition != "clarify" and self.clarification_question:
            raise ValueError("clarification_question only allowed for clarify")
        ids = [t.task_id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task_id values must be unique")
        return self


def plan_proposal_json_schema() -> dict[str, Any]:
    return PlanProposal.model_json_schema()


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
    world_version: int
    projected_energy_pct: dict[str, float] = Field(default_factory=dict)


class AllocatedTask(Strict):
    task_id: str
    binding_id: str
    robot_id: Optional[str]
    action: Action
    depends_on: list[str]
    skill: SkillKind


class PlannerRecord(Strict):
    """Provenance of a proposal. ``kind`` is never inferred: it states which planner ran."""

    kind: Literal["qwen", "baseline"]
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


class ExecutionPlan(Strict):
    schema_version: Literal["hive.execution-plan.v1"] = EXECUTION_PLAN_SCHEMA
    mission_id: str
    revision: int
    kind: Literal["initial", "continuation", "recovery", "edit", "clarification"]
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
