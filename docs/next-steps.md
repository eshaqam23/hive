# Executive: next steps

For anyone picking up work on the `exec` branch. Read this first, then the
[README](../README.md) section "Executive" for how to run and test.

## Where things stand (2026-11-11)

The Executive is built end to end. You type a mission in English; a planner proposes tasks;
HIVE checks them with fixed rules; an operator approves; three robots carry them out; and the
map and outcome come only from what the robots report.

| Area | State |
|---|---|
| Delivery A: planner, checks, approval, fake robots, UI | Working. 45 automated tests pass. All five acceptance stories pass **with the Baseline planner** |
| Qwen3-8B planner code | Written, but tested only against a scripted mock |
| **Live Qwen** | **Never run: we have no model server.** This blocks A's acceptance |
| Delivery B: same flow through AgenticROS/Synapse to ROS 2 fake robots | Working: 8/8 transport checks and 8/8 stories (Baseline planner) |
| Real robots | Not connected to the Executive. Manual control on the robot pages is unchanged |

Full evidence: [run-report.md](run-report.md). Design: [executive-architecture.md](executive-architecture.md).
ROS path: [delivery-b-agenticros.md](delivery-b-agenticros.md).

## Next steps, in priority order

### 1. Get a Qwen3-8B server running (blocker)

Nothing else matters for acceptance until this is done.

- [ ] Find a GPU: lab machine, cloud GPU, or a free Colab/Kaggle GPU over a tunnel.
      bf16 with a 12k context needs roughly a 24 GB-class card.
- [ ] Serve it: `vllm serve Qwen/Qwen3-8B --max-model-len 12288 --seed 1234 --api-key ...`
- [ ] Tunnel it to `127.0.0.1:8000` and set `QWEN_BASE_URL` / `QWEN_API_KEY` in `.env` (never commit `.env`).
- [ ] `POST /api/exec/planner/probe` must return `ok: true`. On an older vLLM, set `QWEN_STRUCTURED_MODE=guided_json`.
- [ ] Run the five stories in the UI with planner **Qwen3**.
- [ ] Run `backend/scripts/smoke_eval.py --execute` (10 commands × 2) and `b_e2e.py --planner qwen`.
- [ ] Add the results (counts, failures, first-pass vs repaired, latency) to `run-report.md`.

If no GPU is available soon, we could point the planner at Ollama (CPU, slow) or a hosted
Qwen3-8B API. Both need a small code change, and they don't match the spec's vLLM setup.
Results from either must be labeled as such.

### 2. Tune the prompt using real Qwen output

- [ ] Look at failures from the smoke evaluation: wrong disposition, invalid plans, repairs.
- [ ] Adjust `backend/hive_exec/planner/prompt.py`: rules and the 5 examples. Bump `PROMPT_VERSION`.
- [ ] Record the real `usage.prompt_tokens`. Our budget check uses a rough estimate (~2,900 of 9,216).
- [ ] Compare temperature 0.7 (Qwen's recommendation) with 0 (repeatability experiment).

### 3. Review and merge

- [ ] Open a PR from `exec` to `main`. The commits are layered so each can be reviewed separately.
- [ ] Decide how this fits with the unmerged `feature/pipeline-frontend` / `pipeline-design-ideas`
      branches, which move the frontend into `frontend/`. `exec` is based on `main` and adds
      `src/executive/`, so a merge will need that folder moved.

### 4. Synapse follow-ups

- [ ] Propose our 3-fix patch upstream (`bridge/patches/corebrum-core-rosbridge-actions.v1.patch`:
      feedback flag, cancel by goal id, pass through GoalStatus), or re-run `bridge/check.mjs`
      whenever we change the Synapse pin.
- [ ] Optional: a status query after a disconnect. Today the Executive blocks until a coordinated reset.

### 5. OR-Tools allocator (spec checklist item)

- [ ] Implement it behind the existing `Allocator` interface (`backend/hive_exec/allocator.py`).
      Keep the current capability matcher as the default until the new one passes the same tests.
      Keep it bounded; it should not delay steps 1–3.

### 6. Toward real robots

Agree these with the embodied team **before** writing code:

- Units, coordinate frames, robot and location IDs, skill names and versions.
- What counts as evidence a skill worked, e.g. what confirms "block held" or "block released".
- How cancellation behaves, and the point at which a skill's effect counts as done.

Then:

- [ ] Replace the fake action servers in `ros/hive_fake_robots` with nodes that call the real skills.
      Keep the `ExecuteSkill` action contract.
- [ ] Start with **one robot and one skill**, then combine all three.
- [ ] Replace the synthetic durations, battery and limits in `backend/hive_exec/catalog.py` with measured values.
- [ ] Check this coexists with manual control (separate ROS domain IDs per robot, `/gripper_open` topic).

## Known limitations

- One mission at a time, three robots, one small named-location world. A restart ends the run.
- State is in memory only. Use **Export JSON** to keep a run.
- The Edit button supports simple edits only (change robot, remove a task, change a location).
- No frontend tests yet; the UI was checked manually in a browser.
- The rclpy fallback adapter was not built because Synapse works.

## Working on this code

- **Run tests before every commit:** `cd backend; .venv\Scripts\python -m pytest -q`.
- **Never label Baseline or mock output as Qwen,** and never label the fake fleet as verified ROS.
  The code records which planner and transport produced every plan; keep it that way.
- The planner can only *propose*. Approval, IDs, state changes and dispatch belong to the backend.
- The planner must never see simulator truth (`backend/hive_exec/sim/`). A test checks this.
- A command being sent is not a success. Only a robot's terminal result changes the world.
  Uncertain outcomes block the run and are never retried automatically.
- Repo on OneDrive and Windows: avoid `git stash --include-untracked` (locked folders make it
  fail halfway). Files run in WSL must keep LF line endings (`.gitattributes` handles this).

## Where to start reading

| To understand… | Read |
|---|---|
| Data contracts and the plan JSON format | `backend/hive_exec/contracts.py`, `schemas/plan_proposal.v1.json` |
| Robots, skills and the world map | `backend/hive_exec/catalog.py` |
| The mission lifecycle and scheduling | `backend/hive_exec/executive.py` |
| The rules a plan must pass | `backend/hive_exec/validator.py` |
| The Qwen prompt | `backend/hive_exec/planner/prompt.py` |
| The UI | `src/executive/ExecutiveView.jsx`, `PlanPanel.jsx` |
| The ROS path | `bridge/server.mjs`, `ros/hive_fake_robots/hive_fake_robots/fake_fleet.py` |
