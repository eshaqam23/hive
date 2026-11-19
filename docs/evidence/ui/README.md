# UI evidence

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
