"""Router + wire: one request body per (kind, structured_mode), failover only on
transport errors, keys only through env vars, provider text redacted.

Every endpoint is an httpx.MockTransport; nothing leaves the process.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from hive_exec.config import QwenSettings
from hive_exec.llm import wire
from hive_exec.llm.providers import ProviderRow, Sampling, load_store
from hive_exec.llm.router import (
    NotConfigured,
    ProviderHTTPError,
    ProviderResponseError,
    Router,
    TransportFailure,
)

KEY = "gsk_ROUTERTEST_never_real_0123456789wxyz"
SCHEMA = wire.planning_schema()

ROWS = {
    "ollama-local": dict(kind="ollama", base_url="http://ollama.invalid:11434", model="qwen3:8b",
                         structured_mode="ollama_format"),
    "ollama-json": dict(kind="ollama", base_url="http://ollamajson.invalid:11434", model="qwen3:8b",
                        structured_mode="json_object"),
    "arc": dict(kind="openai_compatible", base_url="http://arc.invalid:8000/v1", model="Qwen/Qwen3-8B",
                structured_mode="structured_outputs"),
    "old-vllm": dict(kind="openai_compatible", base_url="http://oldvllm.invalid/v1", model="Qwen/Qwen3-8B",
                     structured_mode="guided_json"),
    "groq": dict(kind="openai_compatible", base_url="https://groq.invalid/openai/v1", model="openai/gpt-oss-120b",
                 structured_mode="json_schema", sampling=Sampling(top_k=None, seed=None)),
    "lmstudio": dict(kind="openai_compatible", base_url="http://lmstudio.invalid:1234/v1", model="qwen/qwen3-8b",
                     structured_mode="json_object", sampling=Sampling(top_k=None)),
}


def make_store(chain, keys=None):
    store = load_store()
    for pid, fields in ROWS.items():
        store.upsert(ProviderRow(id=pid, label=pid.upper(), **fields), api_key=(keys or {}).get(pid))
    store.set_role("executive", chain)
    store.save()


class Recorder:
    """A MockTransport handler: per-host behaviour, every request kept."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        action = self.behaviour.get(request.url.host, "ok")
        if isinstance(action, Exception):
            raise type(action)(str(action), request=request)
        if isinstance(action, httpx.Response):
            return action
        if action == "ok":
            return httpx.Response(200, json={"model": "served-model", "message": {"content": "{}"},
                                             "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
        raise AssertionError(action)

    def bodies(self):
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]

    def hosts(self):
        return [r.url.host for r in self.requests]


def router_for(behaviour=None, **kwargs):
    rec = Recorder(behaviour or {})
    return Router(transport=httpx.MockTransport(rec), **kwargs), rec


def body_for(row):
    return wire.chat_body(row, "SYSTEM", "USER", SCHEMA)


# -- one body per kind and mode ---------------------------------------------------------
async def test_ollama_format_body_is_exec_v2s_native_chat():
    make_store(["ollama-local"])
    router, rec = router_for()
    result = await router.chat("executive", body_for)
    req = rec.requests[0]
    assert (req.method, req.url.path) == ("POST", "/api/chat")
    body = rec.bodies()[0]
    assert body["format"] == SCHEMA and body["stream"] is False and body["think"] is False
    assert body["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}]
    assert body["options"] == {"temperature": 0.2, "top_p": 0.8, "seed": 1234, "num_predict": 3072, "num_ctx": 12288,
                               "top_k": 20}
    assert "authorization" not in req.headers
    assert result.row.id == "ollama-local" and result.fallback_used is False


async def test_ollama_json_object_sends_format_json():
    make_store(["ollama-json"])
    router, rec = router_for()
    await router.chat("executive", body_for)
    assert rec.bodies()[0]["format"] == "json"


async def test_structured_outputs_body_is_exec_v2s_vllm_body():
    make_store(["arc"])
    router, rec = router_for()
    await router.chat("executive", body_for)
    assert rec.requests[0].url.path == "/v1/chat/completions"
    body = rec.bodies()[0]
    assert body["structured_outputs"] == {"json": SCHEMA}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert (body["temperature"], body["top_p"], body["seed"], body["max_tokens"], body["top_k"]) == (
        0.2, 0.8, 1234, 3072, 20)
    assert "guided_json" not in body and "response_format" not in body


async def test_guided_json_body_for_older_vllm():
    make_store(["old-vllm"])
    router, rec = router_for()
    await router.chat("executive", body_for)
    body = rec.bodies()[0]
    assert body["guided_json"] == SCHEMA and "structured_outputs" not in body
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


async def test_json_schema_body_uses_response_format_and_omits_vllm_extras():
    make_store(["groq"], keys={"groq": KEY})
    router, rec = router_for()
    await router.chat("executive", body_for)
    body = rec.bodies()[0]
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "plan_proposal", "schema": SCHEMA}}
    assert "chat_template_kwargs" not in body and "top_k" not in body and "seed" not in body
    assert rec.requests[0].headers["authorization"] == f"Bearer {KEY}"


async def test_json_object_body_parses_then_validates():
    make_store(["lmstudio"])
    router, rec = router_for()
    await router.chat("executive", body_for)
    body = rec.bodies()[0]
    assert body["response_format"] == {"type": "json_object"}
    assert "top_k" not in body and "chat_template_kwargs" not in body


def test_planning_schema_requires_scope():
    assert "scope" in SCHEMA["required"]


# -- response normalization ---------------------------------------------------------------
def test_wire_reads_content_usage_models_and_truncation_per_kind():
    ollama = {"message": {"content": "X"}, "prompt_eval_count": 10, "eval_count": 5, "done_reason": "length"}
    assert wire.content("ollama", ollama) == "X"
    assert wire.usage("ollama", ollama) == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert wire.truncated("ollama", ollama) is True
    oai = {"choices": [{"message": {"content": "Y"}, "finish_reason": "stop"}], "usage": {"total_tokens": 3}}
    assert wire.content("openai_compatible", oai) == "Y"
    assert wire.usage("openai_compatible", oai) == {"total_tokens": 3}
    assert wire.truncated("openai_compatible", oai) is False
    assert wire.served_models("ollama", {"models": [{"name": "qwen3:8b"}, {}]}) == ["qwen3:8b"]
    assert wire.served_models("openai_compatible", {"data": [{"id": "a"}, {"id": "b"}]}) == ["a", "b"]
    assert wire.content("openai_compatible", {"choices": []}) == ""


def test_redact_replaces_the_key_with_its_hint():
    assert wire.redact(f"bad key {KEY} sent", KEY) == "bad key …wxyz sent"
    assert wire.redact("nothing here", KEY) == "nothing here"
    assert wire.redact("x", "") == "x"


# -- failover: transport errors only --------------------------------------------------------
async def test_timeout_fails_over_to_the_next_provider():
    make_store(["arc", "ollama-local"])
    router, rec = router_for({"arc.invalid": httpx.ReadTimeout("read timed out")})
    result = await router.chat("executive", body_for)
    assert rec.hosts() == ["arc.invalid", "ollama.invalid"]
    assert result.row.id == "ollama-local" and result.fallback_used is True
    assert len(result.failovers) == 1 and result.failovers[0].startswith("arc: ")
    assert "ReadTimeout" in result.failovers[0]
    # the fallback got its own kind's body, not the primary's
    assert "format" in rec.bodies()[1] and "structured_outputs" in rec.bodies()[0]


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("connection refused"),
    httpx.ConnectTimeout("connect timed out"),
    httpx.Response(503, json={"error": {"message": "overloaded"}}),
    httpx.Response(500, text="internal"),
    httpx.Response(429, json={"error": {"message": "rate limited"}}),
])
async def test_these_fail_over(failure):
    make_store(["arc", "ollama-local"])
    router, rec = router_for({"arc.invalid": failure})
    result = await router.chat("executive", body_for)
    assert result.row.id == "ollama-local" and result.fallback_used


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_client_errors_do_not_fail_over(status):
    make_store(["groq", "ollama-local"], keys={"groq": KEY})
    router, rec = router_for({"groq.invalid": httpx.Response(
        status, json={"error": {"message": "The model `openai/gpt-oss-120b` does not exist"}})})
    with pytest.raises(ProviderHTTPError) as excinfo:
        await router.chat("executive", body_for)
    assert rec.hosts() == ["groq.invalid"]
    assert excinfo.value.status == status and excinfo.value.row.id == "groq"
    assert "does not exist" in str(excinfo.value)


async def test_a_non_json_200_is_a_parse_error_with_raw_not_a_failover():
    make_store(["arc", "ollama-local"])
    router, rec = router_for({"arc.invalid": httpx.Response(200, text="<html>proxy</html>")})
    with pytest.raises(ProviderResponseError) as excinfo:
        await router.chat("executive", body_for)
    assert excinfo.value.raw == "<html>proxy</html>"
    assert rec.hosts() == ["arc.invalid"]


async def test_every_provider_down_is_one_transport_failure_listing_each():
    make_store(["arc", "ollama-local"])
    router, _ = router_for({"arc.invalid": httpx.ConnectError("refused"),
                            "ollama.invalid": httpx.ReadTimeout("slow")})
    with pytest.raises(TransportFailure) as excinfo:
        await router.chat("executive", body_for)
    assert [a.split(":")[0] for a in excinfo.value.attempts] == ["arc", "ollama-local"]
    assert excinfo.value.row.id == "ollama-local"


async def test_provider_error_text_is_redacted():
    make_store(["groq"], keys={"groq": KEY})
    router, _ = router_for({"groq.invalid": httpx.Response(401, json={"error": {"message": f"Invalid API Key {KEY}"}})})
    with pytest.raises(ProviderHTTPError) as excinfo:
        await router.chat("executive", body_for)
    assert KEY not in str(excinfo.value) and "…wxyz" in str(excinfo.value)


async def test_transport_error_text_is_redacted():
    make_store(["groq", "arc"], keys={"groq": KEY})
    router, _ = router_for({"groq.invalid": httpx.ConnectError(f"refused for {KEY}"),
                            "arc.invalid": httpx.ConnectError("refused")})
    with pytest.raises(TransportFailure) as excinfo:
        await router.chat("executive", body_for)
    assert KEY not in str(excinfo.value) and KEY not in json.dumps(excinfo.value.attempts)


# -- resolve, keys through env, reload on change ------------------------------------------------
def test_resolve_returns_the_primary_row_and_its_key_via_the_env_var():
    make_store(["groq", "arc"], keys={"groq": KEY})
    row, key = Router().resolve("executive")
    assert row.id == "groq" and key == KEY
    assert os.environ["GROQ_API_KEY"] == KEY


def test_an_empty_role_is_not_configured():
    make_store([])
    with pytest.raises(NotConfigured) as excinfo:
        Router().resolve("executive")
    assert "not configured" in str(excinfo.value)


def test_no_store_and_no_env_is_not_configured():
    with pytest.raises(NotConfigured):
        Router(legacy=QwenSettings(base_url="")).resolve("executive")


def test_no_store_with_qwen_env_resolves_the_env_row():
    row, _ = Router(legacy=QwenSettings(base_url="http://127.0.0.1:8000/v1")).resolve("executive")
    assert row.id == "env" and row.source == "env"


def test_router_rereads_the_store_when_its_fingerprint_changes():
    make_store(["arc"])
    router = Router()
    assert router.resolve("executive")[0].id == "arc"
    store = load_store()
    store.set_role("executive", ["ollama-local", "arc"])
    store.upsert(ProviderRow(id="extra-row-to-change-size", kind="ollama", base_url="http://x.invalid",
                             model="m", structured_mode="ollama_format"))
    store.save()
    assert router.resolve("executive")[0].id == "ollama-local"
    assert [c.row.id for c in router.chain("executive")] == ["ollama-local", "arc"]


def test_a_removed_key_is_unexported_on_reload():
    make_store(["groq"], keys={"groq": KEY})
    router = Router()
    router.resolve("executive")
    assert os.environ.get("GROQ_API_KEY") == KEY
    store = load_store()
    store.delete("groq")
    store.set_role("executive", ["arc"])
    store.save()
    router.resolve("executive")
    assert "GROQ_API_KEY" not in os.environ


def test_candidate_repr_hides_the_key():
    make_store(["groq"], keys={"groq": KEY})
    assert KEY not in repr(Router().chain("executive"))


def test_unreadable_store_is_not_configured_without_quoting_the_file(tmp_path):
    from hive_exec.llm.providers import store_path
    store_path().write_text(f"providers: [unclosed {KEY}\n", encoding="utf-8")
    with pytest.raises(NotConfigured) as excinfo:
        Router().resolve("executive")
    assert KEY not in str(excinfo.value) and "not valid YAML" in str(excinfo.value)


def test_planning_sampling_is_low_temperature():
    from hive_exec.llm.templates import HOSTED_SAMPLING, QWEN_SAMPLING, TEMPLATES

    assert QWEN_SAMPLING["temperature"] == 0.2 and HOSTED_SAMPLING["temperature"] == 0.2
    assert all(t["sampling"]["temperature"] == 0.2 for t in TEMPLATES)
    assert Sampling().temperature == 0.2  # rows saved without explicit sampling plan at 0.2 too


# -- WS4 fix F1: Ollama must be told its context window ---------------------------------
def _ollama_row(max_tokens=None):
    make_store(["ollama-local"])
    row = Router().chain("executive")[0].row
    if max_tokens is not None:
        row = row.model_copy(update={"sampling": row.sampling.model_copy(update={"max_tokens": max_tokens})})
    return row


def test_ollama_body_always_carries_num_ctx_from_the_planner_input_limit_and_max_tokens():
    from hive_exec.context import MAX_INPUT_TOKENS

    body = wire.chat_body(_ollama_row(), "SYSTEM", "USER", SCHEMA)
    assert body["options"]["num_ctx"] == MAX_INPUT_TOKENS + 3072 == 12288
    assert wire.chat_body(_ollama_row(), "S", "U", None)["options"]["num_ctx"] == 12288  # free text too
    assert wire.chat_body(_ollama_row(), "S", "U", SCHEMA, probe=True)["options"]["num_ctx"] == 12288


def test_ollama_num_ctx_follows_the_rows_max_tokens():
    body = wire.chat_body(_ollama_row(2048), "SYSTEM", "USER", SCHEMA)
    assert body["options"]["num_ctx"] == 9216 + 2048
    assert body["options"]["num_predict"] == 2048


def test_probe_and_planning_calls_send_the_same_num_ctx_for_a_row():
    row = _ollama_row(2048)
    planning = wire.chat_body(row, "SYSTEM", "USER", SCHEMA)
    ping = wire.chat_body(row, "", "ping", None, max_tokens=wire.PROBE_MAX_TOKENS)
    schema_probe = wire.chat_body(row, "SYSTEM", "USER", SCHEMA, probe=True, max_tokens=wire.PROBE_MAX_TOKENS)
    assert planning["options"]["num_ctx"] == ping["options"]["num_ctx"] == schema_probe["options"]["num_ctx"]
    assert ping["options"]["num_predict"] == wire.PROBE_MAX_TOKENS  # the output limit still differs; the window must not


@pytest.mark.parametrize("pid", ["arc", "ollama-json"])
def test_only_ollama_bodies_have_num_ctx_in_options(pid):
    make_store([pid])
    row = Router().chain("executive")[0].row
    body = wire.chat_body(row, "SYSTEM", "USER", SCHEMA)
    if row.kind == "ollama":
        assert "num_ctx" in body["options"]
    else:
        assert "num_ctx" not in json.dumps(body) and "options" not in body
