"""/api/exec/llm: the Models page's HTTP surface. Keys are write-only.

Ported from Bhavya007-17/hive@4fa0156 src/hive_dashboard_server/test/test_providers_api.py.
The summer suite drove a flat provider list and a ROS reload service; this one
drives the provider store with roles as ordered chains and the router's
fingerprint re-read. What carries over unchanged: no response this API can
produce contains a key (the raw HTTP text is grepped, failure paths included),
a blank key keeps the stored one, and the provider's own error text comes back
from the test button verbatim.
"""

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from hive_exec.api import create_app
from hive_exec.config import ExecSettings, QwenSettings
from hive_exec.executive import Executive
from hive_exec.gateway.fake import FakeGateway
from hive_exec.llm.providers import ENV_ROW_ID, store_path
from hive_exec.llm.router import Router
from hive_exec.llm.templates import ARC_TUNNEL
from hive_exec.planner.baseline import BaselinePlanner
from hive_exec.planner.llm import LLMPlanner

from .conftest import DEMO
from .llm_mock import MOCK_HOST, MOCK_MODEL, ScriptedQwen
from .test_llm_planner import OBS

KEY = "sk-test-NOT-A-REAL-KEY-4f2a"
GEMINI_KEY = "AIza-test-NOT-A-REAL-KEY-9c1d"
LEGACY_KEY = "legacy-test-NOT-A-REAL-KEY-77e0"

GROQ = {"id": "groq", "label": "Groq", "kind": "openai_compatible", "base_url": f"http://{MOCK_HOST}/v1",
        "model": MOCK_MODEL, "structured_mode": "structured_outputs"}
GEMINI = {"id": "gemini", "label": "Gemini", "kind": "openai_compatible",
          "base_url": "https://gemini.example.test/v1beta/openai", "model": "gemini-2.5-flash",
          "structured_mode": "json_schema"}


def make_client(handler=None, legacy: QwenSettings = None):
    """An app whose LLM router speaks to ``handler`` (a MockTransport), never the network."""
    settings = ExecSettings(sim_time_scale=0.005, default_planner="baseline",
                            qwen=legacy or QwenSettings(base_url=""))
    transport = httpx.MockTransport(handler or ScriptedQwen([]).handler)
    planners = {"llm": LLMPlanner(Router(legacy=settings.qwen, transport=transport)),
                "baseline": BaselinePlanner()}
    ex = Executive(FakeGateway(time_scale=settings.sim_time_scale), planners, default_planner="baseline")
    return TestClient(create_app(settings, executive=ex))


def post(c, row, api_key=None):
    body = dict(row)
    if api_key is not None:
        body["api_key"] = api_key
    return c.post("/api/exec/llm/providers", json=body)


def entry(view, pid):
    return next(p for p in view["providers"] if p["id"] == pid)


# ---------------------------------------------------------------- reads
def test_providers_start_empty():
    with make_client() as c:
        view = c.get("/api/exec/llm/providers").json()
    assert view["providers"] == []
    assert view["roles"] == {"executive": [], "instance_agent": []}
    assert view["problems"] == []
    assert view["store"]["path"] == str(store_path()) and view["store"]["exists"] is False
    assert view["store"]["env_row"] is False


# ---------------------------------------------------------------- THE write-only contract
def test_get_after_post_reports_presence_and_a_hint_never_the_key():
    with make_client() as c:
        assert post(c, GROQ, KEY).status_code == 200
        response = c.get("/api/exec/llm/providers")
    row = entry(response.json(), "groq")
    assert row["key_present"] is True and row["key_hint"] == "…4f2a"
    assert set(row) == {"id", "label", "kind", "base_url", "model", "structured_mode", "sampling", "key_env",
                        "key_present", "key_hint", "roles", "source", "read_only"}
    assert KEY not in response.text  # the raw body, not the parsed one


def test_no_response_this_api_can_produce_contains_a_key():
    """Every route, failure paths and validation errors included, grepped for key material."""
    mock = ScriptedQwen([json.dumps(OBS)])

    def handler(request):
        if request.url.host == "gemini.example.test":  # a provider that echoes the key back
            auth = request.headers.get("authorization", "")
            return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {auth}"}})
        return mock.handler(request)

    with make_client(handler, legacy=QwenSettings(base_url="http://127.0.0.1:8000/v1", model="Qwen/Qwen3-8B",
                                                  api_key=LEGACY_KEY)) as c:
        bodies = [
            c.get("/api/exec/llm/providers").text,  # the env row, before any store exists
            post(c, GROQ, KEY).text,
            post(c, GEMINI, GEMINI_KEY).text,
            post(c, dict(GROQ, model="renamed"), "").text,  # blank key keeps the stored one
            c.get("/api/exec/llm/providers").text,
            c.get("/api/exec/llm/roles").text,
            c.post("/api/exec/llm/roles", json={"executive": ["groq", "gemini"]}).text,
            c.post("/api/exec/llm/roles", json={"executive": ["nope"]}).text,  # 404
            c.post("/api/exec/llm/providers/groq/test", json={}).text,
            c.post("/api/exec/llm/providers/gemini/test").text,  # 401 echoing the key
            c.post("/api/exec/llm/providers/nope/test").text,  # 404
            c.get("/api/exec/llm/templates").text,
            # validation failures, each carrying a key in the request
            post(c, {k: v for k, v in GROQ.items() if k != "model"}, KEY).text,  # missing field
            post(c, dict(GROQ, apikey=KEY), KEY).text,  # unknown field holding a key
            post(c, dict(GROQ, structured_mode="ollama_format"), KEY).text,  # mode not for kind
            post(c, dict(GROQ, id=ENV_ROW_ID), KEY).text,  # reserved id
            post(c, dict(GROQ, sampling={"temperature": 9}), KEY).text,  # out of range
            post(c, GROQ, KEY + "\nsecond line").text,  # a key that is not one line
            c.post("/api/exec/llm/providers", content=f'{{"id": "groq", "api_key": "{KEY}"',
                   headers={"content-type": "application/json"}).text,  # broken JSON
            c.get("/api/exec/planner").text,
            c.get("/api/exec/health").text,  # carries the last probe error
            c.get("/api/exec/state").text,
        ]
        c.post("/api/exec/reset", json={"scenario": "unknown"})
        bodies.append(c.post("/api/exec/missions", json={"command": DEMO, "planner": "llm"}).text)
        bodies += [c.get("/api/exec/export").text, c.get("/api/exec/events").text,
                   c.delete("/api/exec/llm/providers/gemini").text]
    joined = "\n".join(bodies)
    for key in (KEY, GEMINI_KEY, LEGACY_KEY):
        assert key not in joined
    assert "Incorrect API key provided: Bearer …9c1d" in joined  # the provider's sentence, key redacted


def test_validation_errors_name_the_field_and_never_echo_input():
    with make_client() as c:
        response = post(c, {k: v for k, v in GROQ.items() if k != "model"}, KEY)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("model" in str(d.get("loc")) for d in detail)
    assert all("input" not in d and "ctx" not in d for d in detail)


def test_a_mode_the_kind_cannot_use_is_a_422_that_says_why():
    with make_client() as c:
        response = post(c, dict(GROQ, structured_mode="ollama_format"), KEY)
    assert response.status_code == 422
    assert "structured_mode ollama_format is not available" in response.text


def test_a_key_that_is_not_one_line_is_refused():
    with make_client() as c:
        assert post(c, GROQ, KEY + "\nsecond line").status_code == 422
        assert c.get("/api/exec/llm/providers").json()["providers"] == []


# ---------------------------------------------------------------- POST semantics
def test_a_blank_or_omitted_key_keeps_the_stored_one():
    with make_client() as c:
        post(c, GROQ, KEY)
        post(c, dict(GROQ, model="other-model"), "")
        view = post(c, dict(GROQ, model="other-model", label="Groq again")).json()  # no api_key field
    row = entry(view, "groq")
    assert row["key_present"] is True and row["key_hint"] == "…4f2a"
    assert row["model"] == "other-model" and row["label"] == "Groq again"
    assert store_path().read_text(encoding="utf-8").count(KEY) == 1


def test_a_new_key_replaces_the_stored_one():
    with make_client() as c:
        post(c, GROQ, KEY)
        view = post(c, GROQ, "sk-test-REPLACEMENT-key-0b0b").json()
    assert entry(view, "groq")["key_hint"] == "…0b0b"
    assert KEY not in store_path().read_text(encoding="utf-8")


def test_post_returns_the_same_view_as_get():
    with make_client() as c:
        posted = post(c, GROQ, KEY).json()
        got = c.get("/api/exec/llm/providers").json()
    assert posted == got and got["store"]["exists"] is True


def test_the_store_file_is_owner_only(tmp_path, monkeypatch):
    target = tmp_path / "fresh" / "providers.yaml"
    monkeypatch.setenv("HIVE_PROVIDERS_FILE", str(target))
    with make_client() as c:
        post(c, GROQ, KEY)
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700


def test_the_env_id_is_reserved():
    with make_client() as c:
        response = post(c, dict(GROQ, id=ENV_ROW_ID), KEY)
    assert response.status_code == 400 and "reserved" in response.json()["detail"]


def test_delete_removes_the_row_and_unbinds_it():
    with make_client() as c:
        post(c, GROQ, KEY)
        post(c, GEMINI, GEMINI_KEY)
        c.post("/api/exec/llm/roles", json={"executive": ["groq", "gemini"]})
        response = c.delete("/api/exec/llm/providers/groq")
    assert response.status_code == 200
    view = response.json()
    assert [p["id"] for p in view["providers"]] == ["gemini"]
    assert view["roles"]["executive"] == ["gemini"]
    assert KEY not in store_path().read_text(encoding="utf-8")


def test_delete_of_an_unknown_provider_is_a_404_that_names_it():
    with make_client() as c:
        response = c.delete("/api/exec/llm/providers/nope")
    assert response.status_code == 404 and "nope" in response.json()["detail"]


# ---------------------------------------------------------------- roles
def test_roles_are_ordered_chains_and_a_post_may_set_one_role():
    with make_client() as c:
        post(c, GROQ, KEY)
        post(c, GEMINI, GEMINI_KEY)
        c.post("/api/exec/llm/roles", json={"instance_agent": ["gemini"]})
        view = c.post("/api/exec/llm/roles", json={"executive": ["gemini", "groq"]}).json()
        roles = c.get("/api/exec/llm/roles").json()
    assert roles == {"executive": ["gemini", "groq"], "instance_agent": ["gemini"]}
    assert view["roles"] == roles
    assert entry(view, "gemini")["roles"] == ["executive", "instance_agent"]


def test_an_empty_chain_unbinds_a_role():
    with make_client() as c:
        post(c, GROQ, KEY)
        c.post("/api/exec/llm/roles", json={"executive": ["groq"]})
        view = c.post("/api/exec/llm/roles", json={"executive": []}).json()
    assert view["roles"]["executive"] == []


def test_an_unknown_role_is_a_400_that_lists_the_known_ones():
    with make_client() as c:
        post(c, GROQ, KEY)
        response = c.post("/api/exec/llm/roles", json={"composer": ["groq"]})
    assert response.status_code == 400
    assert "executive" in response.json()["detail"] and "instance_agent" in response.json()["detail"]


def test_binding_an_unknown_provider_is_a_404_and_changes_nothing():
    with make_client() as c:
        post(c, GROQ, KEY)
        c.post("/api/exec/llm/roles", json={"executive": ["groq"]})
        response = c.post("/api/exec/llm/roles", json={"executive": ["groq", "nope"]})
        roles = c.get("/api/exec/llm/roles").json()
    assert response.status_code == 404 and "nope" in response.json()["detail"]
    assert roles["executive"] == ["groq"]


# ---------------------------------------------------------------- the test button
def test_the_test_button_probes_the_stored_row_with_its_key():
    mock = ScriptedQwen([])
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return mock.handler(request)

    with make_client(handler) as c:
        post(c, GROQ, KEY)
        before = c.get("/api/exec/events").json()["timeline"]
        result = c.post("/api/exec/llm/providers/groq/test").json()
        after = c.get("/api/exec/events").json()["timeline"]
    assert result["ok"] is True and result["provider_id"] == "groq" and result["model"] == MOCK_MODEL
    assert result["schema_roundtrip"]["ok"] is True
    assert set(seen) == {f"Bearer {KEY}"}
    assert after == before  # not logged to the mission


def test_the_test_button_probes_the_model_just_typed_without_saving_it():
    mock = ScriptedQwen([])
    with make_client(mock.handler) as c:
        post(c, GROQ, KEY)
        result = c.post("/api/exec/llm/providers/groq/test", json={"model": "not-yet-saved"}).json()
        stored = entry(c.get("/api/exec/llm/providers").json(), "groq")["model"]
    assert result["model"] == "not-yet-saved"
    assert {r["model"] for r in mock.requests} == {"not-yet-saved"}
    assert stored == MOCK_MODEL


def test_an_unreachable_provider_is_an_answer_not_a_server_error():
    def handler(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    with make_client(handler) as c:
        post(c, GROQ, KEY)
        response = c.post("/api/exec/llm/providers/groq/test")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"].startswith(f"unreachable: http://{MOCK_HOST}/v1 (")


def test_testing_an_unknown_provider_is_a_404():
    with make_client() as c:
        assert c.post("/api/exec/llm/providers/nope/test").status_code == 404


# ---------------------------------------------------------------- templates
def test_templates_include_ollama_and_the_arc_tunnel_hint():
    with make_client() as c:
        templates = c.get("/api/exec/llm/templates").json()
    ids = {t["template_id"] for t in templates}
    assert "ollama_local" in ids and "vt_arc_vllm" in ids
    arc = next(t for t in templates if t["template_id"] == "vt_arc_vllm")
    assert ARC_TUNNEL in arc["hint"]


# ---------------------------------------------------------------- a save applies to the next mission
def test_a_save_is_used_by_the_next_mission_without_a_restart():
    mock = ScriptedQwen([json.dumps(OBS), json.dumps(OBS)])
    with make_client(mock.handler) as c:
        post(c, GROQ, KEY)
        c.post("/api/exec/llm/roles", json={"executive": ["groq"]})
        c.post("/api/exec/reset", json={"scenario": "unknown"})
        first = c.post("/api/exec/missions", json={"command": DEMO, "planner": "llm"}).json()
        post(c, dict(GROQ, id="groq-b", label="Second"), KEY)
        c.post("/api/exec/llm/roles", json={"executive": ["groq-b"]})
        c.post("/api/exec/reset", json={"scenario": "unknown"})
        second = c.post("/api/exec/missions", json={"command": DEMO, "planner": "llm"}).json()
    assert first["mission"]["revisions"][-1]["planner"]["provider_id"] == "groq"
    assert second["mission"]["revisions"][-1]["planner"]["provider_id"] == "groq-b"


def test_editing_the_probed_row_clears_live_enabled():
    with make_client(ScriptedQwen([]).handler) as c:
        post(c, GROQ, KEY)
        c.post("/api/exec/llm/roles", json={"executive": ["groq"]})
        assert c.post("/api/exec/planner/probe").json()["ok"] is True
        assert c.get("/api/exec/planner").json()["llm"]["live_enabled"] is True
        post(c, dict(GROQ, base_url="http://elsewhere.invalid/v1"), "")
        status = c.get("/api/exec/planner").json()["llm"]
    assert status["endpoint"] == "http://elsewhere.invalid/v1"
    assert status["live_enabled"] is False  # the probe was of another endpoint


# ---------------------------------------------------------------- the QWEN_* env row, and a bad file
LEGACY = QwenSettings(base_url="http://127.0.0.1:8000/v1", model="Qwen/Qwen3-8B", api_key=LEGACY_KEY)


def test_the_env_row_is_listed_read_only_until_a_store_exists():
    with make_client(ScriptedQwen([]).handler, legacy=LEGACY) as c:
        before = c.get("/api/exec/llm/providers").json()
        delete = c.delete(f"/api/exec/llm/providers/{ENV_ROW_ID}")
        bind = c.post("/api/exec/llm/roles", json={"instance_agent": [ENV_ROW_ID]})
        after = post(c, GROQ, KEY).json()
    env = entry(before, ENV_ROW_ID)
    assert env["read_only"] is True and env["key_present"] is True
    assert before["roles"]["executive"] == [ENV_ROW_ID]
    assert before["store"]["env_row"] is True and "QWEN_" in before["store"]["note"]
    assert delete.status_code == 400 and bind.status_code == 400
    # The first save creates the store; from then on QWEN_* is not read.
    assert [p["id"] for p in after["providers"]] == ["groq"]
    assert after["roles"]["executive"] == [] and after["store"]["env_row"] is False
    assert LEGACY_KEY not in store_path().read_text(encoding="utf-8")


def test_a_store_that_does_not_parse_is_reported_and_never_overwritten():
    target = store_path()
    target.write_text("providers: [unclosed\n", encoding="utf-8")
    os.chmod(target, 0o600)
    with make_client() as c:
        view = c.get("/api/exec/llm/providers").json()
        write = post(c, GROQ, KEY)
        roles = c.post("/api/exec/llm/roles", json={"executive": []})
        delete = c.delete("/api/exec/llm/providers/groq")
        test = c.post("/api/exec/llm/providers/groq/test")
    assert view["providers"] == [] and any("not valid YAML" in p for p in view["problems"])
    assert view["store"]["exists"] is True
    assert {write.status_code, roles.status_code, delete.status_code, test.status_code} == {409}
    assert target.read_text(encoding="utf-8") == "providers: [unclosed\n"


@pytest.mark.parametrize("path", ["/api/exec/llm/providers", "/api/exec/llm/roles", "/api/exec/llm/templates"])
def test_routes_are_mounted(path):
    with make_client() as c:
        assert c.get(path).status_code == 200
