"""api.py: /api/exec/llm, the Models page's HTTP surface.

Ported from Bhavya007-17/hive@4fa0156
src/hive_dashboard_server/hive_dashboard_server/providers_api.py, onto this
repo's store (rows with kind, structured_mode and sampling; roles as ordered
chains) and the router's fingerprint re-read in place of the ROS reload service.

WRITE-ONLY FOR KEY MATERIAL. POST puts a key in; nothing here gets one back
out. Every read answers with :meth:`ProviderStore.public` (``key_present`` and
a four-character hint). ``api_key`` arrives as a SecretStr, and validation
errors on these routes are answered with loc/msg/type only, because FastAPI's
default 422 body echoes the request ("input"), key included. There is no
route, no query parameter and no error path here that returns a key, and none
may be added.

A write loads the file fresh, changes it, saves it (0600, atomic) and tells the
router to drop its cached copy, so the next mission plans with what was saved.
A file that does not parse is reported and never overwritten: a write answers
409 until the operator fixes it by hand.

The test button probes one row as stored (or with a model typed but not yet
saved). It is not routed through the planner, not cached and not written to
the mission log. The models route asks the same row's models endpoint only, for
the pickers' suggestions; a provider without one answers ok false with its own
words, never a server error.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .probe import ModelsUnsupported, list_models, probe_provider
from .providers import (ENV_ROW_ID, ROLES, ProviderRow, ProviderStore, ProviderStoreError, Sampling, load_store,
                        store_path)
from .router import NotConfigured, Router
from .templates import templates

__all__ = ["PREFIX", "ProbeIn", "ProviderIn", "install"]

PREFIX = "/api/exec/llm"

MAX_KEY_LEN = 4096

#: A picker suggestion list; hosted routers serve a few hundred ids.
MAX_MODELS = 1000

ENV_NOTE = ("No provider store exists yet, so the executive role uses the read-only row built from QWEN_* "
            "env. The first save creates the store and from then on QWEN_* is not read: bind the executive "
            "role to a saved provider.")


class ProviderIn(BaseModel):
    """POST /api/exec/llm/providers: one row from the form (create or replace).

    ``api_key`` is optional and an empty string counts as omission: the form
    shows a saved key as its hint and posts an empty field when the operator did
    not retype it. Removing a key is DELETE.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = ""
    kind: Literal["ollama", "openai_compatible"]
    base_url: str
    model: str
    structured_mode: str
    sampling: Sampling = Field(default_factory=Sampling)
    api_key_env: str = ""
    api_key: SecretStr = SecretStr("")


class ProbeIn(BaseModel):
    """POST /api/exec/llm/providers/{id}/test: optionally a model typed but not yet saved."""

    model_config = ConfigDict(extra="forbid")

    model: str = ""


def _clean(errors: list) -> list:
    """loc/msg/type only. pydantic's "input" and "ctx" can hold the request body, key included."""
    return [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "invalid")), "type": str(e.get("type", ""))}
            for e in errors]


def _unprocessable(errors: list) -> JSONResponse:
    return JSONResponse({"detail": _clean(errors)}, status_code=422)


def _view(router: Router) -> dict:
    """The body of every read and every write: rows, roles, problems, where the store is."""
    target = store_path(router.path)
    out: dict[str, Any] = {"providers": [], "roles": {role: [] for role in ROLES}, "problems": [],
                           "store": {"path": str(target), "exists": target.exists(), "env_row": False, "note": ""}}
    try:
        store = router.store()
    except NotConfigured as exc:
        out["problems"].append(str(exc))
        return out
    env_row = store.row(ENV_ROW_ID) is not None and store.row(ENV_ROW_ID).read_only
    out.update(providers=store.public(), roles=store.roles(), problems=list(store.problems))
    out["store"].update(env_row=env_row, note=ENV_NOTE if env_row else "")
    return out


def build_router(llm: Router) -> APIRouter:
    api = APIRouter(prefix=PREFIX)
    lock = asyncio.Lock()  # one read-modify-write of the file at a time

    def fresh() -> ProviderStore:
        try:
            return load_store(llm.path, legacy=llm.legacy)
        except ProviderStoreError as exc:
            raise HTTPException(409, f"{exc}; fix the file by hand, it is not overwritten") from None

    def saved(store: ProviderStore) -> dict:
        store.save()
        llm.invalidate()
        return _view(llm)

    # -- reads ---------------------------------------------------------------------------
    @api.get("/providers")
    async def providers() -> dict:
        return _view(llm)

    @api.get("/roles")
    async def roles() -> dict:
        return _view(llm)["roles"]

    @api.get("/templates")
    async def provider_templates() -> list:
        return templates()

    # -- writes --------------------------------------------------------------------------
    @api.post("/providers")
    async def upsert(body: ProviderIn):
        key = body.api_key.get_secret_value().strip()
        if len(key) > MAX_KEY_LEN or any(ch.isspace() for ch in key):
            return _unprocessable([{"loc": ("body", "api_key"), "msg": "must be one line with no whitespace",
                                    "type": "value_error"}])
        fields = body.model_dump(exclude={"api_key"})
        fields["id"] = fields["id"].strip()
        if fields["id"] == ENV_ROW_ID:
            raise HTTPException(400, f"provider id {ENV_ROW_ID!r} is reserved for the read-only QWEN_* env row")
        try:
            row = ProviderRow(**fields)
        except ValidationError as exc:
            return _unprocessable([dict(e, loc=("body", *e.get("loc", ()))) for e in exc.errors()])
        async with lock:
            store = fresh()
            try:
                store.upsert(row, api_key=key or None)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            return saved(store)

    @api.delete("/providers/{provider_id}")
    async def delete(provider_id: str) -> dict:
        async with lock:
            store = fresh()
            try:
                found = store.delete(provider_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            if not found:
                raise HTTPException(404, f"no provider {provider_id!r}")
            return saved(store)

    @api.post("/roles")
    async def set_roles(body: dict[str, list[str]]) -> dict:
        """``{"executive": ["primary", "fallback", ...]}``; roles left out keep their chain."""
        unknown = [role for role in body if role not in ROLES]
        if unknown:
            raise HTTPException(400, f"unknown role {unknown[0]!r}; known roles are {list(ROLES)}")
        async with lock:
            store = fresh()
            for role, chain in body.items():
                if ENV_ROW_ID in chain:
                    raise HTTPException(400, "the QWEN_* env row cannot be bound; it serves the executive role "
                                             "only while no store file exists")
                try:
                    store.set_role(role, chain)
                except KeyError as exc:
                    raise HTTPException(404, f"cannot bind {role}: {exc.args[0]}") from None
            return saved(store)

    def stored(provider_id: str) -> tuple[ProviderStore, ProviderRow]:
        try:
            store = llm.store()
        except NotConfigured as exc:
            raise HTTPException(409, str(exc)) from None
        row = store.row(provider_id)
        if row is None:
            raise HTTPException(404, f"no provider {provider_id!r}")
        return store, row

    # -- the model list ------------------------------------------------------------------
    @api.get("/providers/{provider_id}/models")
    async def provider_models(provider_id: str) -> dict:
        """What the provider's models endpoint serves, for the pickers. A failure is an answer:
        the pickers fall back to free text, so ``ok`` false is not an error status."""
        store, row = stored(provider_id)
        try:
            served = await list_models(row, store.api_key(provider_id) or "", transport=llm.transport)
        except ModelsUnsupported as exc:
            return {"ok": False, "models": [], "error": str(exc)}
        return {"ok": True, "models": served[:MAX_MODELS], "error": ""}

    # -- the test button -----------------------------------------------------------------
    @api.post("/providers/{provider_id}/test")
    async def test_provider(provider_id: str, body: Optional[ProbeIn] = None) -> dict:
        store, row = stored(provider_id)
        model = body.model.strip() if body is not None else ""
        if model:
            try:
                row = ProviderRow.model_validate({**row.model_dump(), "model": model})
            except ValidationError as exc:
                return _unprocessable([dict(e, loc=("body", *e.get("loc", ()))) for e in exc.errors()])
        return await probe_provider(row, store.api_key(provider_id) or "", transport=llm.transport)

    return api


def install(app: FastAPI, llm: Router) -> None:
    """Mount the routes, and answer validation errors under PREFIX without echoing input."""
    app.include_router(build_router(llm))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(PREFIX + "/") or request.url.path == PREFIX:
            return _unprocessable(list(exc.errors()))
        return await request_validation_exception_handler(request, exc)
