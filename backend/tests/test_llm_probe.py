"""Probe: list models, a tiny prompt, a plan_proposal.v1 round trip. No network.

Ported from Bhavya007-17/hive@4fa0156 src/hive_llm/test/test_probe.py: the
summer tests drove the OpenAI SDK with a fake handle; these drive httpx with a
MockTransport, per provider kind. The assertions that matter are the same:
the provider's own error text comes back verbatim, a key it echoes is redacted
to its hint, the probe never raises, and it never retries.
"""

import json

import httpx
import pytest

from hive_exec.llm import probe as probe_mod
from hive_exec.llm import wire
from hive_exec.llm.probe import PROBE_PROMPT, TIMEOUT_S, ModelsUnsupported, list_models, probe_provider
from hive_exec.llm.providers import ProviderRow

KEY = "sk-test-NOT-A-REAL-KEY-4f2a"

REJECT = json.dumps({"schema_version": "hive.plan-proposal.v1", "disposition": "reject", "scope": None,
                     "summary": "No flight skill.", "mission_goals": [], "stage_goals": [], "tasks": [],
                     "issues": [], "clarification_question": None})


def openai_row(**over) -> ProviderRow:
    fields = dict(id="groq", label="Groq", kind="openai_compatible", base_url="https://api.example.test/openai/v1",
                  model="openai/gpt-oss-120b", structured_mode="json_schema")
    fields.update(over)
    return ProviderRow(**fields)


def ollama_row(**over) -> ProviderRow:
    fields = dict(id="ollama", kind="ollama", base_url="http://127.0.0.1:11434", model="qwen3:8b",
                  structured_mode="ollama_format")
    fields.update(over)
    return ProviderRow(**fields)


class Provider:
    """Scripted OpenAI-compatible / Ollama endpoint. Every request is recorded."""

    def __init__(self, kind="openai_compatible", models=None, ping="ok", plan=REJECT, echo=None,
                 chat_status=200, chat_error=None, models_status=200, finish="stop"):
        self.kind, self.ping, self.plan, self.echo = kind, ping, plan, echo
        self.models = models if models is not None else ["openai/gpt-oss-120b"]
        self.chat_status, self.chat_error, self.models_status, self.finish = chat_status, chat_error, \
            models_status, finish
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/models") or path.endswith("/api/tags"):
            if self.models_status != 200:
                return httpx.Response(self.models_status, json={"error": {"message": "no models endpoint here"}})
            if self.kind == "ollama":
                return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.chat_status != 200:
            return httpx.Response(self.chat_status, json=self.chat_error) if isinstance(self.chat_error, dict) \
                else httpx.Response(self.chat_status, text=self.chat_error or "")
        text = self.ping if body["messages"][-1]["content"] == PROBE_PROMPT else self.plan
        model = self.echo or body["model"]
        if self.kind == "ollama":
            return httpx.Response(200, json={"model": model, "message": {"role": "assistant", "content": text},
                                             "done": True, "done_reason": "length" if self.finish == "length"
                                             else "stop"})
        return httpx.Response(200, json={"model": model, "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": self.finish}]})


async def run(row, provider, key=KEY):
    return await probe_provider(row, key, transport=httpx.MockTransport(provider))


# ---------------------------------------------------------------- success
async def test_probe_reports_ok_with_latency_and_the_model_the_provider_ran():
    result = await run(openai_row(), Provider())
    assert result["ok"] is True and result["error"] == ""
    assert result["chat"]["ok"] and result["chat"]["model_echo"] == "openai/gpt-oss-120b"
    assert result["chat"]["latency_ms"] >= 0 and result["latency_ms"] >= 0
    assert result["schema_roundtrip"]["ok"] is True
    assert result["models"] == {"ok": True, "served": ["openai/gpt-oss-120b"], "model_listed": True, "error": ""}
    assert result["provider_id"] == "groq" and result["kind"] == "openai_compatible"


async def test_probe_echoes_what_the_provider_actually_ran_not_what_was_asked():
    result = await run(openai_row(), Provider(echo="openai/gpt-oss-120b-2026-01"))
    assert result["chat"]["model_echo"] == "openai/gpt-oss-120b-2026-01"


async def test_ping_is_one_user_message_at_the_configured_temperature():
    provider = Provider()
    row = openai_row()
    await run(row, provider)
    ping = provider.bodies[0]
    assert ping["messages"] == [{"role": "user", "content": PROBE_PROMPT}]
    assert ping["temperature"] == row.sampling.temperature  # the real planning setting
    assert ping["model"] == row.model and "response_format" not in ping


async def test_schema_roundtrip_sends_the_planning_schema():
    provider = Provider()
    await run(openai_row(), provider)
    body = provider.bodies[1]
    assert body["response_format"]["json_schema"]["schema"] == wire.planning_schema()
    assert body["temperature"] == 0 and body["max_tokens"] == wire.PROBE_MAX_TOKENS
    assert body["messages"][0]["role"] == "system"


async def test_ollama_probe_uses_native_paths_and_bodies():
    provider = Provider(kind="ollama", models=["qwen3:8b"])
    result = await run(ollama_row(), provider, key="")
    assert result["ok"] is True
    assert [r.url.path for r in provider.requests] == ["/api/tags", "/api/chat", "/api/chat"]
    ping, plan = provider.bodies
    assert "format" not in ping and ping["think"] is False and ping["stream"] is False
    assert plan["format"] == wire.planning_schema()
    assert all("authorization" not in r.headers for r in provider.requests)  # no key, no header


# ---------------------------------------------------------------- failure text
async def test_a_dead_model_id_surfaces_the_providers_own_sentence():
    message = "The model `openai/gpt-oss-120b` does not exist or you do not have access to it."
    provider = Provider(chat_status=404, chat_error={"error": {"message": message}})
    result = await run(openai_row(), provider)
    assert result["ok"] is False
    assert result["chat"]["error"] == f"HTTP 404: {message}"  # verbatim, not summarised
    assert result["schema_roundtrip"] is None  # skipped: it would fail the same way
    assert message in result["error"]


async def test_an_error_without_a_structured_body_falls_through_untouched():
    result = await run(openai_row(), Provider(chat_status=502, chat_error="upstream connect error"))
    assert result["chat"]["error"] == "HTTP 502: upstream connect error"


async def test_a_key_echoed_back_in_an_error_is_redacted_to_its_hint():
    provider = Provider(chat_status=401, chat_error={"error": {"message": f"Incorrect API key provided: {KEY}"}})
    result = await run(openai_row(), provider)
    text = json.dumps(result)
    assert KEY not in text
    assert "…4f2a" in result["chat"]["error"]
    assert "Incorrect API key provided" in result["chat"]["error"]  # the rest still readable


async def test_the_key_reaches_the_provider_as_a_bearer_header_only():
    provider = Provider()
    result = await run(openai_row(), provider)
    assert {r.headers["authorization"] for r in provider.requests} == {f"Bearer {KEY}"}
    assert KEY not in json.dumps(result)


# ---------------------------------------------------------------- unreachable, timeouts, no retries
@pytest.mark.parametrize("exc", [httpx.ConnectError("[Errno 111] Connection refused"),
                                 httpx.ConnectTimeout("timed out")])
async def test_unreachable_is_one_clean_error_and_stops(exc):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise type(exc)(str(exc), request=request)

    result = await probe_provider(ollama_row(), "", transport=httpx.MockTransport(handler))
    assert result["ok"] is False and calls == ["/api/tags"]  # no further steps, no retries
    assert result["error"].startswith("unreachable: http://127.0.0.1:11434 (")
    assert type(exc).__name__ in result["error"]
    assert result["chat"] is None and result["schema_roundtrip"] is None


async def test_requests_carry_the_ten_second_timeout():
    provider = Provider()
    await run(openai_row(), provider)
    assert TIMEOUT_S == 10.0
    assert all(r.extensions["timeout"]["read"] == TIMEOUT_S for r in provider.requests)


async def test_a_5xx_is_not_retried():
    provider = Provider(chat_status=503, chat_error={"error": {"message": "overloaded"}})
    await run(openai_row(), provider)
    assert len(provider.bodies) == 1


async def test_probe_never_raises():
    def handler(request):
        raise RuntimeError("something unexpected")

    result = await probe_provider(openai_row(), KEY, transport=httpx.MockTransport(handler))
    assert result["ok"] is False and "RuntimeError: something unexpected" in result["error"]


# ---------------------------------------------------------------- models
async def test_models_listing_is_informational():
    provider = Provider(models_status=404)
    result = await run(openai_row(), provider)
    assert result["models"]["ok"] is False and "no models endpoint here" in result["models"]["error"]
    assert result["ok"] is True  # discovery never stands between the operator and a working config


async def test_model_not_listed_is_reported_but_not_fatal():
    result = await run(openai_row(), Provider(models=["llama-3.3-70b"]))
    assert result["models"]["model_listed"] is False and result["ok"] is True


async def test_gemini_lists_models_with_a_prefix():
    row = openai_row(id="gemini", model="gemini-2.5-flash")
    result = await run(row, Provider(models=["models/gemini-2.5-flash"]))
    assert result["models"]["model_listed"] is True


async def test_list_models_returns_sorted_unique_ids():
    provider = Provider(models=["b-model", "a-model", "b-model"])
    assert await list_models(openai_row(), KEY, transport=httpx.MockTransport(provider)) == ["a-model", "b-model"]


async def test_list_models_raises_models_unsupported_with_redacted_text():
    def handler(request):
        return httpx.Response(403, json={"error": {"message": f"key {KEY} may not list models"}})

    with pytest.raises(ModelsUnsupported) as excinfo:
        await list_models(openai_row(), KEY, transport=httpx.MockTransport(handler))
    assert KEY not in str(excinfo.value) and "may not list models" in str(excinfo.value)


# ---------------------------------------------------------------- schema round trip
async def test_schema_roundtrip_failure_is_reported():
    result = await run(openai_row(), Provider(plan='{"disposition": "plan"'))
    assert result["ok"] is False and result["chat"]["ok"] is True
    assert result["schema_roundtrip"]["ok"] is False
    assert "not valid JSON" in result["schema_roundtrip"]["error"]


async def test_schema_violation_lists_the_fields():
    result = await run(openai_row(), Provider(plan=json.dumps({"disposition": "fly"})))
    sr = result["schema_roundtrip"]
    assert sr["ok"] is False and sr["errors"] and "plan_proposal.v1" in sr["error"]


async def test_truncated_roundtrip_is_reported():
    result = await run(ollama_row(), Provider(kind="ollama", models=["qwen3:8b"], finish="length"), key="")
    assert result["schema_roundtrip"]["ok"] is False and "truncated" in result["schema_roundtrip"]["error"]


async def test_result_shape_is_documented():
    result = await run(openai_row(), Provider())
    assert set(result) == {"ok", "provider_id", "kind", "base_url", "model", "checked_at", "latency_ms",
                           "models", "chat", "schema_roundtrip", "error"}
    assert set(result["chat"]) == {"ok", "latency_ms", "model_echo", "error"}
    assert set(result["schema_roundtrip"]) == {"ok", "latency_ms", "error", "errors"}
    assert probe_mod.PROBE_PROMPT == "reply with: ok"
