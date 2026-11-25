# UI evidence

## Gate 10: end-to-end in a real browser (prompt 10)

Captured 2026-11-19 with Playwright (Chromium, dark theme, viewport 1440×1500). Images are in
`e2e/`. The stack was one launcher:
`HIVE_ROS_SETUP=$PWD/backend/test/mock_ros/setup.bash npm run hive` from Git Bash. That started
the car backend on the mock ROS, the fake fleet (ROS_DOMAIN_ID 77), the bridge, the Executive
(gateway synapse) and Vite. The Executive read the real `~/.hive/providers.yaml`: the executive
role was already bound to groq · openai/gpt-oss-120b. Nothing was saved to the store, so its
mtime is unchanged and no `.v1.bak` exists. Steps 1–8 used the Baseline planner chip. Step 9
used the Groq chip.

Fix found and made on the way: `npm run hive` did not pass `HIVE_ROS_SETUP` into WSL, so the car
backend could not use the mock ROS (93c7ce6, `fix: npm run hive passes HIVE_ROS_SETUP into WSL as
a WSL path`). The launcher log then read `ROS 2 setup /mnt/c/dev/hive/backend/test/mock_ros/setup.bash: found`.

| Step | Result | Images |
|---|---|---|
| 1. Fleet, robot page, back | PASS. Three panels (Camera, LiDAR, Gripper car), each with its Executive SIM status. The camera car page has h1 "Camera car". Back returns to "Your fleet". | `01-fleet.png`, `01-robot-page.png`, `01-fleet-back.png` |
| 2. Map empty state, DEMO | PASS. "Shared perception map not built yet". The DEMO watermark is shown when on and gone when off. | `02-map-empty.png`, `02-map-demo-on.png`, `02-map-demo-off.png` |
| 3. exec_v2 demo, Baseline | PASS. Mission m1-f84ea0, run run-ffa5ef06. Thinking card, then rev 1 (observation stage) awaiting approval with COMMAND TO AGENT filled. Approve & run. Rows go accepted, in progress, done. Then the rev 2 continuation is approved and ticks to done. "Mission complete · 1 min 16 s · all 6 step(s) succeeded". `runs/run-ffa5ef06/agent_commands/`: `camera_01/t1-r1-a1.json`, `lidar_01/t2-r1-a1.json`, `gripper_01/t{1,2,3,4}-r2-a1.json` (schema `hive.task-envelope.v1`). | `03-chat-start.png`, `03-awaiting-approval.png`, `03-rev1-tick-{0,3,5}.png`, `03-rev2-running-{1,3}.png`, `03-complete.png` |
| 4. nav_fault_after_pickup | PASS. Known scenario, fault injected from the Sim drawer. At 8 s T3 is "failed motor_stall" with "drive motor stalled; robot stopped in place", and T4 is skipped. "Replan proposal · rev 2 · triggered by T3 motor_stall" shows the diff "T1, T2 done in rev 1, not repeated" and "2 tasks unchanged (T3→T1, T4→T2)". After Approve rev 2: "Mission complete · 27 s · 4 of 5 step(s) succeeded". | `04-inject.png`, `04-failed-row.png`, `04-replan-proposal.png`, `04-outcome.png` |
| 5. hang_navigate, Stop current | PASS. T1 hung in accepted. Stop current gives "canceling", then within 0.25 s "canceled operator_stop" with "Operator stopped t1; robot confirmed.". "Replan proposal · rev 2 · triggered by T1 operator_stop", checks passed 4/4. | `05-hang-running.png`, `05-canceling.png`, `05-canceled.png`, `05-recovery-proposal.png` |
| 6. Edit plan | PASS. Invalid edit: T2 to lidar_01. The binding moves T1–T4 together, giving rev 3 "deterministic checks failed 0/4" with per-task errors (`unsupported_assignment … cannot reach pickup_zone`, `no_route No route from lidar_post to pickup_zone.`, `over_payload red_block 0.1 kg exceeds 0.0 kg.`). Approve rev 3 is disabled: "This revision cannot run: its checks failed." Valid edit: T1 back to gripper_01 gives rev 4 "operator edit → deterministic checks passed 4/4" with the reassignment diff. | `06-edit-open.png`, `06-invalid-edit.png`, `06-valid-edit-rev4.png` |
| 7. Refresh mid-mission | PASS. Rev 4 is running with T1 hung by hang_navigate. After a reload, the Chat main text and the Mission context aside are byte-identical to before (timers masked), and the header still reads "mission running · rev 4". The reload lands on the Fleet tab, because the view is not in the URL. Fleet already showed "gripper_01 executing t1 navigate_to". | `07-before-refresh.png`, `07-after-refresh-raw.png`, `07-after-refresh.png` |
| 8. STOP ALL mid-mission | PASS. With the mock camera car connected, the STOP ALL results read "Camera car Stopped" and "Executive mission cancel accepted". The chat shows "Mission canceled · 3 min 28 s · 0 of 2 step(s) succeeded · Operator canceled; all active tasks confirmed terminal.". The launcher log has `POST /api/robots/drive` (the car stop) and `POST /api/exec/cancel`. | `08-camera-car-connected.png`, `08-stop-all-results.png`, `08-mission-canceled.png` |
| 9. exec_v2 demo, LLM planner | PASS. Models page Test: groq OK 1.6 s (models ✓, chat ✓ 232 ms, schema ✓ 1.2 s). Mission m4-c1c912, run run-410b9d7c, Groq chip. Rev 1 (observation, 2 tasks) and rev 2 (continuation, 4 tasks) were both proposed by the LLM and passed checks. "Mission complete · 36 s · all 6 step(s) succeeded". The agent_commands files match step 3. PlannerRecords are below. | `09-models-test.png`, `09-chat-start-llm.png`, `09-thinking.png`, `09-awaiting-approval.png`, `09-proposal-{1,2}.png`, `09-tick-{0,3,7}.png`, `09-complete.png` |
| 10. Car regression | PASS. Disconnect, then Connect the camera car from its Fleet panel: Live, link online. On its page, held W: "Driving forward". Released: "Connected · Stopped" and "Car confirmed 0,1,0,1". The activity log reads "drive stop #3: stop sent (zero Twist burst)" after "Released forward after 1.4 s (5 keepalives answered)". | `10-connected.png`, `10-robot-page.png`, `10-holding-w.png`, `10-released-stopped.png` |

PlannerRecords for step 9 (from `GET /api/exec/state`):

| Rev | Provider · model | Latency (provider) | Tokens prompt / completion / total (reasoning) | Est. input | Fallback | Repaired |
|---|---|---|---|---|---|---|
| 1 initial | groq · openai/gpt-oss-120b | 2050.9 ms (2049.2) | 3738 / 565 / 4303 (349) | 2960 | no | no, first pass ok |
| 2 continuation | groq · openai/gpt-oss-120b | 2326.1 ms (2324.4) | 3801 / 836 / 4637 (552) | 3036 | no | no, first pass ok |

Both use `prompt_version hive.prompt.v2` and `structured_mode json_schema`, with temperature
0.7, top_p 0.8 and seed 1234.

Key suffixes on the Models page were replaced with `…[masked]` in the DOM before
`09-models-test.png` was taken. The browser console had only React DevTools info lines during
the run. The `ERR_CONNECTION_REFUSED` lines came after the stack was stopped.

### Seen during the run, not changed in prompt 10

- **Gemini fails its Test:** `HTTP 400 … Unknown name "seed": Cannot find field.` (visible in
  `09-models-test.png`). The hosted presets' sampling (`HOSTED_SAMPLING` in
  `backend/hive_exec/llm/templates.py`) and the `Sampling.seed` default of 1234 send `seed`,
  which Gemini's OpenAI endpoint rejects. Groq accepts it.
- **Reload goes to Fleet:** the view tab is not in the URL, so a reload opens Fleet. The mission
  state is unaffected.
- **Header pill:** it shows the active revision ("rev 1") while a later proposal awaits
  approval, as noted at Gate 8.

## Gate 9: Models page (prompt 9)

Captured 2026-11-19 with Playwright (Chromium, dark theme). The viewport was 1440×2300 so the
whole page fits in one image. The Executive ran against a throwaway store
(`HIVE_PROVIDERS_FILE=/tmp/hive-gate9/providers.yaml`), never `~/.hive/providers.yaml`. The
store was seeded with three keyless rows through `POST /api/exec/llm/providers`, and the
executive role was bound to `["ollama", "arc-vllm"]` through `POST /api/exec/llm/roles`. No key
appears in either image.

| File | What it shows |
|---|---|
| `models.png` | Roles on top. Executive: plans missions is bound to ollama · qwen3:8b, with arc-vllm as fallback 1 and the model hint "No model list: unreachable: http://127.0.0.1:11434 (ConnectTimeout). Type any id.". Instance agent: not built yet is not bound. Below are the provider cards for VT ARC vLLM, Cerebras and Ollama (local), each with kind, base_url, model, "none via <KEY_ENV>" and role chips. Ollama's Test result reads FAILED 10.0 s with the verbatim error "unreachable: http://127.0.0.1:11434 (ConnectTimeout)", because Ollama was not running. |
| `models-templates.png` | The Add provider picker, filled from `GET /api/exec/llm/templates`. It lists Ollama (local), then VT ARC (vLLM) with its tunnel hint. Next is Hosted OSS-model provider with presets Groq, Cerebras (marked "added"), OpenRouter, Together AI, Google Gemini and Other. Last is Local OpenAI-compatible server with LM Studio, llama.cpp server, vLLM (local) and Other. |

The console showed only `ERR_CONNECTION_REFUSED` on `127.0.0.1:8787/api/health`. The car
backend was not started for this gate.

## Gate 8: mission chat (prompt 8)

Captured 2026-11-19 with Playwright (Chromium, 1440×900, dark theme) on the synapse gateway with
the Baseline planner. Every robot state here comes from the fake ROS fleet and carries a SIM tag.

| File | What it shows |
|---|---|
| `chat-awaiting-approval.png` | Rev 1 awaiting approval: the open thinking card (Baseline (no LLM), latency, what it read, four lines from summary, planning log and allocation reasons), the plan table with checks passed 2/2, Approve & run · Edit plan · Reject, "Nothing moves until you approve.", the composer disabled with its reason, and the sim drawer collapsed with the armed fault |
| `chat-running.png` | Rev 2 executing: T1 done with its report line, T2 accepted with Stop current, T3 and T4 blocked ("waits on T2", "waits on T3"), the gripper car busy · T2 in the fleet list, and agent reports newest first |
| `chat-failed-replan.png` | Rev 2's T3 failed `motor_stall` with its report line and Replan, T4 skipped, the recovery thinking card, and "Replan proposal · rev 3 · triggered by T3 motor_stall" with its diff against rev 2 |
| `chat-complete.png` | Rev 3 finished and "Mission complete · 4 min 16 s · 6 of 7 step(s) succeeded", with rev 2's T3 failure shown as recovered, the Export run link and Start a new run |

### How the stack was run

The four services ran in WSL from the repo root, started by one script. The UI ran on Windows.

- Car backend: `HIVE_ROS_SETUP=backend/test/mock_ros/setup.bash python3 backend/server.py` (port 8787)
- Fake ROS fleet: `bash ros/run_fake_fleet.sh` (ROS_DOMAIN_ID 77)
- Synapse bridge: `node bridge/server.mjs` (port 8790, rosbridge on 9090)
- Executive, from `backend/`: `HIVE_GATEWAY=synapse HIVE_DEFAULT_PLANNER=baseline HIVE_PROVIDERS_FILE=<temp file> HIVE_RUNS_DIR=<temp dir> ~/.venvs/hive-exec/bin/python -m hive_exec` (port 8788).
  The temporary providers file keeps the real `~/.hive/providers.yaml` out of the run, so the LLM planner reports "not bound".
- UI, on Windows: `npx vite --host 127.0.0.1` (port 5173)

`GET /api/exec/health` during the run reported `"gateway":{"kind":"synapse","label":"AgenticROS / Corebrum Synapse bridge","connected":true,"verified_round_trip":true,…}`, `"default_planner":"baseline"`, and the bridge and rosbridge both connected.

### The run

1. In Sim controls: scenario "Unknown object/route", fault `nav_fault_after_pickup` checked, then Reset simulation.
2. Sent with the Baseline chip: "Find the red block in the work area, check the pickup corridor, and bring the block to the drop-off area."
3. Rev 1 (observation stage: T1 `inspect_area` on camera_01, T2 `scan_route` on lidar_01). Approved. It finished in about 3 s, so no screenshot shows rev 1 running.
4. Rev 2 (4 tasks on gripper_01) arrived awaiting approval. Approved, then captured running.
5. T3 `navigate_to` failed with `motor_stall`, T4 was skipped, and the Executive proposed recovery rev 3 (2 tasks). Captured, then approved.
6. The mission completed. Its duration includes the time spent waiting for each approval.

After a page reload the thread repainted all three revisions and the completion card, and the
finished mission was kept in localStorage (`hive.chat.earlier.v1`, 3 revisions, export link).

### Seen during the run, not changed in prompt 8

- The header pill shows the Executive's `active_revision`, so it reads "rev 1" while rev 2 waits for approval.
- At a 390 px wide viewport the header is 528 px wide and STOP ALL sits off-screen. It happens on every tab, and the header layout is unchanged since Gate 7.

## Gate 7: fleet, robot page and map (prompt 7)

Captured 2026-11-19 with Playwright (Chromium, 1440×900, dark theme).

| File | What it shows |
|---|---|
| `fleet.png` | Fleet tab: three saved robots, the camera car Live, Executive status tagged SIM on each panel, the Add a robot card |
| `robot-page.png` | The camera car's robot page, opened from its Fleet panel, after the W hold and release: "Connected · Stopped" |
| `map.png` | Map tab with the Executive's real reply (`available: false`): the empty state |
| `map-demo.png` | Map tab with the dev-only **Demo data** toggle on. It is a fixture and is watermarked DEMO |

### How the stack was run

The stack was started by hand, not with `npm run hive`. The launcher does not pass `HIVE_ROS_SETUP` through to WSL.

- Car backend, from the repo root in WSL:
  `HIVE_ROS_SETUP=backend/test/mock_ros/setup.bash python3 backend/server.py` (port 8787)
- Executive, from `backend/` in WSL:
  `HIVE_GATEWAY=fake ~/.venvs/hive-exec/bin/python -m hive_exec` (port 8788)
- UI: `npx vite --host 127.0.0.1` (port 5173)

### live-data regression on the mock backend

These steps were run after a full page reload:

1. On Fleet, the camera car's panel showed "Offline". Clicked its Connect button. The panel went Live, with link online and 3 nodes · 7 topics.
2. Clicked the panel. The robot page opened unchanged. The back button reads "Fleet".
3. Held W for 1.2 s, then released.

| Time | Event |
|---|---|
| 15 ms | `POST /api/robots/drive` action `forward`, seq 1 |
| 1200 ms | Page showed "Connected · Driving forward" |
| on release | Page showed "Connected · Stopping…" |
| 1244 ms | `POST /api/robots/drive` action `stop`, seq 2 |
| 1295 ms | Page showed "Connected · Stopped" |

- Confirmation: "Car confirmed 0,1,0,1 · 328 ms ago".
- Console: 0 errors.

The Map tab, with the demo toggle on and then off, also logged no console errors or warnings.

## A8: Map with the SIM map, Fleet with the EMULATED stub (branch feature/agent-integration)

Captured 2026-11-25 on the laptop (WSL Ubuntu 24.04) in headless Chromium 151 (Playwright's
cached chrome-headless-shell, no system packages installed), dark theme, viewport 1440×1000.
Images are in `a8/`. Nothing here comes from a car or from hive-agent.

| Image | Stack | What it shows |
|---|---|---|
| `a8/map-sim.png` | `node scripts/hive.mjs --no-open` with `HIVE_ROS_SETUP=backend/test/mock_ros/setup.bash`, `HIVE_CORE_DIR=~/.hive-bridge-a8/core` (patch v2) and a throwaway `HIVE_PROVIDERS_FILE`: car backend on the mock ROS, fake fleet (ROS_DOMAIN_ID 77), bridge, Executive (gateway synapse), Vite. Then, through the API: reset (world fixed), mission "Map the arena" with the Baseline planner, approve rev 1. | Run run-7b1f4810, mission m1-26a012 completed. One task, `map_area` on lidar_01. `GET /api/exec/map`: available, 80×60 cells, provenance `{source: map_area, robot_id: lidar_01, attempt_id: 1.t1.a1-6810, coverage: 0.95, evidence: simulated}`. The Map tab reads "map_area by lidar_01 · 95% coverage", with the SIM tag. The fake fleet's map is synthetic. |
| `a8/fleet-emulated.png` | The Executive in-process on the test fixture `StubFleetGateway(agent_robots(full_caps()), EMULATED_FLEET)` from `backend/tests/fleet_stub.py` (no fleet, no bridge, no skill runs), and Vite. Three saved cars were put in the page's localStorage first, because the Fleet tab shows Executive blocks only on saved cars. | Fleet report `{kind: hive_agent, profile: laptop, evidence: emulated}`. Each car's Executive block has the EMULATED tag, "idle", "battery unknown" (the stub sends no battery_pct) and the stub's location. The car links are offline: no car backend ran. |
