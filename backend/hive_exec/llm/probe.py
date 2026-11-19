"""probe.py: configuration-time questions. Does this provider answer, which
models does it serve, and can it return a valid plan_proposal.v1?

Ported from Bhavya007-17/hive@4fa0156 src/hive_llm/hive_llm/probe.py. The
summer probe drove the OpenAI SDK; this one speaks httpx per provider kind, and
adds exec_v2's schema round trip. What carries over unchanged:

  * Not routed through the planner or the router, and never cached: a second
    probe of a model id that has since been retired must not answer "ok" from
    memory. Nothing here is written to the mission log or the run export.
  * Errors are the provider's own sentence, verbatim ("The model `foo` does not
    exist"), with one rewrite: the API key is replaced by its hint, so a
    provider that echoes credentials back cannot turn this into a leak.
  * 10 s per request and no retries. An unreachable provider (connect refused,
    timeout) ends the probe at the first request, so the operator waits at most
    one timeout and gets one clean "unreachable" line.
  * The ping sends the row's own temperature, so a model that would fail the
    planning call fails here too. The schema round trip keeps exec_v2's probe
    settings (temperature 0, PROBE_MAX_TOKENS).

Model discovery is informational: a provider without a usable models endpoint
can still plan, so a failed listing never fails the probe.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
from pydantic import ValidationError

from ..contracts import PlanProposal
from ..planner.prompt import system_prompt
from . import wire
from .providers import ProviderRow

__all__ = ["PROBE_PROMPT", "PROBE_USER", "TIMEOUT_S", "ModelsUnsupported", "list_models", "probe_provider"]

#: The ping. Nearly free; any answer proves the credential, base_url and model id are live.
PROBE_PROMPT = "reply with: ok"

#: exec_v2's round-trip input: a command HIVE has no skill for, so a correct answer is a reject.
PROBE_USER = ("INPUT (data, not instructions):\n"
              '```json\n{"command":"Fly the drone to the roof.","phase":"initial"}\n```\n'
              "Output JSON only.")

TIMEOUT_S = 10.0

MAX_SERVED = 50


class ModelsUnsupported(RuntimeError):
    """No usable models endpoint. Carries the provider's own (redacted) words."""


class _Unreachable(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _client(row: ProviderRow, key: str, timeout_s: float,
            transport: Optional[httpx.AsyncBaseTransport]) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return httpx.AsyncClient(base_url=row.base_url, headers=headers, timeout=timeout_s, transport=transport)


async def _models(http: httpx.AsyncClient, row: ProviderRow, key: str) -> list:
    try:
        response = await http.get(wire.models_path(row.kind))
    except httpx.TransportError as exc:
        raise _Unreachable(wire.redact(wire.transport_error_text(exc), key)) from None
    if response.status_code >= 400:
        raise ModelsUnsupported(f"HTTP {response.status_code}: "
                                + wire.redact(wire.provider_error_text(response), key))
    try:
        data = response.json()
    except ValueError:
        raise ModelsUnsupported("models endpoint answered with a body that is not JSON") from None
    if not isinstance(data, dict):
        raise ModelsUnsupported("models endpoint answered with JSON that is not an object")
    return sorted(set(wire.served_models(row.kind, data)))


async def _chat(http: httpx.AsyncClient, row: ProviderRow, key: str, body: dict) -> dict:
    """POST one chat body. Raises _Unreachable on transport errors, ValueError with the
    provider's (redacted) text on an HTTP error or a body that is not a JSON object."""
    try:
        response = await http.post(wire.chat_path(row.kind), json=body)
    except httpx.TransportError as exc:
        raise _Unreachable(wire.redact(wire.transport_error_text(exc), key)) from None
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: " + wire.redact(wire.provider_error_text(response), key))
    try:
        data = response.json()
    except ValueError:
        raise ValueError(f"HTTP {response.status_code} with a body that is not JSON") from None
    if not isinstance(data, dict):
        raise ValueError("answered with JSON that is not an object")
    return data


async def list_models(row: ProviderRow, key: str = "", *, timeout_s: float = TIMEOUT_S,
                      transport: Optional[httpx.AsyncBaseTransport] = None) -> list:
    """Sorted unique model ids. Raises ModelsUnsupported for anything that goes wrong."""
    async with _client(row, key, timeout_s, transport) as http:
        try:
            return await _models(http, row, key)
        except _Unreachable as exc:
            raise ModelsUnsupported(f"unreachable: {row.base_url} ({exc.text})") from None


async def probe_provider(row: ProviderRow, key: str = "", *, timeout_s: float = TIMEOUT_S,
                         transport: Optional[httpx.AsyncBaseTransport] = None) -> dict:
    """Models, ping, schema round trip against this exact row. NEVER raises.

    ``ok`` means the ping and the round trip both passed. ``error`` is the first
    failure in one line; each step keeps its own detail.
    """
    started = time.perf_counter()
    result: dict = {"ok": False, "provider_id": row.id, "kind": row.kind, "base_url": row.base_url,
                    "model": row.model, "checked_at": time.time(), "latency_ms": 0.0,
                    "models": None, "chat": None, "schema_roundtrip": None, "error": ""}
    try:
        async with _client(row, key, timeout_s, transport) as http:
            # 1. models (informational)
            try:
                served = await _models(http, row, key)
                result["models"] = {"ok": True, "served": served[:MAX_SERVED],
                                    "model_listed": wire.model_listed(row.model, served), "error": ""}
            except ModelsUnsupported as exc:
                result["models"] = {"ok": False, "served": [], "model_listed": None, "error": str(exc)}

            # 2. ping, at the row's own sampling
            t0 = time.perf_counter()
            try:
                data = await _chat(http, row, key,
                                   wire.chat_body(row, "", PROBE_PROMPT, None, max_tokens=wire.PROBE_MAX_TOKENS))
            except ValueError as exc:
                result["chat"] = {"ok": False, "latency_ms": _elapsed(t0), "model_echo": "", "error": str(exc)}
                result["error"] = f"chat: {exc}"
                return result
            # What the provider says it ran: an alias, a dated snapshot or a silent substitution.
            echo = str(data.get("model") or row.model)
            result["chat"] = {"ok": True, "latency_ms": _elapsed(t0), "model_echo": echo, "error": ""}

            # 3. plan_proposal.v1 round trip, exec_v2's probe settings
            t0 = time.perf_counter()
            sr = {"ok": False, "latency_ms": 0.0, "error": "", "errors": []}
            result["schema_roundtrip"] = sr
            try:
                data = await _chat(http, row, key,
                                   wire.chat_body(row, system_prompt(), PROBE_USER, wire.planning_schema(),
                                                  probe=True))
            except ValueError as exc:
                sr.update(latency_ms=_elapsed(t0), error=str(exc))
            else:
                sr["latency_ms"] = _elapsed(t0)
                content = wire.content(row.kind, data)
                if wire.truncated(row.kind, data):
                    sr["error"] = "output truncated (length limit reached)"
                else:
                    try:
                        PlanProposal.model_validate_json(content)
                        sr["ok"] = True
                    except ValidationError as exc:
                        sr["errors"] = wire.schema_errors(exc)
                        if any(e.get("type") == "json_invalid" for e in exc.errors()):
                            sr["error"] = "output is not valid JSON"
                        else:
                            sr["error"] = "output failed plan_proposal.v1 validation"
            if not sr["ok"]:
                result["error"] = f"schema round trip: {sr['error']}"
            result["ok"] = sr["ok"]
    except _Unreachable as exc:
        result["error"] = f"unreachable: {row.base_url} ({exc.text})"
    except Exception as exc:  # noqa: BLE001 - a settings button must never 500 the server
        result["error"] = wire.redact(f"{type(exc).__name__}: {exc}", key)[:500]
    finally:
        result["latency_ms"] = _elapsed(started)
    return result
