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

## Executive build, prompts 2–10 (one line each)

- **P2 merge, not rebase:** exec_v2 was merged into live-data; on conflict live-data's `server.py` and `App.jsx` won, because exec_v2's only server change (the `/gripper_open` topic) was already there.
- **P2 two interpreters:** the car-backend tests run on system `python3` (has cv2) and the Executive tests on the hive-exec venv (has FastAPI and pytest-asyncio); one interpreter cannot collect both.
- **P2 schema test:** the Qwen request test compares against `planner.schema` (with `scope` required), the schema actually sent, not the unpatched one.
- **P3 what came from exec:** `bridge/`, `ros/`, `backend/scripts/` and docs only; exec's `hive_exec/`, `App.jsx`, `hive.mjs` and `package.json` were left, because exec_v2's are newer.
- **P3 domain 77 always:** the fake fleet ignores an inherited `ROS_DOMAIN_ID`, because a `~/.bashrc` exporting 42 put the fake action servers on the camera car's domain.
- **P3 refuse Origin, no token:** both WebSocket servers answer 403 to any `Origin` header, because browsers always send one and the gateway never does; a token would add nothing, since any local process can reach domain 77 over DDS anyway.
- **P3 rosbridge_local:** rosbridge 2.7.1 has no origin setting, so the launch file runs the stock script with `check_origin` replaced instead of patching the installed package.
- **P3 robot_id allowlist:** the bridge accepts only the three fake robot ids, because the id becomes part of the ROS action name.
- **P4 provider store, router and probe:** see the section above.
- **P5 per-task stop through recovery:** Stop current reuses the task-failure recovery path (`operator_stop`) instead of a new state, so the decision semantics stay exec_v2's.
- **P5 agent_commands byte for byte:** the recorded `envelope_json` reproduces the bridge's `JSON.stringify` output, so the file is exactly what an instance agent receives.
- **P5 health gates dispatch:** mission start and approve answer 503 naming the dependency that is down, rather than dispatching into a dead gateway.
- **P5 map stub:** `/api/exec/map` answers `available: false` with the schema name instead of 404, so the Map tab can show its empty state from the same call the real map will answer.
- **P6 Cyclone DDS for the fake fleet:** under Fast DDS (the default in a non-interactive shell) rosbridge could not reach `/hive/fleet_control`; domain 77 is separate, so the car backend's Fast DDS pin is untouched.
- **P6 shutdown order:** one Ctrl+C stops the car backend first and completely, because it is the process that stops real cars.
- **P6 busy port refuses:** the launcher names the leftover process and its `pkill` rather than killing it; the operator decides.
- **P6 synapse by default:** `npm run hive` defaults to `HIVE_GATEWAY=synapse`, the path real dispatch will take; the Executive alone (and `.env.example`) still defaults to `fake`.
- **P7 SIM tag:** any state from the fake world or fake fleet is tagged SIM; real connection data comes only from the car backend.
- **P7 map from a contract:** the Map tab renders `hive.shared-map.v0` (written down first in `docs/shared-map-contract.md`), and its fixture is watermarked DEMO.
- **P7 STOP ALL:** stops every connected car through the car backend and cancels the Executive mission, so one button covers both paths.
- **P8 polling pauses when hidden:** Executive polling stops while the tab is hidden and resumes on `visibilitychange`.
- **P8 earlier missions in localStorage:** the list is per browser, not shared between machines.
- **P8 retire the old Executive view:** the old view, plan panel, world map and stylesheet were removed rather than kept beside the chat, and the command log moved under Fleet.
- **P9 key input uncontrolled:** the key field is not React state and is emptied at submit; tests grep the DOM, inputs, storage and console for the key.
- **P9 model pickers:** fed by `GET /api/exec/llm/providers/{id}/models` (no chat, not logged); without a model list they fall back to free text.
- **P9 refresh after save:** the Models page refreshes Executive state after every write so the planner chip shows the new binding at once.
- **P10 HIVE_ROS_SETUP through WSLENV:** a Windows path is converted to `/mnt/<drive>/...` so the launcher can run the car backend on the mock ROS.
- **P10 open on Mission:** sending missions is the main job, so the app (and a reload) opens on Mission, now the first tab; the view is still not in the URL.
- **P10 left as is:** Gemini's rejection of `seed` and the header pill showing the active revision while a later one awaits approval.
