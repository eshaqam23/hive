"""Per-kind HTTP shapes: request bodies, response fields, provider error text.

The bodies for ``ollama`` + ``ollama_format`` and for ``openai_compatible`` +
``structured_outputs``/``guided_json`` are exec_v2's QwenPlanner bodies,
unchanged. ``json_schema`` and ``json_object`` add the OpenAI response_format
shapes for hosted and local OpenAI-compatible servers.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from pydantic import ValidationError

from ..context import MAX_INPUT_TOKENS, RESERVED_OUTPUT_TOKENS, TOTAL_WINDOW_TOKENS
from ..contracts import plan_proposal_json_schema
from .providers import ProviderRow, key_hint

#: Schema round trip in the probe. exec_v2 used 600; hosted reasoning models
#: (gpt-oss) spend completion tokens before the JSON, so the probe allows more.
PROBE_MAX_TOKENS = 1024
VLLM_MODES = ("structured_outputs", "guided_json")


def ollama_num_ctx(row: ProviderRow) -> int:
    """The context window Ollama is told to use: the planner's input limit plus the row's output limit.

    Without it Ollama falls back to its own default (often 4096) and silently cuts a long prompt from the
    front. It depends on the row only, never on the call (probe or planning): a different num_ctx makes
    Ollama reload the model. Clamped to the planner's total window (TOTAL_WINDOW_TOKENS): a row max_tokens of
    131072 (the Field allows it) would otherwise ask for a window that can exhaust the desktop GPU."""
    return min(MAX_INPUT_TOKENS + row.sampling.max_tokens, TOTAL_WINDOW_TOKENS)


def planning_schema() -> dict:
    """plan_proposal.v1 as sent to the model, with ``scope`` required.

    Pydantic marks scope optional because it may be null (clarify/reject).
    HIVE still requires the key, so the model cannot omit it (exec_v2 patch).
    """
    schema = plan_proposal_json_schema()
    required = schema.setdefault("required", [])
    if "scope" not in required:
        required.append("scope")
    return schema


def schema_errors(exc: ValidationError) -> list:
    """``loc: msg`` for the first 12 errors. Never the input value."""
    out = []
    for e in exc.errors()[:12]:
        loc = ".".join(str(p) for p in e.get("loc", ()))
        out.append(f"{loc}: {e.get('msg')}")
    return out


def chat_path(kind: str) -> str:
    return "/api/chat" if kind == "ollama" else "/chat/completions"


def models_path(kind: str) -> str:
    return "/api/tags" if kind == "ollama" else "/models"


def chat_body(row: ProviderRow, system: str, user: str, schema: Optional[dict], *,
              probe: bool = False, max_tokens: Optional[int] = None) -> dict:
    """One chat request for this row. ``schema=None`` asks for free text (probe ping).

    An empty ``system`` sends the user message alone.
    """
    s = row.sampling
    temperature = 0 if probe else s.temperature
    limit = max_tokens or (PROBE_MAX_TOKENS if probe else s.max_tokens)
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]

    if row.kind == "ollama":
        options: dict[str, Any] = {"temperature": temperature, "top_p": s.top_p}
        if s.seed is not None:
            options["seed"] = s.seed
        # Ollama's name for the output-token limit; capped at the reserved output so input + output fit num_ctx.
        options["num_predict"] = min(limit, RESERVED_OUTPUT_TOKENS)
        options["num_ctx"] = ollama_num_ctx(row)  # the same on every request for this row
        if s.top_k is not None:
            options["top_k"] = s.top_k
        body: dict[str, Any] = {"model": row.model, "messages": messages, "stream": False}
        if schema is not None:
            body["format"] = schema if row.structured_mode == "ollama_format" else "json"
        body["think"] = False
        body["options"] = options
        return body

    body = {"model": row.model, "messages": messages, "temperature": temperature, "top_p": s.top_p}
    if s.seed is not None:
        body["seed"] = s.seed
    body["max_tokens"] = limit
    if row.structured_mode in VLLM_MODES:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if schema is not None:
        if row.structured_mode == "guided_json":
            body["guided_json"] = schema
        elif row.structured_mode == "structured_outputs":
            body["structured_outputs"] = {"json": schema}
        elif row.structured_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "plan_proposal", "schema": schema}}
        else:
            body["response_format"] = {"type": "json_object"}
    if s.top_k is not None:
        body["top_k"] = s.top_k
    return body


def content(kind: str, data: dict) -> str:
    if kind == "ollama":
        return (data.get("message") or {}).get("content") or ""
    choices = data.get("choices") or [{}]
    return ((choices[0] or {}).get("message") or {}).get("content") or ""


def usage(kind: str, data: dict) -> Optional[dict]:
    if kind != "ollama":
        value = data.get("usage")
        return value if isinstance(value, dict) else None
    out: dict[str, Any] = {}
    if data.get("prompt_eval_count") is not None:
        out["prompt_tokens"] = data["prompt_eval_count"]
    if data.get("eval_count") is not None:
        out["completion_tokens"] = data["eval_count"]
    if out:
        out["total_tokens"] = out.get("prompt_tokens", 0) + out.get("completion_tokens", 0)
    return out or None


def truncated(kind: str, data: dict) -> bool:
    choices = data.get("choices") or [{}]
    return (choices[0] or {}).get("finish_reason") == "length" or data.get("done_reason") == "length"


def served_models(kind: str, data: dict) -> list:
    if kind == "ollama":
        return [m["name"] for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


def model_listed(model: str, served: list) -> bool:
    """Gemini lists ``models/<id>``; Ollama may list ``<name>:latest``."""
    return any(s == model or s == f"models/{model}" or s == f"{model}:latest" for s in served)


def provider_error_text(response: httpx.Response) -> str:
    """The provider's own sentence: error.message, a string error, message, else the body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:1000]
        if isinstance(error, str) and error:
            return error[:1000]
        if isinstance(body.get("message"), str) and body["message"]:
            return body["message"][:1000]
        if isinstance(body.get("detail"), str) and body["detail"]:
            return body["detail"][:1000]
    text = response.text.strip()
    return text[:1000] if text else (response.reason_phrase or "no body")


def transport_error_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def redact(text: str, key: str) -> str:
    """A key a provider echoes back is replaced by its hint before anyone sees it."""
    if not key or not text or key not in text:
        return text
    return text.replace(key, key_hint(key) or "…")
