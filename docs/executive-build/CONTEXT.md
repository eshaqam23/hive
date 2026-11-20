# Executive build: context for every prompt

## Goal
In the chat, I type a mission. The Executive (exec_v2 logic) breaks it into tasks, assigns each
task to a car, and writes the exact command each car's instance agent will receive. I can
approve, edit, reject, stop a task, or replan. Approved tasks go out over the AgenticROS/Synapse
bridge as ROS 2 ExecuteSkill actions. For now the fake fleet answers them. The real instance
agent comes later, behind the same action names.
UI: three tabs plus a settings page.
- Fleet: a grid of robot panels; clicking a panel opens the existing robot page.
- Map: a UI shell for the shared perception map.
- Chat: Claude-like, with the plan table and controls.
- Models: a model router where I add API keys and endpoints for hosted OSS-model providers,
  Ollama, a vLLM server on VT ARC, and any local model.

## Sources
- origin/feature/live-data: the BASE. A React + Vite 7 + Tailwind v4 front end, and a
  stdlib-Python backend on :8787 with one rclpy worker per car. It already has hold-to-drive,
  deadman, stop confirmation, the Live view (camera / LiDAR / topics), camera quality, and the
  Windows launcher that runs the backend inside WSL. Docs: README.md, CHANGES.md,
  docs/robot-setup.md, backend/test/README.md,
  docs/superpowers/specs/2026-11-13-hive-live-data-design.md.
- origin/exec_v2: the EXECUTIVE LOGIC: backend/hive_exec/ (FastAPI on :8788), src/executive/,
  backend/requirements.txt, and backend/tests/test_{api,contracts,executive,qwen_planner,
  validator}.py. It has the Qwen planner with Ollama + vLLM, plus Baseline, validator,
  allocator, scheduler, recovery, edit ops (set_robot / remove_task / set_param), replan,
  cancel, and a fake gateway + fake world with faults. It branched from an OLD base (db824ff),
  so it lacks the live-data redesign.
- origin/exec: has the BRIDGE that exec_v2 lacks: bridge/, ros/ (hive_interfaces with the
  ExecuteSkill action and FleetControl service; hive_fake_robots), backend/scripts/
  (b_e2e.py, smoke_eval.py), docs/delivery-b-agenticros.md, docs/executive-architecture.md,
  docs/next-steps.md, docs/run-report.md, docs/evidence/. Do NOT take exec's
  backend/hive_exec/** (exec_v2's is newer), src/App.jsx, scripts/hive.mjs or package.json.
- ../hive-summer (Bhavya007-17/hive, private, read-only): the summer build. Port PATTERNS from:
  src/hive_llm/hive_llm/{providers,probe,config}.py,
  src/hive_dashboard_server/hive_dashboard_server/providers_api.py,
  dashboard/src/settings/{SettingsPage,ProviderCard}.tsx,
  src/hive_llm/test/{test_providers,test_probe}.py, and DECISIONS.md.
  Convert to this repo's stack: JavaScript/JSX (not TS), httpx (not the openai/instructor
  SDKs), FastAPI inside hive_exec. Do NOT port: ROS hive_executive, hive_msgs, Foxglove,
  Gazebo/PX4, the Composer or the onboarding wizard. Put
  "ported from Bhavya007-17/hive@<sha> <path>" in the commit message of anything ported.

## Reference images
If they're in docs/ui-reference/, use them; otherwise use these descriptions.
- R1 Plan card: header "Plan · rev 1 · 4 tasks · LLM proposed → deterministic checks passed
  4/4" and a status legend (queued / in progress / done / failed). Table columns: TASK | WHAT |
  SENT TO | COMMAND TO AGENT | STATUS. A row looks like: T1 · Map west half of lab · → Rover 1 ·
  explore(region="lab_west", max_s=180) · queued. A blocked row says "waits on T1–T3". Buttons:
  Approve & run (primary) · Edit plan · Reject. Footer: "Nothing moves until you approve."
  Above the card is a collapsible "Executive thinking · 4.2 s · read fleet registry, live state,
  shared map" with numbered lines.
- R2 Claude-like column: an "Earlier missions" list on top, the user's command as a
  right-aligned bubble, a "Plan reasoning" card, then the plan card with a terracotta border
  while AWAITING APPROVAL. The input bar at the bottom says "Describe a mission" (Enter sends,
  Shift+Enter adds a new line). A Fleet rail on the right with name, type, state chip and a
  battery bar. Header: HIVE wordmark · LIVE dot · theme toggle · E-STOP.
- R3 The same column after completion: steps ticked green with SUCCEEDED, then a "Mission
  complete · 5 min 01 s · all 2 step(s) succeeded" card with a green left edge.
- R4 Running state: tabs in the header; a "mission running · rev 1 · 01:42" pill; a red STOP
  ALL button. Row states: done (with report "map saved · 94% coverage · 2:31"); in progress
  (with a progress bar and a "Stop current" button); failed (with a red error code like
  "NO_PATH · hallway blocked at x=4.2 m · 2 retries used · rover holding" and a "Replan"
  button); blocked. A "Replan proposal · rev 2 · triggered by T3 NO_PATH" card lists the
  numbered changes, with buttons Approve rev 2 · Edit · Keep rev 1. The right rail has a
  "Shared map · 61% known" mini map and an "Agent reports" timeline.
The images are LAYOUT guidance. Keep live-data's visual system: its Tailwind v4 @theme tokens,
dark mode, terracotta accent, and fonts (Newsreader / Hanken Grotesk / JetBrains Mono). Use mono
for robot ids, skills, commands and states. Add no new font families.

## Ports and processes
live-data car backend 8787 · Vite 5173 (strict; the backend only accepts this origin) ·
Executive 8788 · bridge 8790 · rosbridge 9090 · fake fleet on ROS_DOMAIN_ID 77 with
LOCALHOST discovery (the cars use 42/43/44).
Executive venv: ~/.venvs/hive-exec (inside WSL on Windows). Provider store:
~/.hive/providers.yaml.

## Hard rules
1. Do not change live-data's safety behaviour: hold-to-drive, the 10 Hz stream, the deadman, the
   stop burst + ros_data confirmation, command ordering (session/seq), the reconnect loop, or
   stop-on-disconnect/exit. All of live-data's existing tests must still pass unchanged.
2. Do not change the Executive's decision semantics: validator, allocator, scheduler, recovery
   budget, approval bound to (revision, proposal_hash), and "effects only from accepted
   results". Only the additions in prompt 5 are allowed. Do not add or rename skills, robots,
   locations or routes in catalog.py.
3. No secret ever goes into the repo, a log, an HTTP response or a commit. .env and
   providers.yaml must be gitignored anywhere in the tree. Check with `git grep` before every
   commit.
4. Simulated data is always labeled. Any robot state from the fake world or fake fleet
   (location, battery, availability) shows a SIM tag. Real connection data comes only from the
   live-data backend.
5. Ask before any sudo, any system package install, installing Ollama/CUDA/vLLM, editing
   .wslconfig, or touching firewall rules. Never ssh anywhere.
6. Use small commits. Keep fix commits separate from chore/docs commits.
7. Work only on feature/executive. Push only in prompt 11, never to another branch. No PR.
8. At every GATE, paste real output (test counts, curl bodies, screenshot paths). If a gate
   fails, fix it or stop and say so.

## Baseline
Taken 2026-11-18 before any code change, on feature/executive = origin/feature/live-data
(76dc4d4) + this doc. Machine: Windows 11 laptop (RTX 5070 Laptop GPU), WSL2 Ubuntu 24.04.
- `npm test` (vitest, Windows Node 24.13.0): 15 files, 109 tests passed.
- `python3 -m pytest backend/tests -q` (WSL, Python 3.12.3, pytest 7.4.4): 152 passed.
- Mock e2e (`backend/server.py` with HIVE_ROS_SETUP=backend/test/mock_ros/setup.bash, then
  `python3 backend/test/test_e2e.py backend/test/mock_ros`): 62/62 passed.

## Test commands
Run from the repo root inside WSL. One interpreter can't run both sets: system python3 has
live-data's deps (cv2) but no pytest-asyncio, so it skips the 24 async exec tests; the
hive-exec venv has FastAPI + pytest-asyncio but no cv2, so test_adapters.py fails to collect.
System pytest 7.4 warns "Unknown config option: asyncio_mode"; that is expected.
- live-data (system python3, expect 152):
  `python3 -m pytest backend/tests -q --ignore=backend/tests/test_api.py --ignore=backend/tests/test_contracts.py --ignore=backend/tests/test_executive.py --ignore=backend/tests/test_validator.py --ignore-glob='backend/tests/test_llm_*'`
- exec (hive-exec venv, expect 503 after WS4; 442 after WS3; 411 after the WS2 fixes; 390 after WS2; 308 after WS1; 244 after prompt 9):
  `~/.venvs/hive-exec/bin/python -m pytest backend/tests/test_api.py backend/tests/test_contracts.py backend/tests/test_executive.py backend/tests/test_validator.py backend/tests/test_llm_*.py -q`
- Also run `npm test` (Windows Node, expect 341 after WS4, 35 files; 322 after WS3; 315 after the WS2 fixes; 313 after WS2; 305 after WS1; 297 before it) and the mock e2e from the Baseline section.
- Prompt evaluation against a live model (WS2, not part of the suites): `backend/scripts/prompt_eval.py` runs the ten
  labelled commands through the Executive on the fake fleet with the `executive` role of an explicitly named provider
  store, prints a PASS/FAIL table and exits 1 on any failure (2 when the store or provider is unusable). Keys are never
  printed. On the desktop, with the `executive` role bound to Ollama `qwen3:8b`:
  `cd ~/dev/hive/backend && HIVE_PROVIDERS_FILE=~/.hive/providers.yaml ~/.venvs/hive-exec/bin/python scripts/prompt_eval.py --runs 3 --seed 1234`
  (run r samples with seed 1234 + r - 1; the store file is read by the script, never printed). It prints each
  executive row's effective sampling first and a separate pass rate for unseen paraphrases (`in_prompt: false`).
- Settings > re-save the executive row if prompt_eval shows temperature 0.7.
