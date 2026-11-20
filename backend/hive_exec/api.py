"""FastAPI service for the HIVE Executive (default http://127.0.0.1:8788/api/exec)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import ExecSettings
from .contracts import plan_proposal_json_schema
from .executive import ExecError, Executive
from .gateway.base import RobotGateway
from .health import exec_health, not_ready_reason
from .planner.baseline import BaselinePlanner
from .llm import api as llm_api
from .llm.router import Router
from .planner.llm import LLMPlanner
from .sim.fake_world import KNOWN_FAULTS


def make_gateway(settings: ExecSettings) -> RobotGateway:
    if settings.gateway == "fake":
        from .gateway.fake import FakeGateway

        return FakeGateway(time_scale=settings.sim_time_scale)
    if settings.gateway == "synapse":
        from .gateway.synapse import SynapseGateway

        return SynapseGateway(settings.bridge_url, label="AgenticROS / Corebrum Synapse bridge")
    raise ValueError(f"Unknown HIVE_GATEWAY {settings.gateway}")


_SURROGATE = re.compile("[\ud800-\udfff]")


def _has_surrogate(value: Any) -> bool:
    if isinstance(value, str):
        return _SURROGATE.search(value) is not None
    if isinstance(value, dict):
        return any(_has_surrogate(k) or _has_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_surrogate(v) for v in value)
    return False


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_lone_surrogates(cls, data: Any) -> Any:
        """A lone surrogate (e.g. "\\ud83d" alone) is valid JSON but not text: it cannot be stored, shown or
        sent to a planner, so the request is refused before anything is kept."""
        if _has_surrogate(data):
            raise ValueError("text contains a lone surrogate (invalid Unicode); nothing was stored")
        return data


class MissionIn(Body):
    command: str = Field(min_length=1, max_length=1000)
    planner: Optional[Literal["llm", "qwen", "baseline"]] = None  # "qwen" is the exec_v2 alias of "llm"


class ApproveIn(Body):
    proposal_hash: str


class RejectIn(Body):
    reason: str = ""


class EditIn(Body):
    edits: list[dict[str, Any]] = Field(min_length=1, max_length=16)


class ClarifyIn(Body):
    answer: str = Field(min_length=1, max_length=500)


class MessageIn(Body):
    """An operator message to the planner (untrusted text: capped, only ever shown to the planner inside its
    delimited OPERATOR FEEDBACK block)."""
    text: str = Field(min_length=1, max_length=500)
    mode: Literal["now", "queue"]


class CancelTaskIn(Body):
    """Which revision's task to stop: task ids repeat across revisions. None: the active revision."""
    revision: Optional[int] = Field(default=None, ge=1)


class ResetIn(Body):
    scenario: Literal["unknown", "known"] = "unknown"
    faults: list[str] = Field(default_factory=list)
    world: Optional[Literal["fixed", "random"]] = None  # None: HIVE_SIM_WORLD default (fixed when unset)
    seed: Optional[int] = Field(default=None, ge=0, le=2 ** 53)  # replay a random world; ignored when fixed


def sim_world_from_env() -> str:
    """Default sim world for the executive process: HIVE_SIM_WORLD=fixed|random (unset: fixed)."""
    value = (os.environ.get("HIVE_SIM_WORLD") or "fixed").strip().lower()
    if value not in ("fixed", "random"):
        raise ValueError(f"HIVE_SIM_WORLD must be fixed or random, got {value!r}")
    return value


class FaultIn(Body):
    fault: str


def create_app(settings: Optional[ExecSettings] = None, gateway: Optional[RobotGateway] = None,
               executive: Optional[Executive] = None) -> FastAPI:
    settings = settings or ExecSettings.from_env()
    if executive is None:
        gateway = gateway or make_gateway(settings)
        planners = {"llm": LLMPlanner(Router(legacy=settings.qwen)), "baseline": BaselinePlanner()}
        executive = Executive(gateway, planners, default_planner=settings.default_planner,
                              runs_dir=settings.runs_dir, sim_world=sim_world_from_env())
    ex = executive

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ex.start()
        llm = ex.planners.get("llm")
        probe_task = None
        if isinstance(llm, LLMPlanner) and llm.status()["configured"]:
            # In the background: an unreachable provider must not hold startup for a timeout.
            probe_task = asyncio.create_task(llm.probe())
        yield
        if probe_task is not None and not probe_task.done():
            probe_task.cancel()
        await ex.gateway.stop()

    app = FastAPI(title="HIVE Executive", version="0.1.0", lifespan=lifespan)
    app.state.executive = ex
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    llm_planner = ex.planners.get("llm")
    if isinstance(llm_planner, LLMPlanner):
        llm_api.install(app, llm_planner.router)  # /api/exec/llm: provider rows, roles, test, templates

    prior_invalid = app.exception_handlers.get(RequestValidationError, request_validation_exception_handler)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request, exc: RequestValidationError):
        try:  # the handler that was there (llm_api's, or FastAPI's default), unchanged
            return await prior_invalid(request, exc)
        except UnicodeEncodeError:
            # the echoed input holds a lone surrogate, which a UTF-8 response cannot encode (it was a 500): the same
            # 422, ASCII-escaped
            body = json.dumps({"detail": jsonable_encoder(exc.errors())}, ensure_ascii=True)
            return Response(body, status_code=422, media_type="application/json")

    @app.exception_handler(ExecError)
    async def _exec_error(_, exc: ExecError):
        return JSONResponse({"ok": False, "error": str(exc), "detail": exc.detail}, status_code=exc.status)

    def require_ready(planner_kind: Optional[str]) -> None:
        """503 naming what is down. Only starting or approving work is gated, never cancel or stop."""
        problem = not_ready_reason(ex, planner_kind)
        if problem:
            reason, missing = problem
            raise ExecError(f"Not ready: {reason}", 503, {"missing": missing})

    @app.get("/api/exec/health")
    async def health():
        return exec_health(ex)

    @app.get("/api/exec/map")
    async def shared_map():
        return {"available": False, "reason": "shared perception not built yet", "schema": "hive.shared-map.v0"}

    @app.get("/api/exec/state")
    async def state():
        return ex.state()

    @app.get("/api/exec/events")
    async def events(after: int = 0):
        return {"run_id": ex.run_id, "timeline": [e for e in ex.timeline if e["seq"] > after]}

    @app.get("/api/exec/schema")
    async def schema():
        return plan_proposal_json_schema()

    @app.get("/api/exec/planner")
    async def planner_status():
        return {k: p.status() for k, p in ex.planners.items()}

    @app.post("/api/exec/planner/probe")
    async def planner_probe():
        llm = ex.planners.get("llm")
        if not isinstance(llm, LLMPlanner):
            raise HTTPException(404, "LLM planner not available")
        return await llm.probe()

    @app.post("/api/exec/missions")
    async def create_mission(body: MissionIn):
        require_ready(body.planner)
        await ex.create_mission(body.command, body.planner)
        return ex.state()

    @app.post("/api/exec/mission/clarify")
    async def clarify(body: ClarifyIn):
        await ex.answer_clarification(body.answer)
        return ex.state()

    @app.post("/api/exec/mission/messages")
    async def post_message(body: MessageIn):
        msg = await ex.post_message(body.text, body.mode)
        return {**ex.state(), "message": msg}

    @app.delete("/api/exec/mission/messages/{msg_id}")
    async def cancel_message(msg_id: str):
        msg = await ex.cancel_message(msg_id)
        return {**ex.state(), "message": msg}

    @app.post("/api/exec/mission/messages/{msg_id}/send-now")
    async def send_message_now(msg_id: str):
        msg = await ex.send_message_now(msg_id)
        return {**ex.state(), "message": msg}

    @app.post("/api/exec/mission/replan")
    async def replan():
        await ex.replan()
        return ex.state()

    @app.post("/api/exec/revisions/{revision}/approve")
    async def approve(revision: int, body: ApproveIn):
        if ex.mission is not None:
            require_ready(ex.mission.planner_kind)
        await ex.approve(revision, body.proposal_hash)
        return ex.state()

    @app.post("/api/exec/revisions/{revision}/reject")
    async def reject(revision: int, body: RejectIn):
        await ex.reject(revision, body.reason)
        return ex.state()

    @app.post("/api/exec/revisions/{revision}/edit")
    async def edit(revision: int, body: EditIn):
        await ex.edit(revision, body.edits)
        return ex.state()

    @app.post("/api/exec/cancel")
    async def cancel():
        await ex.cancel()
        return ex.state()

    @app.post("/api/exec/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, body: Optional[CancelTaskIn] = None):
        """Stop one task (of body.revision, default the active revision); never gated on planner or transport
        health."""
        await ex.cancel_task(task_id, body.revision if body is not None else None)
        return ex.state()

    @app.post("/api/exec/reset")
    async def reset(body: ResetIn):
        unknown = [f for f in body.faults if f not in KNOWN_FAULTS]
        if unknown:
            raise HTTPException(400, f"Unknown faults: {unknown}")
        await ex.reset(body.scenario, body.faults, world=body.world, seed=body.seed)
        return ex.state()

    @app.get("/api/exec/faults")
    async def faults():
        return {"available": KNOWN_FAULTS, "active": ex.faults}

    @app.post("/api/exec/faults")
    async def inject(body: FaultIn):
        if body.fault not in KNOWN_FAULTS:
            raise HTTPException(400, f"Unknown fault {body.fault}")
        await ex.inject_fault(body.fault)
        return ex.state()

    @app.get("/api/exec/export")
    async def export():
        return JSONResponse(ex.export(), headers={
            "Content-Disposition": f'attachment; filename="hive-run-{ex.run_id}.json"'})

    @app.get("/api/exec/debug/truth")
    async def debug_truth():
        """Simulator truth for debugging the fake fleet only. Not used by planning or the normal map."""
        return {"warning": "SIMULATOR TRUTH - debug only", "truth": await ex.gateway.debug_truth()}

    return app
