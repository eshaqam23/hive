"""Offline spec for hive_exec.llm.providers: the provider + role store.

Ported from Bhavya007-17/hive src/hive_llm/test/test_providers.py and adapted to
this repo's rows (kind, structured_mode, sampling), roles (executive,
instance_agent), the summer v1 file that may already sit at ~/.hive, and the
QWEN_* env migration row.

The conftest autouse fixture points HIVE_PROVIDERS_FILE at tmp_path, so nothing
here can read or write the operator's real ~/.hive/providers.yaml.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
import yaml

from hive_exec.config import QwenSettings
from hive_exec.llm.providers import (
    DEFAULT_RELATIVE_PATH,
    ENV_ROW_ID,
    FILE_MODE,
    ROLES,
    ProviderRow,
    Sampling,
    env_var_for,
    export_keys,
    fingerprint,
    key_hint,
    load_store,
    store_path,
)
from hive_exec.llm.templates import TEMPLATES

FAKE_KEY = "gsk_TESTKEY_do_not_use_0123456789abcdef4f2a"
OTHER_KEY = "AIzaSyTESTKEY_second_provider_9876543210zzzz"


@pytest.fixture(autouse=True)
def _clean_key_env(monkeypatch):
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY", "QWEN_API_KEY",
                "HIVE_KEY_LOCALBOX", "HIVE_KEY_ARC"):
        monkeypatch.delenv(var, raising=False)


def groq_row(**over) -> ProviderRow:
    data = dict(id="groq", label="Groq", kind="openai_compatible",
                base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b",
                structured_mode="json_schema", sampling=Sampling(top_k=None))
    data.update(over)
    return ProviderRow(**data)


def ollama_row(**over) -> ProviderRow:
    data = dict(id="ollama-local", label="Ollama (local)", kind="ollama", base_url="http://127.0.0.1:11434",
                model="qwen3:8b", structured_mode="ollama_format")
    data.update(over)
    return ProviderRow(**data)


def _saved():
    store = load_store()
    store.upsert(groq_row(), api_key=FAKE_KEY)
    store.upsert(ollama_row())
    store.upsert(groq_row(id="gemini", label="Gemini", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                          model="gemini-2.5-flash"), api_key=OTHER_KEY)
    store.set_role("executive", ["groq", "ollama-local"])
    store.set_role("instance_agent", ["gemini"])
    store.save()
    return store


def _values(node) -> list:
    if isinstance(node, dict):
        return [v for child in node.values() for v in _values(child)]
    if isinstance(node, (list, tuple)):
        return [v for child in node for v in _values(child)]
    return [str(node)]


# -- where the file lives -----------------------------------------------------------
def test_default_path_is_dot_hive_providers_yaml(monkeypatch):
    monkeypatch.delenv("HIVE_PROVIDERS_FILE", raising=False)
    assert DEFAULT_RELATIVE_PATH == (".hive", "providers.yaml")
    assert store_path().parts[-2:] == DEFAULT_RELATIVE_PATH


def test_env_var_overrides_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_PROVIDERS_FILE", str(tmp_path / "elsewhere.yaml"))
    assert store_path() == tmp_path / "elsewhere.yaml"


def test_missing_file_loads_an_empty_store():
    store = load_store()
    assert store.public() == []
    assert store.roles() == {role: [] for role in ROLES}


def test_roles_are_executive_and_instance_agent():
    assert ROLES == ("executive", "instance_agent")


# -- permissions --------------------------------------------------------------------
def test_saved_file_is_0600():
    _saved()
    assert oct(stat.S_IMODE(store_path().stat().st_mode)) == oct(FILE_MODE) == "0o600"


def test_saved_parent_directory_is_0700(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_PROVIDERS_FILE", str(tmp_path / "nested" / "providers.yaml"))
    _saved()
    assert stat.S_IMODE((tmp_path / "nested").stat().st_mode) == 0o700


def test_load_repairs_a_loosened_mode():
    _saved()
    os.chmod(store_path(), 0o644)
    load_store()
    assert stat.S_IMODE(store_path().stat().st_mode) == FILE_MODE


def test_no_temp_file_is_left_behind():
    _saved()
    assert [p.name for p in store_path().parent.iterdir() if p.name != "providers.yaml"] == []


# -- the write-only contract ----------------------------------------------------------
def test_public_view_has_no_key_anywhere():
    public = _saved().public()
    text = json.dumps(public)
    assert FAKE_KEY not in text and OTHER_KEY not in text
    assert not any(FAKE_KEY in v or OTHER_KEY in v for v in _values(public))


def test_public_view_reports_presence_and_a_four_char_hint():
    entry = next(e for e in _saved().public() if e["id"] == "groq")
    assert entry["key_present"] is True
    assert entry["key_hint"] == "…4f2a"
    assert entry["key_env"] == "GROQ_API_KEY"
    assert entry["kind"] == "openai_compatible" and entry["structured_mode"] == "json_schema"
    assert entry["sampling"]["top_k"] is None and entry["sampling"]["think"] is False
    assert "api_key" not in entry and "key" not in entry


def test_short_key_gets_no_hint_characters():
    assert key_hint("abc") == "…"
    assert key_hint("1234567") == "…"
    assert key_hint("") == ""
    assert key_hint("12345678") == "…5678"


def test_public_view_reports_a_keyless_provider_honestly():
    store = load_store()
    store.upsert(ollama_row())
    entry = store.public()[0]
    assert entry["key_present"] is False and entry["key_hint"] == ""


def test_provider_row_model_has_no_key_field():
    row = _saved().row("groq")
    assert "api_key" not in row.model_dump() and "key" not in row.model_dump()
    assert FAKE_KEY not in json.dumps(row.model_dump())
    assert FAKE_KEY not in repr(_saved())


def test_public_view_lists_roles_bound_to_each_provider():
    public = {e["id"]: e for e in _saved().public()}
    assert public["groq"]["roles"] == ["executive"]
    assert public["ollama-local"]["roles"] == ["executive"]
    assert public["gemini"]["roles"] == ["instance_agent"]


# -- upsert semantics -------------------------------------------------------------------
def test_upsert_without_a_key_keeps_the_stored_one():
    _saved()
    store = load_store()
    store.upsert(groq_row(model="llama-3.3-70b-versatile"))
    store.save()
    reloaded = load_store()
    assert reloaded.api_key("groq") == FAKE_KEY
    assert reloaded.row("groq").model == "llama-3.3-70b-versatile"


def test_upsert_with_an_empty_key_is_omission_not_erasure():
    _saved()
    store = load_store()
    store.upsert(groq_row(), api_key="")
    store.save()
    assert load_store().api_key("groq") == FAKE_KEY


def test_upsert_with_a_new_key_replaces_it():
    _saved()
    store = load_store()
    store.upsert(groq_row(), api_key="gsk_REPLACED_aaaa")
    store.save()
    assert load_store().api_key("groq") == "gsk_REPLACED_aaaa"


def test_delete_drops_the_key_and_unbinds_the_role():
    _saved()
    store = load_store()
    assert store.delete("groq") is True
    store.save()
    reloaded = load_store()
    assert reloaded.api_key("groq") is None
    assert reloaded.roles()["executive"] == ["ollama-local"]
    assert FAKE_KEY not in store_path().read_text(encoding="utf-8")


def test_delete_of_an_unknown_provider_is_false():
    assert load_store().delete("never-existed") is False


# -- rows --------------------------------------------------------------------------------
@pytest.mark.parametrize("kind,mode", [("ollama", "json_schema"), ("ollama", "structured_outputs"),
                                       ("openai_compatible", "ollama_format")])
def test_kind_and_structured_mode_must_fit(kind, mode):
    with pytest.raises(ValueError):
        groq_row(kind=kind, structured_mode=mode)


@pytest.mark.parametrize("mode", ["json_schema", "structured_outputs", "guided_json", "json_object"])
def test_openai_compatible_accepts_every_openai_mode(mode):
    assert groq_row(structured_mode=mode).structured_mode == mode


def test_think_cannot_be_turned_on():
    with pytest.raises(ValueError):
        Sampling(think=True)


def test_base_url_must_be_http():
    with pytest.raises(ValueError):
        groq_row(base_url="file:///etc/passwd")


def test_env_row_id_is_reserved():
    with pytest.raises(ValueError):
        load_store().upsert(groq_row(id=ENV_ROW_ID))


# -- env-var naming: how a key reaches the client -------------------------------------------
def test_known_hosted_presets_keep_their_documented_env_var():
    assert env_var_for("groq") == "GROQ_API_KEY"
    assert env_var_for("gemini") == "GEMINI_API_KEY"
    assert env_var_for("cerebras") == "CEREBRAS_API_KEY"
    assert env_var_for("openrouter") == "OPENROUTER_API_KEY"
    assert env_var_for("together") == "TOGETHER_API_KEY"


def test_an_unknown_provider_gets_a_derived_env_var():
    assert env_var_for("localbox") == "HIVE_KEY_LOCALBOX"
    assert env_var_for("my-vllm.host") == "HIVE_KEY_MY_VLLM_HOST"


def test_an_explicit_env_var_wins():
    assert env_var_for("groq", "MY_OWN_VAR") == "MY_OWN_VAR"


def test_export_keys_sets_the_named_env_var_and_undoes_it():
    store = _saved()
    exported = export_keys(store, {})
    assert os.environ["GROQ_API_KEY"] == FAKE_KEY
    assert store.key_env("groq") in exported
    store.delete("groq")
    export_keys(store, exported)
    assert "GROQ_API_KEY" not in os.environ


def test_unexport_restores_a_shell_value(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-the-shell")
    store = _saved()
    exported = export_keys(store, {})
    assert os.environ["GROQ_API_KEY"] == FAKE_KEY
    store.delete("groq")
    export_keys(store, exported)
    assert os.environ["GROQ_API_KEY"] == "from-the-shell"


def test_key_present_when_only_the_environment_has_it(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "from-the-shell-1234")
    store = load_store()
    store.upsert(groq_row(id="cerebras", base_url="https://api.cerebras.ai/v1", model="gpt-oss-120b"))
    entry = store.public()[0]
    assert entry["key_present"] is True and entry["key_hint"] == "…1234"


# -- roles ------------------------------------------------------------------------------------
def test_role_holds_an_ordered_fallback_list():
    store = _saved()
    assert store.roles()["executive"] == ["groq", "ollama-local"]
    assert [r.id for r in store.chain("executive")] == ["groq", "ollama-local"]


def test_set_role_dedupes_and_keeps_order():
    store = _saved()
    store.set_role("executive", ["ollama-local", "groq", "ollama-local"])
    assert store.roles()["executive"] == ["ollama-local", "groq"]


def test_set_role_rejects_an_unknown_provider():
    with pytest.raises(KeyError):
        _saved().set_role("executive", ["nope"])


def test_unknown_role_is_rejected_by_name():
    with pytest.raises(KeyError) as excinfo:
        load_store().set_role("navigator", [])
    assert "navigator" in str(excinfo.value)


# -- persistence ------------------------------------------------------------------------------
def test_yaml_round_trip_preserves_rows_roles_and_sampling():
    _saved()
    store = load_store()
    assert store.ids() == ["gemini", "groq", "ollama-local"]
    assert store.row("ollama-local").sampling.top_k == 20
    assert store.row("groq").sampling.top_k is None
    assert store.roles()["instance_agent"] == ["gemini"]


def test_written_yaml_is_versioned_and_summer_readable():
    _saved()
    data = yaml.safe_load(store_path().read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["providers"]["groq"]["api_key"] == FAKE_KEY  # the one place a key lives
    assert data["roles"]["executive"] == {"provider_id": "groq", "fallbacks": ["ollama-local"]}


def test_fingerprint_changes_when_the_file_changes():
    assert fingerprint() is None
    _saved()
    after = fingerprint()
    store = load_store()
    store.upsert(groq_row(model="a-different-model"))
    store.save()
    assert after is not None and fingerprint() != after


def test_a_bad_row_is_reported_without_its_key_and_the_rest_still_load():
    store_path().write_text(yaml.safe_dump({"version": 2, "providers": {
        "broken": {"kind": "telepathy", "base_url": "http://x", "model": "m", "api_key": FAKE_KEY},
        "ollama-local": {"kind": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen3:8b"},
    }}), encoding="utf-8")
    store = load_store()
    assert store.ids() == ["ollama-local"]
    assert store.problems and "broken" in store.problems[0]
    assert FAKE_KEY not in json.dumps(store.problems)


# -- the summer v1 file that may already be at ~/.hive ------------------------------------------
SUMMER_V1 = {
    "version": 1,
    "providers": {"gemini": {"api_key": OTHER_KEY, "model": "gemini-2.0-flash"},
                  "groq": {"api_key": FAKE_KEY, "base_url": "https://api.groq.com/openai/v1",
                           "model": "openai/gpt-oss-120b"}},
    "roles": {"composer": {"provider_id": "groq", "model": ""},
              "executive": {"provider_id": "groq", "model": ""}},
}


def _write_summer():
    store_path().write_text(yaml.safe_dump(SUMMER_V1), encoding="utf-8")
    os.chmod(store_path(), 0o600)


def test_summer_v1_file_loads_with_keys_and_the_executive_binding():
    _write_summer()
    store = load_store()
    assert store.ids() == ["gemini", "groq"]
    gemini = store.row("gemini")
    assert gemini.kind == "openai_compatible" and gemini.structured_mode == "json_schema"
    assert gemini.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert gemini.sampling.top_k is None
    assert store.api_key("groq") == FAKE_KEY
    assert store.roles()["executive"] == ["groq"]
    assert store.problems == []


def test_first_save_over_a_v1_file_keeps_a_private_backup_and_unknown_roles():
    _write_summer()
    original = store_path().read_text(encoding="utf-8")
    store = load_store()
    store.upsert(ollama_row())
    store.save()
    backup = store_path().with_name("providers.yaml.v1.bak")
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    data = yaml.safe_load(store_path().read_text(encoding="utf-8"))
    assert data["roles"]["composer"] == {"provider_id": "groq", "model": ""}
    assert data["providers"]["gemini"]["api_key"] == OTHER_KEY


# -- migration: QWEN_* env and no store -------------------------------------------------------------
LEGACY = QwenSettings(provider="vllm", base_url="http://127.0.0.1:8000/v1", model="Qwen/Qwen3-8B",
                      api_key="legacy-key-abcd", structured_mode="guided_json", top_k=20)


def test_no_store_and_qwen_base_url_synthesizes_a_read_only_env_row():
    store = load_store(legacy=LEGACY)
    row = store.row(ENV_ROW_ID)
    assert row.kind == "openai_compatible" and row.structured_mode == "guided_json"
    assert row.base_url == LEGACY.base_url and row.model == LEGACY.model
    assert row.sampling.top_k == 20 and row.sampling.seed == 1234
    assert store.roles()["executive"] == [ENV_ROW_ID]
    entry = store.public()[0]
    assert entry["read_only"] is True and entry["source"] == "env"
    assert entry["key_env"] == "QWEN_API_KEY" and entry["key_hint"] == "…abcd"
    assert store.api_key(ENV_ROW_ID) == "legacy-key-abcd"


def test_env_row_for_ollama_uses_the_native_kind():
    store = load_store(legacy=QwenSettings(provider="ollama", base_url="http://127.0.0.1:11434", model="qwen3:8b"))
    row = store.row(ENV_ROW_ID)
    assert row.kind == "ollama" and row.structured_mode == "ollama_format"


def test_no_env_row_without_qwen_base_url():
    assert load_store(legacy=QwenSettings(base_url="")).ids() == []


def test_no_env_row_once_a_store_exists():
    _saved()
    assert ENV_ROW_ID not in load_store(legacy=LEGACY).ids()


def test_env_row_is_read_only_and_never_written():
    store = load_store(legacy=LEGACY)
    with pytest.raises(ValueError):
        store.delete(ENV_ROW_ID)
    store.upsert(ollama_row())
    store.save()
    data = yaml.safe_load(store_path().read_text(encoding="utf-8"))
    assert ENV_ROW_ID not in data["providers"]
    assert "legacy-key-abcd" not in store_path().read_text(encoding="utf-8")


# -- templates: empty forms, nothing created ---------------------------------------------------------
def test_templates_are_forms_and_create_nothing():
    ids = [t["template_id"] for t in TEMPLATES]
    assert ids == ["ollama_local", "vt_arc_vllm", "hosted_oss", "local_openai"]
    assert load_store().ids() == [] and fingerprint() is None


def test_ollama_template():
    t = TEMPLATES[0]
    assert (t["kind"], t["base_url"], t["model"], t["structured_mode"], t["key"]) == (
        "ollama", "http://127.0.0.1:11434", "qwen3:8b", "ollama_format", "none")
    assert "127.0.0.1" in t["hint"] and "mirrored" in t["hint"]


def test_arc_template_carries_the_tunnel_hint():
    t = TEMPLATES[1]
    assert (t["kind"], t["base_url"], t["model"], t["structured_mode"], t["key"]) == (
        "openai_compatible", "http://127.0.0.1:8000/v1", "Qwen/Qwen3-8B", "structured_outputs", "optional")
    assert "ssh -L 8000:<compute-node>:8000 <pid>@tinkercliffs1.arc.vt.edu" in t["hint"]


def test_hosted_template_presets():
    t = TEMPLATES[2]
    assert t["key"] == "required" and t["structured_mode"] == "json_schema" and t["sampling"]["top_k"] is None
    presets = {p["id"]: p for p in t["presets"]}
    assert list(presets) == ["groq", "cerebras", "openrouter", "together", "gemini"]
    assert presets["groq"]["base_url"] == "https://api.groq.com/openai/v1"
    assert presets["groq"]["model"] == "openai/gpt-oss-120b"
    assert all(p["base_url"].startswith("https://") and p["model"] for p in t["presets"])


def test_local_openai_template():
    t = TEMPLATES[3]
    assert t["kind"] == "openai_compatible" and t["key"] == "none"
    urls = {p["base_url"] for p in t["presets"]}
    assert {"http://127.0.0.1:1234/v1", "http://127.0.0.1:8080/v1"} <= urls


def test_every_template_validates_as_a_row():
    for t in TEMPLATES:
        for p in t.get("presets") or [t]:
            ProviderRow(id=p.get("id", t["template_id"]).replace("_", "-"), label=t["label"], kind=t["kind"],
                        base_url=p["base_url"], model=p["model"] or "x",
                        structured_mode=p.get("structured_mode", t["structured_mode"]), sampling=t["sampling"])
