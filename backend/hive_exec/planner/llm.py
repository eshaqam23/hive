"""LLM planner: the role's provider chain from the store, through the router.

Generalizes exec_v2's QwenPlanner. The request bodies per provider kind and the
scope-required schema patch live in ``llm.wire``; which provider answers lives
in ``llm.router`` (ordered fallbacks, failover on transport errors only). This
class keeps what QwenPlanner owned: the prompt (hive.prompt.v3), the token
budget, one request at a time, schema validation, and the PlannerRecord.

A schema or parse failure raises LLMParseError carrying the raw output; the
Executive owns the single repair attempt. It is never a failover.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from pydantic import ValidationError

from ..context import MAX_INPUT_TOKENS, ContextBudgetExceeded, PlanningContext, estimate_tokens
from ..contracts import PlanProposal, PlannerRecord
from ..llm import wire
from ..llm.probe import probe_provider
from ..llm.providers import ProviderRow
from ..llm.router import NotConfigured, ProviderHTTPError, ProviderResponseError, Router, TransportFailure
from .base import Planner, PlannerError, PlannerOutput
from .prompt import PROMPT_VERSION, fit_user_prompt, system_prompt


class LLMParseError(PlannerError):
    """The provider answered, but not with a valid PlanProposal. ``raw`` goes to the repair."""

    def __init__(self, message: str, raw: str, record: PlannerRecord):
        super().__init__(message, record)
        self.raw = raw


def settings_record(row: ProviderRow) -> dict[str, Any]:
    s = row.sampling
    return {
        "provider_kind": row.kind,
        "structured_mode": row.structured_mode,
        "temperature": s.temperature,
        "top_p": s.top_p,
        "top_k": s.top_k,
        "seed": s.seed,
        "max_tokens": s.max_tokens,
        "timeout_s": s.timeout_s,
        "enable_thinking": False,
    }


def _label(row: ProviderRow, model: str) -> str:
    return f"LLM via {row.label or row.id} ({model})"


class LLMPlanner(Planner):
    kind = "llm"

    def __init__(self, router: Router, role: str = "executive"):
        self.router = router
        self.role = role
        self._lock = asyncio.Lock()  # one planning request at a time
        self.probe_result: Optional[dict] = None
        self.schema = wire.planning_schema()

    # ------------------------------------------------------------- record
    def _record(self, row: ProviderRow, tokens: int, **extra: Any) -> PlannerRecord:
        model = extra.pop("model", row.model)
        return PlannerRecord(kind="llm", label=_label(row, model), model=model, endpoint=row.base_url,
                             prompt_version=PROMPT_VERSION, settings=settings_record(row),
                             input_tokens_est=tokens, provider_id=row.id, provider_kind=row.kind, **extra)

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        """What the UI badge needs. Never a key: rows are reported by id, label and URL."""
        p = self.probe_result
        out: dict[str, Any] = {"kind": "llm", "role": self.role, "configured": False, "provider": None,
                               "provider_label": None, "provider_kind": None, "model": None, "endpoint": None,
                               "fallbacks": [], "prompt_version": PROMPT_VERSION, "settings": None, "probe": p,
                               "live_enabled": False, "problems": []}
        try:
            out["problems"] = list(self.router.store().problems)
            chain = self.router.chain(self.role)
            row, _ = self.router.resolve(self.role)
        except NotConfigured as exc:
            out["problems"].append(str(exc))
            return out
        out.update(configured=True, provider=row.id, provider_label=row.label or row.id, provider_kind=row.kind,
                   model=row.model, endpoint=row.base_url, fallbacks=[c.row.id for c in chain[1:]],
                   settings=settings_record(row))
        # The probe must be of this exact row: an edited URL or model is unproven until probed again.
        out["live_enabled"] = bool(p and p.get("ok") and p.get("provider_id") == row.id
                                   and p.get("model") == row.model and p.get("base_url") == row.base_url
                                   and p.get("kind") == row.kind)
        return out

    # ------------------------------------------------------------- probe
    async def probe(self) -> dict:
        """Probe the role's primary provider. Not cached for later probes, not logged to a mission."""
        try:
            row, key = self.router.resolve(self.role)
        except NotConfigured as exc:
            self.probe_result = {"ok": False, "provider_id": None, "kind": None, "base_url": None, "model": None,
                                 "checked_at": time.time(), "latency_ms": 0.0, "models": None, "chat": None,
                                 "schema_roundtrip": None, "error": str(exc)}
            return self.probe_result
        self.probe_result = await probe_provider(row, key, transport=self.router.transport)
        return self.probe_result

    # ------------------------------------------------------------- propose
    async def propose(
        self,
        ctx: PlanningContext,
        repair_feedback: Optional[list[str]] = None,
        previous_output: Optional[str] = None,
    ) -> PlannerOutput:
        try:
            primary, _ = self.router.resolve(self.role)
        except NotConfigured as exc:
            raise PlannerError(str(exc)) from None

        sys_msg = system_prompt()
        schema_text = json.dumps(self.schema)
        fixed = estimate_tokens(sys_msg) + estimate_tokens(schema_text) // 4
        usr_msg = fit_user_prompt(ctx, repair_feedback, previous_output, fixed)
        tokens = fixed + estimate_tokens(usr_msg)
        if tokens > MAX_INPUT_TOKENS:
            raise ContextBudgetExceeded(
                f"Estimated {tokens} input tokens exceeds the {MAX_INPUT_TOKENS}-token budget; refusing to truncate."
            )
        record = self._record(primary, tokens)

        async with self._lock:
            try:
                result = await self.router.chat(
                    self.role, lambda row: wire.chat_body(row, sys_msg, usr_msg, self.schema))
            except NotConfigured as exc:
                raise PlannerError(str(exc), record) from None
            except (TransportFailure, ProviderHTTPError) as exc:
                failed = self._record(exc.row, tokens, fallback_used=exc.row.id != primary.id,
                                      failovers=list(exc.attempts))
                raise PlannerError(str(exc), failed) from None
            except ProviderResponseError as exc:
                failed = self._record(exc.row, tokens, fallback_used=exc.row.id != primary.id,
                                      failovers=list(exc.attempts))
                raise LLMParseError(str(exc), exc.raw, failed) from None

        row, data = result.row, result.data
        record = self._record(row, tokens, model=str(data.get("model") or row.model),
                              fallback_used=result.fallback_used, failovers=list(result.failovers),
                              provider_latency_ms=result.latency_ms, latency_ms=result.latency_ms,
                              usage=wire.usage(row.kind, data))
        content = wire.content(row.kind, data)

        if wire.truncated(row.kind, data):
            raise LLMParseError("LLM output truncated (length limit reached)", content, record)
        try:
            proposal = PlanProposal.model_validate_json(content)
        except ValidationError as exc:
            errs = wire.schema_errors(exc)
            record.errors = errs
            raise LLMParseError("LLM output failed schema validation: " + "; ".join(errs), content, record) from exc
        except ValueError as exc:
            raise LLMParseError(f"LLM output is not valid JSON: {exc}", content, record) from exc
        return PlannerOutput(proposal, record)
