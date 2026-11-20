"""providers.py: the LLM provider + role store the Models page writes and the
Executive's router reads.

Ported from Bhavya007-17/hive@4fa0156 src/hive_llm/hive_llm/providers.py and
extended with this repo's row shape (kind, structured_mode, sampling), roles
with ordered fallbacks, and a read-only row synthesized from QWEN_* env.

WHERE IT LIVES: ``~/.hive/providers.yaml`` (inside WSL when the Executive runs
there), file mode 0600, directory 0700, outside the repository.
``$HIVE_PROVIDERS_FILE`` overrides the path (the test suite uses it).

KEY MATERIAL NEVER ENTERS A MODEL. :class:`ProviderRow` has no key field, so no
``model_dump()`` can carry a key into a log line or an HTTP body. Keys live in
one private dict on the store, are written only to the yaml file, and reach the
HTTP client through the environment variable named by the row
(:func:`export_keys`). The API reports ``key_present`` and a four-character hint.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .templates import preset

log = logging.getLogger(__name__)

#: The sampling every row was saved with before WS2 (temperature 0.7; hosted rows have top_k None).
#: A saved row whose temperature/top_p/top_k/seed equal one of these sets exactly was never tuned
#: by the operator, so it plans at the current default temperature instead.
PRE_WS2_SAMPLING = ({"temperature": 0.7, "top_p": 0.8, "top_k": 20, "seed": 1234},
                    {"temperature": 0.7, "top_p": 0.8, "top_k": None, "seed": 1234})
_MIGRATION_LOGGED: set = set()

__all__ = [
    "DEFAULT_RELATIVE_PATH", "DIR_MODE", "ENV_ROW_ID", "FILE_MODE", "KINDS", "MODES_BY_KIND",
    "PATH_ENV", "ROLES", "ProviderRow", "ProviderStore", "ProviderStoreError", "Sampling",
    "env_var_for", "export_keys", "fingerprint", "key_hint", "load_store", "store_path",
]

DEFAULT_RELATIVE_PATH = (".hive", "providers.yaml")
PATH_ENV = "HIVE_PROVIDERS_FILE"
FILE_MODE = 0o600
DIR_MODE = 0o700
SCHEMA_VERSION = 2

#: Roles that pick their own provider chain. Adding a role is a tuple entry.
ROLES = ("executive", "instance_agent")

#: The read-only row synthesized from QWEN_* when no store file exists yet.
ENV_ROW_ID = "env"
LEGACY_KEY_ENV = "QWEN_API_KEY"

KINDS = ("ollama", "openai_compatible")
MODES_BY_KIND = {
    "ollama": ("ollama_format", "json_object"),
    "openai_compatible": ("json_schema", "structured_outputs", "guided_json", "json_object"),
}

HINT_CHARS = 4
#: Below this length a hint of four characters would be most of the key.
HINT_MIN_KEY_LEN = 8

_HEADER = """\
# HIVE LLM providers, written by the Executive's Models page (or by hand).
#
# THIS FILE CONTAINS API KEYS. It is mode 0600 in a 0700 directory, outside the
# repository; .gitignore also refuses providers.yaml anywhere in the tree.
# Nothing reads a key back out over HTTP: /api/exec/llm/providers answers with
# key_present and a four-character hint.
#
# A provider is a row: kind (ollama | openai_compatible), base_url, model,
# structured_mode, sampling, and optionally api_key. A role names an ordered
# chain: provider_id first, then fallbacks, used only on transport errors.
"""

_ENV_UNSAFE = re.compile(r"[^A-Z0-9]+")
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class ProviderStoreError(ValueError):
    """The store file cannot be read. The message never quotes file content."""


# -- locating the file ---------------------------------------------------------------
def store_path(path: Optional[Union[str, Path]] = None) -> Path:
    if path:
        return Path(path).expanduser()
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home().joinpath(*DEFAULT_RELATIVE_PATH)


def fingerprint(path: Optional[Union[str, Path]] = None) -> Optional[tuple]:
    """``(mtime_ns, size)`` of the store, or None when it does not exist.

    The router compares this before every mission, so a save from the browser
    (or an edit by hand) takes effect on the next mission without a restart.
    """
    try:
        info = store_path(path).stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


# -- naming the environment variable a key is handed over in -------------------------
def env_var_for(provider_id: str, explicit: str = "") -> str:
    """The row's own ``api_key_env``, else the preset's documented name, else HIVE_KEY_<ID>."""
    if explicit:
        return explicit
    known = preset(provider_id).get("key_env", "")
    if known:
        return known
    slug = _ENV_UNSAFE.sub("_", provider_id.upper()).strip("_")
    return f"HIVE_KEY_{slug}" if slug else "HIVE_KEY"


def key_hint(key: str) -> str:
    """``"…4f2a"``: the last four characters, "…" for a short key, "" for none."""
    if not key:
        return ""
    if len(key) < HINT_MIN_KEY_LEN:
        return "…"
    return f"…{key[-HINT_CHARS:]}"


# -- rows -----------------------------------------------------------------------------
class Sampling(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float = Field(0.2, ge=0.0, le=2.0)  # WS2: low temperature for strict-JSON planning
    top_p: float = Field(0.8, gt=0.0, le=1.0)
    top_k: Optional[int] = Field(20, ge=1)
    seed: Optional[int] = 1234
    max_tokens: int = Field(3072, ge=16, le=131072)
    timeout_s: float = Field(60.0, gt=0.0, le=600.0)
    #: Thinking stays off: the planner needs the JSON, not a reasoning trace.
    think: Literal[False] = False


class ProviderRow(BaseModel):
    """One provider. Deliberately has NO key field; see the module docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_ID_PATTERN)
    label: str = Field("", max_length=80)
    kind: Literal["ollama", "openai_compatible"]
    base_url: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=200)
    structured_mode: Literal["ollama_format", "json_schema", "structured_outputs", "guided_json", "json_object"]
    sampling: Sampling = Field(default_factory=Sampling)
    #: Variable the key is exported as; "" means derive it with env_var_for.
    api_key_env: str = Field("", pattern=r"^([A-Za-z_][A-Za-z0-9_]{0,63})?$")
    source: Literal["store", "env"] = "store"

    @field_validator("api_key_env")
    @classmethod
    def _key_variables_only(cls, value: str) -> str:
        # export_keys writes the key INTO this variable: PATH or HIVE_PROVIDERS_FILE here would clobber the process.
        if value and "KEY" not in value.upper() and "TOKEN" not in value.upper():
            raise ValueError("api_key_env must name a key variable (containing KEY or TOKEN), e.g. GROQ_API_KEY")
        return value

    @field_validator("base_url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _mode_fits_kind(self) -> "ProviderRow":
        if self.structured_mode not in MODES_BY_KIND[self.kind]:
            raise ValueError(f"structured_mode {self.structured_mode} is not available for kind {self.kind}; "
                             f"use one of {list(MODES_BY_KIND[self.kind])}")
        return self

    @property
    def key_env(self) -> str:
        return env_var_for(self.id, self.api_key_env)

    @property
    def read_only(self) -> bool:
        return self.source == "env"


# -- the store ------------------------------------------------------------------------
class ProviderStore:
    """Rows, role chains and, held apart from every model, the keys.

    ``_keys`` is the only place a key lives in this process besides the
    environment variable :func:`export_keys` writes.
    """

    def __init__(self, rows: Optional[dict] = None, roles: Optional[dict] = None,
                 keys: Optional[dict] = None, *, extra_roles: Optional[dict] = None,
                 invalid_rows: Optional[dict] = None, problems: Optional[list] = None,
                 loaded_version: Optional[int] = None, original_text: Optional[str] = None,
                 path: Optional[Union[str, Path]] = None) -> None:
        self._rows: dict[str, ProviderRow] = dict(rows or {})
        self._roles: dict[str, list[str]] = {role: [] for role in ROLES}
        for role, chain in (roles or {}).items():
            if role in ROLES:
                self._roles[role] = list(chain)
        self._keys: dict[str, str] = {k: v for k, v in (keys or {}).items() if v}
        self._extra_roles: dict[str, Any] = dict(extra_roles or {})
        self._invalid_rows: dict[str, Any] = dict(invalid_rows or {})
        self.problems: list[str] = list(problems or [])
        self._loaded_version = loaded_version
        self._original_text = original_text
        self._path = path

    def __repr__(self) -> str:
        return f"ProviderStore(rows={sorted(self._rows)}, roles={self.roles()})"

    # -- reads ------------------------------------------------------------------------
    def ids(self) -> list:
        return sorted(self._rows)

    def row(self, provider_id: str) -> Optional[ProviderRow]:
        return self._rows.get(provider_id)

    def key_env(self, provider_id: str) -> str:
        row = self._rows.get(provider_id)
        return row.key_env if row else env_var_for(provider_id)

    def api_key(self, provider_id: str) -> Optional[str]:
        """The key that would be used: stored, else whatever the shell exported."""
        return self._effective_key(provider_id) or None

    def _effective_key(self, provider_id: str) -> str:
        return self._keys.get(provider_id) or os.environ.get(self.key_env(provider_id), "")

    def stored_keys(self) -> dict:
        """``{env var: key}`` for keys held in the store (not ones only the shell has)."""
        return {self.key_env(pid): key for pid, key in self._keys.items() if pid in self._rows}

    def roles(self) -> dict:
        return {role: list(chain) for role, chain in self._roles.items()}

    def chain(self, role: str) -> list:
        return [self._rows[pid] for pid in self._roles[_check_role(role)] if pid in self._rows]

    def public(self) -> list:
        """The GET body: metadata, key_present and a hint, never key material."""
        bound: dict[str, list] = {}
        for role, chain in self._roles.items():
            for pid in chain:
                bound.setdefault(pid, []).append(role)
        rows = []
        for pid in sorted(self._rows):
            row = self._rows[pid]
            key = self._effective_key(pid)
            rows.append({
                "id": row.id,
                "label": row.label or row.id,
                "kind": row.kind,
                "base_url": row.base_url,
                "model": row.model,
                "structured_mode": row.structured_mode,
                "sampling": row.sampling.model_dump(),
                "key_env": row.key_env,
                "key_present": bool(key),
                "key_hint": key_hint(key),
                "roles": sorted(bound.get(pid, [])),
                "source": row.source,
                "read_only": row.read_only,
            })
        return rows

    # -- writes -----------------------------------------------------------------------
    def upsert(self, row: ProviderRow, api_key: Optional[str] = None) -> ProviderRow:
        """Create or replace one row. A None or empty ``api_key`` keeps the stored key.

        Empty means omission, not erasure: the form shows a saved key as its hint
        and posts an empty field when the operator did not retype it. Removing a
        key is :meth:`delete`, which is explicit.
        """
        if row.id == ENV_ROW_ID or row.source != "store":
            raise ValueError(f"provider id {ENV_ROW_ID!r} is reserved for the read-only QWEN_* env row")
        self._rows[row.id] = row
        self._invalid_rows.pop(row.id, None)
        if api_key:
            self._keys[row.id] = api_key
        return row

    def delete(self, provider_id: str) -> bool:
        """Forget a provider, its key, and every role position that named it."""
        if provider_id == ENV_ROW_ID and provider_id in self._rows:
            raise ValueError("the QWEN_* env row is read-only; unset QWEN_BASE_URL or save a store instead")
        found = self._rows.pop(provider_id, None) is not None
        found = (self._invalid_rows.pop(provider_id, None) is not None) or found
        if not found:
            return False
        self._keys.pop(provider_id, None)
        for role in self._roles:
            self._roles[role] = [pid for pid in self._roles[role] if pid != provider_id]
        self._extra_roles = {role: binding for role, binding in self._extra_roles.items()
                             if not (isinstance(binding, dict) and binding.get("provider_id") == provider_id)}
        return True

    def set_role(self, role: str, provider_ids: Iterable[str]) -> list:
        _check_role(role)
        chain: list[str] = []
        for pid in provider_ids:
            pid = str(pid).strip()
            if pid not in self._rows:
                raise KeyError(f"unknown provider {pid!r}")
            if pid not in chain:
                chain.append(pid)
        self._roles[role] = chain
        return list(chain)

    # -- persistence ------------------------------------------------------------------
    def to_yaml_data(self) -> dict:
        providers: dict[str, Any] = dict(self._invalid_rows)
        for pid, row in self._rows.items():
            if row.read_only:
                continue
            data: dict[str, Any] = {
                "label": row.label,
                "kind": row.kind,
                "base_url": row.base_url,
                "model": row.model,
                "structured_mode": row.structured_mode,
                "sampling": row.sampling.model_dump(),
            }
            if row.api_key_env:
                data["api_key_env"] = row.api_key_env
            if self._keys.get(pid):
                data["api_key"] = self._keys[pid]
            providers[pid] = data
        roles: dict[str, Any] = dict(self._extra_roles)
        for role, chain in self._roles.items():
            chain = [pid for pid in chain if pid in self._rows and not self._rows[pid].read_only]
            if chain:
                roles[role] = {"provider_id": chain[0], "fallbacks": chain[1:]}
            else:
                roles.pop(role, None)
        return {"version": SCHEMA_VERSION, "providers": providers, "roles": roles}

    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        target = store_path(path or self._path)
        if self._original_text is not None and (self._loaded_version or 1) < SCHEMA_VERSION:
            backup = target.with_name(target.name + f".v{self._loaded_version or 1}.bak")
            if not backup.exists():
                _write_private(backup, self._original_text)
        body = yaml.safe_dump(self.to_yaml_data(), sort_keys=True, default_flow_style=False, allow_unicode=True)
        _write_private(target, _HEADER + body)
        self._loaded_version = SCHEMA_VERSION
        self._original_text = None
        return target


# -- handing keys to the client ---------------------------------------------------------
def export_keys(store: ProviderStore, previous: Optional[dict] = None) -> dict:
    """Publish stored keys into their env vars; undo what an earlier call published.

    ``previous`` is what the last call returned: ``{var: value before HIVE wrote it}``.
    A var HIVE no longer has a key for goes back to the shell's value, or away.
    Keys only the shell exported are never touched.
    """
    previous = dict(previous or {})
    wanted = store.stored_keys()
    exported: dict[str, Optional[str]] = {}
    for var, key in wanted.items():
        exported[var] = previous[var] if var in previous else os.environ.get(var)
        os.environ[var] = key
    for var, original in previous.items():
        if var in wanted:
            continue
        if original is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = original
    return exported


# -- loading ------------------------------------------------------------------------------
def load_store(path: Optional[Union[str, Path]] = None, legacy: Any = None) -> ProviderStore:
    """Read the store. A missing file is an empty store (plus the env row, see below).

    ``legacy`` is the Executive's QwenSettings (built from QWEN_* env). When no
    store file exists and it has a base_url, a read-only ``env`` row is bound to
    the executive role, so a machine configured the old way keeps planning.
    """
    target = store_path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _env_only_store(legacy, target)
    except OSError as exc:
        raise ProviderStoreError(f"cannot read the provider store {target}: {exc.strerror}") from exc
    _enforce_mode(target)
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise ProviderStoreError(f"{target} is not valid YAML{where}") from None
    if not isinstance(data, dict):
        raise ProviderStoreError(f"{target} must hold a mapping at the top level")

    problems: list[str] = []
    rows: dict[str, ProviderRow] = {}
    keys: dict[str, str] = {}
    invalid: dict[str, Any] = {}
    for pid, raw_row in _mapping(data.get("providers")).items():
        pid = str(pid)
        entry = _mapping(raw_row)
        key = entry.get("api_key") or entry.get("key")
        try:
            rows[pid] = _row_from_file(pid, entry)
        except ValidationError as exc:
            invalid[pid] = raw_row
            problems.extend(f"providers.{pid}: {_describe(err)}" for err in exc.errors(include_url=False))
            continue
        if key:
            keys[pid] = str(key)

    roles: dict[str, list[str]] = {}
    extra_roles: dict[str, Any] = {}
    for role, binding in _mapping(data.get("roles")).items():
        role = str(role)
        if role not in ROLES:
            extra_roles[role] = binding
            continue
        chain = _chain_from_file(binding)
        if isinstance(binding, dict) and str(binding.get("model") or ""):
            problems.append(f"roles.{role}: a per-role model override is not supported; the row's model is used")
        kept = []
        for pid in chain:
            if pid in rows and pid not in kept:
                kept.append(pid)
            elif pid not in rows:
                problems.append(f"roles.{role}: provider {pid!r} is not a valid row and was skipped")
        roles[role] = kept

    try:
        version = int(data.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    return ProviderStore(rows, roles, keys, extra_roles=extra_roles, invalid_rows=invalid,
                         problems=problems, loaded_version=version, original_text=raw, path=path)


def _env_only_store(legacy: Any, target: Path) -> ProviderStore:
    base_url = str(getattr(legacy, "base_url", "") or "").strip()
    if not base_url:
        return ProviderStore(path=target)
    is_ollama = str(getattr(legacy, "provider", "")).strip().lower() == "ollama"
    mode = str(getattr(legacy, "structured_mode", "") or "structured_outputs")
    if is_ollama:
        mode = "ollama_format"
    elif mode not in MODES_BY_KIND["openai_compatible"]:
        mode = "structured_outputs"
    try:
        row = ProviderRow(
            id=ENV_ROW_ID, label="QWEN_* environment (read-only)",
            kind="ollama" if is_ollama else "openai_compatible", base_url=base_url,
            model=str(getattr(legacy, "model", "") or ""), structured_mode=mode,
            sampling=Sampling(temperature=legacy.temperature, top_p=legacy.top_p, top_k=legacy.top_k,
                              seed=legacy.seed, max_tokens=legacy.max_tokens, timeout_s=legacy.timeout_s),
            api_key_env=LEGACY_KEY_ENV, source="env")
    except (ValidationError, AttributeError, TypeError) as exc:
        detail = ("; ".join(_describe(e) for e in exc.errors(include_url=False))
                  if isinstance(exc, ValidationError) else type(exc).__name__)
        return ProviderStore(problems=[f"QWEN_* env row ignored: {detail}"], path=target)
    keys = {ENV_ROW_ID: str(getattr(legacy, "api_key", "") or "")}
    return ProviderStore({ENV_ROW_ID: row}, {"executive": [ENV_ROW_ID]}, keys, path=target)


def _row_from_file(pid: str, entry: dict) -> ProviderRow:
    """Lenient on what may be missing (a summer v1 row is just base_url/model/key)."""
    known = preset(pid)
    kind = str(entry.get("kind") or "openai_compatible")
    fields: dict[str, Any] = {
        "id": pid,
        "label": str(entry.get("label") or known.get("label", "")),
        "kind": kind,
        "base_url": str(entry.get("base_url") or known.get("base_url", "")),
        "model": str(entry.get("model") or known.get("model", "")),
        "structured_mode": str(entry.get("structured_mode")
                               or known.get("structured_mode")
                               or ("ollama_format" if kind == "ollama" else "json_schema")),
        "api_key_env": str(entry.get("api_key_env") or ""),
    }
    sampling = entry.get("sampling")
    if isinstance(sampling, dict):
        fields["sampling"] = _migrated_sampling(pid, sampling)
    elif kind != "ollama":
        fields["sampling"] = Sampling(top_k=None)
    return ProviderRow(**fields)


def _migrated_sampling(pid: str, sampling: dict) -> dict:
    """A row saved with the pre-WS2 default sampling plans at today's default temperature."""
    picked = {k: sampling.get(k, Sampling.model_fields[k].default) for k in PRE_WS2_SAMPLING[0]}
    if picked not in PRE_WS2_SAMPLING:
        return sampling
    temperature = Sampling.model_fields["temperature"].default
    if pid not in _MIGRATION_LOGGED:
        _MIGRATION_LOGGED.add(pid)
        log.info("provider row %s has the pre-WS2 default sampling (temperature 0.7); planning at %s",
                 pid, temperature)
    return {**sampling, "temperature": temperature}


def _chain_from_file(binding: Any) -> list:
    if isinstance(binding, str):
        return [binding] if binding else []
    if isinstance(binding, (list, tuple)):
        return [str(pid) for pid in binding if pid]
    if isinstance(binding, dict):
        first = str(binding.get("provider_id") or "")
        rest = binding.get("fallbacks") or []
        return ([first] if first else []) + [str(pid) for pid in rest if pid]
    return []


def _describe(error: dict) -> str:
    """'loc: msg' only: pydantic's 'input' could be a key, so it is never included."""
    loc = ".".join(str(part) for part in error.get("loc", ())) or "row"
    return f"{loc}: {error.get('msg', 'invalid')}"


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise KeyError(f"unknown role {role!r}; known roles are {list(ROLES)}")
    return role


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _enforce_mode(path: Path) -> None:
    with suppress(OSError):
        if (path.stat().st_mode & 0o777) != FILE_MODE:
            os.chmod(path, FILE_MODE)


def _write_private(path: Path, text: str) -> None:
    """Atomic write at 0600 (mkstemp in the same directory, then os.replace).

    The parent is made 0700 when this call creates it, and the default ~/.hive
    is kept at 0700; a directory the operator chose with HIVE_PROVIDERS_FILE is
    otherwise left alone.
    """
    created_parent = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    if created_parent or path.parent == Path.home() / DEFAULT_RELATIVE_PATH[0]:
        with suppress(OSError):
            os.chmod(path.parent, DIR_MODE)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".providers-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            with suppress(OSError):
                os.chmod(temp, FILE_MODE)
            stream.write(text)
        os.replace(temp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp)
        raise
