"""FastAPI service for the HIVE Executive (default http://127.0.0.1:8788/api/exec)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import ExecSettings
from .contracts import plan_proposal_json_schema
from .executive import ExecError, Executive
from .gateway.base import RobotGateway
from .planner.baseline import BaselinePlanner
from .planner.qwen import QwenPlanner
from .sim.fake_world import KNOWN_FAULTS


def make_gateway(settings: ExecSettings) -> RobotGateway:
    if settings.gateway == "fake":
        from .gateway.fake import FakeGateway

        return FakeGateway(time_scale=settings.sim_time_scale)
    if settings.gateway == "synapse":
        from .gateway.synapse import SynapseGateway

        return SynapseGateway(settings.bridge_url, label="AgenticROS / Corebrum Synapse bridge")
    raise ValueError(f"Unknown HIVE_GATEWAY {settings.gateway}")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MissionIn(Body):
    command: str = Field(min_length=1, max_length=1000)
    planner: Optional[Literal["qwen", "baseline"]] = None


class ApproveIn(Body):
    proposal_hash: str


class RejectIn(Body):
    reason: str = ""


class EditIn(Body):
    edits: list[dict[str, Any]] = Field(min_length=1, max_length=16)


class ClarifyIn(Body):
    answer: str = Field(min_length=1, max_length=500)


class ResetIn(Body):
    scenario: Literal["unknown", "known"] = "unknown"
    faults: list[str] = Field(default_factory=list)


class FaultIn(Body):
    fault: str


def create_app(settings: Optional[ExecSettings] = None, gateway: Optional[RobotGateway] = None,
               executive: Optional[Executive] = None) -> FastAPI:
    settings = settings or ExecSettings.from_env()
    if executive is None:
        gateway = gateway or make_gateway(settings)
        planners = {"qwen": QwenPlanner(settings.qwen), "baseline": BaselinePlanner()}
        executive = Executive(gateway, planners, default_planner=settings.default_planner)
    ex = executive

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ex.start()
        qwen = ex.planners.get("qwen")
        if isinstance(qwen, QwenPlanner) and settings.qwen.base_url:
            await qwen.probe()
        yield
        await ex.gateway.stop()

    app = FastAPI(title="HIVE Executive", version="0.1.0", lifespan=lifespan)
    app.state.executive = ex
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.exception_handler(ExecError)
    async def _exec_error(_, exc: ExecError):
        return JSONResponse({"ok": False, "error": str(exc), "detail": exc.detail}, status_code=exc.status)

    @app.get("/api/exec/health")
    async def health():
        return {"ok": True, "service": "hive-executive", "run_id": ex.run_id}

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
        qwen = ex.planners.get("qwen")
        if not isinstance(qwen, QwenPlanner):
            raise HTTPException(404, "Qwen planner not configured")
        result = await qwen.probe()
        return result.__dict__

    @app.post("/api/exec/missions")
    async def create_mission(body: MissionIn):
        await ex.create_mission(body.command, body.planner)
        return ex.state()

    @app.post("/api/exec/mission/clarify")
    async def clarify(body: ClarifyIn):
        await ex.answer_clarification(body.answer)
        return ex.state()

    @app.post("/api/exec/mission/replan")
    async def replan():
        await ex.replan()
        return ex.state()

    @app.post("/api/exec/revisions/{revision}/approve")
    async def approve(revision: int, body: ApproveIn):
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

    @app.post("/api/exec/reset")
    async def reset(body: ResetIn):
        unknown = [f for f in body.faults if f not in KNOWN_FAULTS]
        if unknown:
            raise HTTPException(400, f"Unknown faults: {unknown}")
        await ex.reset(body.scenario, body.faults)
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
