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

## Open-vocabulary objects and a random sim world (WS1)

- **Labels are open:** an object label is any lowercase snake_case string (`^[a-z][a-z0-9_]{0,31}$`); the nine synthetic catalog profiles are the only objects with a trusted mass, so anything else can be found but not carried (`unknown_object_profile`). An enum of three labels was turned down because the planner could not even name what a camera saw.
- **One canonicalization point:** `ObjectQuery`'s before-validator in `contracts.py` calls `catalog.canonical_label` (lowercase, spaces/hyphens to `_`, alias lookup, e.g. `red box` to `red_block`). Labels robots report are taken as they come, so the robot side stays the source of truth.
- **Profile check before location:** the validator rejects a pick of an unprofiled label even while the object is still unlocated, so a plan cannot spend a search on something it could never carry.
- **Random world is seeded:** `FakeWorld(world="random", seed=N)` is reproducible; a reset without a seed draws one and records it in state and export (`sim: {world, seed}`) so any run can be replayed. The fixed world ignores the seed.
- **Defaults:** the Executive and the e2e harnesses default to `fixed` (`HIVE_SIM_WORLD` unset; b_e2e and smoke_eval construct the Executive without it), so evidence stays stable; `npm run hive` sets `HIVE_SIM_WORLD=random` unless the caller set it, because a person trying the app should meet a world the planner has not seen. The drawer defaults to Random and offers Fixed demo.
- **Old fleet, new Executive:** a fleet without world support answers reset without `world`; the Executive records `fixed` and logs that the fleet ignored the request, rather than failing the reset.
- **Truth stays debug-only:** `/api/exec/debug/truth` and the drawer's "Reveal world truth" show the simulator's hidden state, labeled simulation only; planning and the normal map never read it.
- **Random world mix (WS1 fix):** 1 to 6 objects, never empty; red_block is drawn on its own (in about half the worlds, two of them in about 7 %), because operators mostly try "find the red box"; the other slots mix profiled and profile-less labels (one or more profile-less in about 47 %). Measured over seeds 0..1999: red 52.8 %, two red 6.7 %, profile-less 46.8 %, corridor blocked 23.9 %. **A seed recorded before this change (up to dd8e11d) now replays a different world.**
- **Fixed world faults unchanged:** in the fixed world every fault and fault pair behaves as at 7db6669 (`two_red_blocks` sets `red_block_2`, so `block_missing` then `two_red_blocks` leaves only red_block_2); only random worlds top up to two free red blocks.

## Find, answer and ask (WS2)

- **Prompt v3:** `hive.prompt.v3` separates find (sensing only: `object_located`, scope full, no transport, no route scan) from fetch (`object_at`, staged), scans a route only when a later stage drives it, reads "the field" as `work_area`, keeps the operator's own noun, and never maps an unnamed destination ("base", "here", "me") to `dropoff_zone`: it asks. Examples are varied and each has its own summary; the rules tell the model to write its own. Prompt size (system + user + schema/4, estimate) went from 3193 to 4258 of the 9216-token budget.
- **Ask once, only when it matters:** clarify asks one short question with up to four `clarification_options` (quick replies in the chat; free text stays). Every round is kept on the mission (`clarifications`) and passed to the planner oldest first; a fourth question ends the mission blocked. No question when one reading is clear or a default exists.
- **answer disposition:** a fleet question gets `disposition: "answer"` (at most 600 characters, no goals, no tasks, scope null) and ends the mission `answered`, a terminal status; nothing is dispatched. An answer on a continuation or recovery blocks the mission like a reject. The Executive handles each disposition explicitly and fails closed on anything else; `_make_revision` refuses a proposal that is not a validated plan.
- **Find-only completion:** an `object_located` goal is also satisfied by a fresh absence report for that area made during the mission, so "find the duck" completes with "Searched work_area: no duck found." instead of blocking; a delivery whose object is absent still blocks.
- **Temperature 0.2:** planning samples at 0.2 (Qwen's top_p/top_k kept) in the templates, the `Sampling` default, `QwenSettings` (an explicit `QWEN_TEMPERATURE` still wins) and the Settings form default, so a small model emits the strict JSON plan close to deterministically. A saved row whose temperature/top_p/top_k/seed are exactly the pre-WS2 default (0.7/0.8/20 or None/1234) is read as untuned and plans at 0.2 (logged once per row); any other saved value is the operator's and is kept.
- **Find needs a fleet report from this mission:** `object_located`, found or absent, counts only from a fleet robot's report made at or after mission start; a prior survey (`operator_seed`) never completes a find.
- **A revision that cannot be built** ends the mission `planner_error` with the reason; nothing is dispatched and earlier revisions are superseded only once the new one exists.
- **prompt_eval splits recall from generalisation:** each case is tagged `in_prompt` (a prompt example shares its sentence frame) and unseen paraphrases get their own pass rate. "snap a photo of the drop-off area" expects reject or clarify (no robot observes `dropoff_zone`); the over-payload toy car expects reject, clarify, or a sensing-only plan with an issue; "is the corridor clear?" accepts an answer or a `route_observed` plan.

## Plan rationale and sensorless navigation (WS3)

- **The planner decides how a sensorless robot gets there, and says so:** the catalog now gives each robot `sensors` and a `localization` text (synthetic, labelled): camera_01 and lidar_01 are fixed posts that see/scan named places; gripper_01 has none, drives between named poses by dead reckoning in simulation, and cannot detect obstacles or confirm its position. There is no hard-coded escort rule; rule 14 of prompt `hive.prompt.v3.1` asks for the choice (lidar scan or camera look first, or the stated dead-reckoning assumption) in `rationale`.
- **`rationale` is optional in the contract:** `PlanProposal.rationale` is up to 4 strings of at most 200 characters, default empty, so old outputs and `ollama_format` schemas stay valid. It is part of the proposal, so it is in the proposal hash, the revision, state and export; an edit keeps the base revision's rationale (it can go stale after a removed task; the operator sees the new task list beside it).
- **Missing rationale is a note, not a block:** the validator adds an info finding `missing_rationale` (on the first moving task) when a plan makes a sensorless robot change location and the rationale is empty or blank. It never stops a plan.
- **Baseline fills one deterministically:** only-camera and only-lidar reasons for sensing, "waits for these results" for a staged fetch, and a dead-reckoning line for the carry.
- **Prompt growth:** system prompt 10917 to 11513 characters (+596, under the 600 limit; token estimate for a first command 4256 to 4451). To fit, the catalog no longer lists `observes areas`/`scans routes` apart from the localization text (which names the same places), and the `(synthetic)` label is dropped from the prompt copy of localization.
- **Plan card:** "Why this plan" sits above the task table: one item is a visible first line, several fold behind an `aria-expanded` button (collapsed by default); text only. The object picker also marks profiled labels heavier than the strongest gripper payload as "(too heavy - cannot carry)" (equal to the payload is allowed; no known payload means no marker).
- **Planning checks that raise end the mission:** an exception from allocate or validate on the initial or clarification-answer path (e.g. a proposal built without validation) ends the mission `planner_error` with the reason logged, instead of leaving it `planning` or failing the request.
- **Fake-timer sim drawer test:** the truth-refetch test now runs on vitest fake timers (no 1.6 s real wait; the 950 ms gap assertion is unchanged and fails if the throttle is removed).

## Next-stage preview (WS4)

- **What the operator sees:** an observation stage now also shows what the next stage would likely be ("Likely next stage - not approved, may change": robot hint plus described action per task, and what it waits for), and the continuation revision says "Matches the preview" or "Changed from preview:" with the backend's diff. Each stage still needs its own approval; nothing about approval changed.
- **Contract:** `PlanProposal.next_stage_preview` is optional `{summary, tasks (<= 16 TaskSpec), waits_for (<= 4 strings of <= 160)}`, allowed only for a plan with `scope=observation_stage` (a schema error otherwise). Preview tasks have their own ids and dependencies (checked: unique ids, known dependencies) and may name objects by `object_query`. A preview with no tasks is dropped (set to null) rather than failing the plan. `preview_changes` is optional, at most 4 strings of at most 160, allowed on any plan (a continuation that goes ahead as previewed has scope full). `plan_proposal.v1.json` was regenerated; the nested optional object is an `anyOf [$ref, null]` like `scope`, so `ollama_format` / `structured_outputs` stay valid and the model may omit it.
- **Never executable:** the allocator and scheduler only ever see `proposal.tasks`, so the preview is not allocated, dispatched or approved (a test asserts only camera_01 and lidar_01 get attempts and only rev-1 task runtimes exist). The proposal hash leaves out `next_stage_preview` and `preview_changes`, so approval binds exactly what can run and the display-only text can never be mistaken for approved.
- **Preview notes never block:** `validate_preview` runs the preview through the normal checks on a shadow proposal, keeps every finding at warning or info (errors are downgraded, the code is kept) and stores them in `validation.preview_findings`, separate from `validation.findings`, so `ok` and approval are untouched. Findings that only say stage-1 evidence has not arrived (object not located, ambiguous, route not observed clear, pick location mismatch, goal not projected, uncovered stage goal, ...) are dropped, since waiting for it is the point; real problems (no robot can do it, unprofiled object cannot be carried, over payload, route observed blocked, robot unavailable) stay as notes. A check that raises becomes one info note (`preview_unchecked`) and never breaks planning.
- **Which preview a continuation answers:** the latest finished observation-stage revision, and only its own preview (a later observation stage without a preview hides an older one). The continuation planning context carries it as `previous_preview {from_revision, summary, tasks, waits_for}`; the key is left out of the context JSON when there is none, so other phases' prompts are byte-for-byte as before. A re-plan of a continuation and an edit of a continuation use the same preview; a recovery revision does not get a diff.
- **The diff is the backend's:** `preview_diff {previous_revision, matches_preview, kept, changed, added, removed}` is computed in `_make_revision` from the previewed tasks and the validated tasks, never from the model's `preview_changes` (which is kept as the planner's own explanation; a test has the model claim "matches" while the diff says every task changed). Tasks are paired by what they do, not by id: pass 1 identical (binding, kind, location, route, area, object label, and object id when both name one), pass 2 same binding and kind, pass 3 same kind under another binding; the rest is added or removed. A binding renamed between preview and plan therefore shows as changed, as the ticket's key list asks. Stored on the revision, so it is in state and in the run export (and in the browser's earlier-missions copy, which keeps the whole export).
- **Baseline:** a staged fetch emits the carry chain (navigate, pick, navigate, place; the located object's id when known, `object_query` otherwise) with `waits_for` naming the camera sighting and/or the lidar scan still missing; on a continuation it fills `preview_changes` from the same diff function (`The evidence matches the preview.`, or up to four short change lines).
- **Prompt hive.prompt.v3.2:** rules 15 and 16 ask for the preview and for `preview_changes`; the fetch example carries a preview; the continuation example shows `preview_changes`. System prompt 11513 to 12572 characters (+1059; the budget was under 900 for the preview plus 200 for the carry-overs, 1100 in all, reached partly by compacting the catalog lines, dropping empty `depends_on` in sensing examples, and one robot preference per binding in the carry example). Token estimate (system + user + schema/4) for a continuation: 4580 (v3.1, which could not carry a preview) to 5251 with a four-task preview in the context (system 3598 to 3929, user 379 to 634, schema/4 603 to 688; the schema grew 7726 to 8809 characters). The hard limit is 9216; the two 50 % guard tests were raised to 60 %, because the ticket's own growth cannot fit under 50 % (v3.1 was already at 4451 of 4608).
- **Carry-overs from the WS3 verifier:** the two plan examples without a rationale got one each, worded differently from the others; rule 11 now covers the rationale too ("Write your own summary and rationale ..."); the prompt's observed places are generated from `observable_areas` / `observable_routes` ("observes areas ...", "observes routes ...", one verb) and the catalog localization strings no longer repeat them (`fixed at camera_post (synthetic)`), with a test that the prompt's places equal the catalog for every robot and follow a changed catalog; the WS3 `planner_error` wrap now has tests for the continuation and recovery phases, and all four wrap tests assert that `replan()` is allowed afterwards and reaches approval; the run of spaces in validator.py is gone.
- **Left as is:** an edit that removes stage-1 tasks keeps the preview text unchanged (it can go stale, like the rationale; the operator sees the new task list above it); the preview's robot hint on the card comes from the catalog (skill and reach), not from an allocation; the card was checked in jsdom for structure and wrapping classes, not in a 390 px browser.

## Operator messages mid-mission (WS5)

- **One composer, two modes:** while a mission is active the chat box sends operator messages ("Send now", the primary button and Enter; "Queue", a secondary button). Queued messages sit above the box as chips ("Queued - delivered at next planning step") with Send now and a remove button. With no active mission the composer is the old "Describe a mission" box; the planner chip is hidden in message mode because a mission's planner is fixed.
- **Reject stays terminal:** the card says "Reject ends this mission. To change the plan, send a message instead." Rejecting a live re-plan (one proposed while an older revision runs) only drops that revision and says so.
- **State machine:** clarification: the message is the answer. awaiting_approval + now: pending revision `superseded` (`operator_message`), then a `feedback` re-plan of the same stage. planning + now: `held`, the proposal that lands is dropped unseen, then one feedback re-plan with every held message. executing + now: live re-plan; approve marks the old revision's never-dispatched tasks `superseded` and runs the stage end once; reject or ignore keeps the old revision running. Terminal or canceling mission: 409. STOP ALL, cancel and terminal outcomes cancel queued and held messages with a note.
- **A stage end beats a pending live re-plan:** if the running stage ends before the live re-plan is approved, that revision is superseded (`stage_ended`) and nothing unapproved runs; the delivered feedback is still in the continuation's context. The thread says why, and "triggered by" skips such a revision.
- **Untrusted text, bounded:** operator text reaches the model only inside the delimited `OPERATOR FEEDBACK` block (prompt hive.prompt.v3.3), newest 4 messages and 1200 characters, with an omitted count; `previous_proposal` is added for a feedback re-plan. Heavy feedback context estimate 6416 tokens (69.6% of 9216), repair 7871 (85.4%).
- **Baseline:** feedback is applied by keyword rules (find-only, camera-only, negated phrases dropped); it cannot change the approved goals of a later stage and says so in issues.
- **Frontend details:** DELETE goes through a local `execDelete` in ChatView (api.js has no DELETE helper); a message used as a clarification answer is not repeated as a bubble (it is in the clarification round); queued messages are also delivered at a feedback planning point.
- **num_ctx clamp (carry-over):** Ollama num_ctx is min(input limit + max_tokens, 12288) and num_predict at most 3072.

## WS5 verifier fixes
- **Stop names its revision:** `/tasks/{id}/cancel` takes an optional `revision` (default the active one) because task ids repeat across revisions; a confirmed operator stop of a task an older revision left running is logged and the stage-end check runs, with no recovery (the operator asked for it, it is not a fault).
- **Old-revision failures:** after a live supersede, an old task that ends failed/rejected/uncertain (or canceled unrequested) takes `_on_task_failure` like an active one; the sibling cancel now covers every live attempt, since all live attempts belong to the running stage; the recovery budget is shared so two such failures recover once.
- **Feedback cap on the rendered form:** `render_feedback` (json.dumps, ASCII) is the one encoder for prompt and cap; older messages stay whole-or-nothing, the newest is cut by code point (binary search) with ` [truncated]`, so no escape or surrogate pair is split. Prompt text unchanged, PROMPT_VERSION stays v3.3.
- **Delivered means used** (replaced by the message invariant in WS5 fix batch 3): a landed non-live planning records which delivered messages it read; one tagged to a `stage_ended` revision is re-tagged to the revision that used it; at a terminal outcome a delivered message nothing read (live re-plan in flight or dropped) is canceled with a resend note. A live reply without a plan (answer) counts as used.
- **Notes:** the held-to-queued fallback note is dropped when the message is later delivered, so a note never describes a past state.

## WS5 fix batch 2
- **First failure wins** (fix batch 3: a real fault replaces an operator stop): `_on_task_failure` while the stage is already `recovering` only adds a history line (an uncertain result still sets the blocked outcome) and still cancels any attempt left running (e.g. after an operator stop); the recovery `failure` is the stage's first failure, kept in `_stage_failed`, even when that was an operator stop.
- **Executive-requested cancels are marked:** `Attempt.stopped_after` is set where siblings are canceled; their `canceled` is a history line `canceled (stopped after <task> failed)` and never re-enters `_on_task_failure`. A task the operator is already stopping is not marked, so it keeps its operator_stop meaning; an unmarked cancel nobody asked for is still a failure.
- **Token weight 2 per non-ASCII code point** (replaced in fix batch 3 by one token per UTF-8 byte): Qwen's byte-level BPE gives CJK about 1 token and common emoji 1-2; 2 is the conservative end without a tokenizer dependency, and ASCII keeps len/3.2 so no ASCII estimate drops. Rare multi-codepoint or 4-byte symbols can exceed 2; the 9216 guard keeps headroom below the real window.
- **Unescaped but line-safe JSON:** `safe_dumps` = `json.dumps(ensure_ascii=False)` plus `\uXXXX` for U+0085, U+2028, U+2029 and lone surrogates (json already escapes the other splitlines breakers, all below U+0020); used for the whole context and feedback lines, so an emoji costs 1 character instead of a 12-character escape pair. PROMPT_VERSION v3.4.
- **Cap in ASCII-equivalent width:** `text_width` counts a non-ASCII code point as 6.4 characters (2 tokens x 3.2; fix batch 3: 3.2 per UTF-8 byte), so FEEDBACK_MAX_CHARS keeps meaning about 375 tokens for any script.
- **Repair addendum fitted:** `fit_user_prompt` cuts the previous output (still at most 2000 chars) to the tokens left, or drops it; the guard in llm.py still checks the final total.
- **Dropped revision = superseded and never approved** (wrong; replaced in fix batch 3): this heuristic tagged a message to the revision a planning produced and then moved or dropped the tag, so two live messages whose revision 3 was approved still ended canceled. The message invariant in fix batch 3 replaces it.
- **Lone surrogates are a 422:** `Body` refuses any string holding U+D800-U+DFFF before anything is stored; the validation handler delegates to the existing one (llm_api's or FastAPI's) and only on UnicodeEncodeError answers the same 422 ASCII-escaped.

## WS5 fix batch 3
- **Message invariant, not heuristics:** I1: a delivered message is applied to revision N only when N was approved and N's planning context included it; `delivered_to_revision` is the first such N, set at approval (`Mission.message_context` maps each revision to the message ids its context had; an edit keeps its base revision's set). Before any approval it stays delivered with no revision (UI "delivered - awaiting approval"). I2: a planning reply without a plan (answer, clarify, reject) consumes the messages its context had: delivered, no revision, a note naming the reply ("The planner answered." / "The planner asked a question." / "The planner rejected the request."; a live reply keeps its "did not re-plan" note). A message carried by a revision still awaiting approval is neither consumed nor re-noted. I3: at any terminal state a delivered feedback message neither applied nor consumed is canceled with "Mission ended before your message was applied; send it again as a new mission." `_dropped`, `_mark_read`, `_feedback_read` and the tag-at-revision-creation are gone; the `superseded_by` / `stage_ended` supersede logic stays (safety and display). I2 applies to every plan-less reply whose context had the message, not only the feedback phase, so a continuation that answers still counts as a reply.
- **Old-revision results while recovering:** a failed / rejected / uncertain / unrequested-cancel result of a task a replaced revision left running goes through `_on_task_failure` while recovering (history line; uncertain blocks). If it lands while recovery (or the next stage) is being planned or awaits approval, it adds the history line, and an uncertain one discards the planning in flight and blocks, which supersedes the pending revision (`stage_ended`), so approving it is a 409.
- **A real fault outranks an operator stop:** when the stage's first failure was an operator stop, a later failed / rejected / uncertain / unrequested cancel in the same stage becomes the recovery failure; the stop stays a history line.
- **Context overflow is not retryable:** `ContextBudgetExceeded` ends planning as planner_error with "This request is too long for the planner. Start a new mission with a shorter command or fewer messages." and `outcome.retryable: false` (the estimate stays in the planning log); replan answers 409 and the UI hides Try again / Replan. Other planner errors keep Try again. A live re-plan overflow puts the same text in the message note.
- **One token per UTF-8 byte:** `estimate_tokens` = int(ASCII chars / 3.2) + UTF-8 bytes of the non-ASCII code points (2-4; a lone surrogate 3) + 1; a byte-level BPE never needs more tokens than bytes, so this is an upper bound. ASCII estimates are unchanged. Heavy shapes (first / repair, budget 9216): emoji feedback 4x500 6485 / 9207, CJK feedback 4x500 6486 / 9208, emoji command 500 + emoji feedback 8329 / 9206, CJK command 1000 8858 / 9626 (repair fails closed), emoji command 1000 9858 / 10626 and emoji answers 3x500 10971 / 11739 (both fail closed).
- **Repair prompt encodes:** lone surrogates in a repair pass's previous output become U+FFFD before the prompt is built. Prompt text unchanged; PROMPT_VERSION stays hive.prompt.v3.4.

## WS5 verify 4 notes
- **Only rendered feedback is applied:** a revision's message context is the delivered answers plus the feedback the cap actually put in the prompt (newest 4, 1200 characters). An older message the cap left out gets the note "Left out of the planner prompt: it shows only your newest messages (at most 4, about 1200 characters). Send it again if it still matters." and is never tagged to a revision; I3 cancels it when the mission ends.
- **A confirmed stop is the stop:** a canceled report for a task the operator stopped is `operator_stop` even when the robot attaches its own error (kept in the message), so a later real fault in the stage still becomes the recovery failure.
- **I3 covers clarification answers:** an answer whose plan was rejected or stopped before approval is canceled with the unapplied note, like feedback.
