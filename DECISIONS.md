# Decisions

Design choices that are not obvious from the code, with the reason and the
alternative that was turned down. Newest section last.

## LLM provider store and router (Executive build, prompt 4)

**Store format: YAML at `~/.hive/providers.yaml`.** The summer build
(Bhavya007-17/hive@4fa0156) already writes this file in YAML, so an existing
summer file loads unchanged, and the operator can edit it by hand with
comments. JSON was turned down: it has no comments and would need a migration.
The file is mode 0600 in a 0700 directory, outside the repository, and
`providers.yaml` is gitignored anywhere in the tree. `HIVE_PROVIDERS_FILE`
overrides the path (the test suite always sets it).

**Summer v1 files.** Read as they are. The first save writes schema version 2
and keeps the original once as `providers.yaml.v1.bak` (also 0600).

**Keys are write-only.** `ProviderRow` has no key field, so no `model_dump()`
can carry one. Keys live in one private dict on the store, are written only to
the YAML file, and reach the HTTP client through the environment variable the
row names. `api_key_env` must contain `KEY` or `TOKEN`, because the store
writes the key into that variable and a name like `PATH` or
`HIVE_PROVIDERS_FILE` would overwrite it. Every API read returns `key_present`
and a four-character hint. Under `/api/exec/llm`, validation errors are
answered with loc/msg/type only, because FastAPI's default 422 body echoes the
request, key included. Probe and router error text replaces the key with its
hint.

**QWEN_* environment row.** While no store file exists, a read-only `env` row
is built from `QWEN_*` and bound to the executive role, so a machine set up the
exec_v2 way keeps planning. The first save creates the store, and from then on
`QWEN_*` is not read; `GET /api/exec/llm/providers` says so in `store.note`.
Turned down: copying the env row into the file on first save. That would write
into the file a key the operator had put only in the environment, and it would
add a row the operator never created.

**Failover only on transport errors.** A connect failure, a timeout, a 5xx or a
429 moves to the next row in the role's chain. A 4xx is the provider telling
the operator something (bad key, unknown model), so it stops there. Output
that fails the schema or does not parse is not a failover: it goes to the
Executive's single repair attempt, the same as in exec_v2.

**Saves apply to the next mission.** The router compares the store's
`(mtime_ns, size)` fingerprint before every resolve, and the API also
invalidates the router after each save. A store file that does not parse is
reported on GET and answered 409 on every write; it is never overwritten.

**Probe.** The probe lists models (for information only), sends a ping, and
then does a `plan_proposal.v1` round trip. It allows 10 s per request, makes
no retries, is not cached and is not logged to a mission. The ping uses the
row's own temperature, so a model that would fail the planning call fails
here too. The round trip keeps exec_v2's probe settings (temperature 0), but
with 1024 max tokens instead of exec_v2's 600, because hosted reasoning models
(gpt-oss) spend completion tokens before they emit the JSON. An unreachable
provider ends the probe at the first request, so the operator waits for at
most one timeout. The startup probe runs in the background, so an unreachable
provider does not hold up startup. The planner badge shows "live" only when
the last probe was of the row now bound to the role: same id, model, base_url
and kind.

**Planner kind.** `"llm"` replaces `"qwen"`, which is still accepted as an
alias in `MissionIn` and in saved state. The baseline is labeled "Baseline (no
LLM)".
