# HIVE Executive: run report (2026-11-11, branch `exec`)

Delivery A and Delivery B are reported separately. "Baseline" means the labeled template
planner, and "mock" means a scripted OpenAI-compatible test double. Neither is live Qwen.

## Environment

- Windows 11, Python 3.12.10 (`backend/.venv`, pins in `backend/requirements.txt`), Node 24.19, Vite 7, React 19.
- WSL `Ubuntu-24.04` with ROS 2 Jazzy, rosbridge_suite, Node 22.
- **No GPU on this machine and no vLLM endpoint configured.**

## Delivery A: Executive demonstration

| Item | Status | Evidence |
|---|---|---|
| Typed contracts, schema `hive.plan-proposal.v1` | Done | `test_contracts.py` (Appendix A parses; clarify cannot carry tasks; extra fields and unknown IDs rejected; saved schema matches models) |
| Registry, capability matching, carrier binding on one robot | Done | `test_validator.py` |
| Deterministic checks (IDs, skills, deps/cycles, possession order, route evidence, battery reserve, availability, resource conflicts) | Done | `test_validator.py`, `test_executive.py` |
| Observed world separate from simulator truth | Done | `test_hidden_truth_does_not_leak_into_context` (context and UI state) |
| Approve / Edit / Reject, stale approval | Done | `test_stale_approval`, `test_api.py` |
| Fake execution, 3 concurrent agents, map from reports | Done | stories below; UI screenshots taken during development |
| Cancellation confirmed before "canceled" | Done | `test_confirmed_cancellation` |
| Duplicate / old-run / late events | Done | `test_duplicate_and_old_run_events`, `test_late_result_for_uncertain_attempt_is_reconciled_not_advanced` |
| One bounded recovery, possession preserved | Done | `test_story_failure_after_pickup_recovers`, `..._honest_block` |
| Timeline, JSON export | Done | `GET /api/exec/export` (`test_http_known_story_and_export`) |
| Qwen planner (non-thinking, structured outputs, probe, one repair, budget, latency logging) | **Implemented, verified against a scripted mock only** | `test_qwen_planner.py` |
| **Live Qwen** | **Not run: no endpoint** | see Blockers |

`pytest`: **45 passed** (`cd backend && .venv/Scripts/python -m pytest -q`).

### Five acceptance stories

| Story | Baseline + fake fleet | Baseline + Synapse/ROS | Live Qwen |
|---|---|---|---|
| Known-object delivery | pass | pass | not run |
| Unknown object/route (sensing, then transport with new approval) | pass | pass | not run |
| Camera-only (others idle) | pass | pass | not run |
| Ambiguous / unsupported (clarify / reject, no dispatch) | pass | pass (clarify) | not run |
| Failure after pickup (approved recovery or honest block) | pass (both) | pass (both) | not run |

Extra fake-fleet cases that pass: block missing → blocked, corridor blocked → blocked, two red
blocks → clarification → answered with an ID, low battery → validation refuses approval,
uncertain place → blocked without retry.

Spec section 11 makes A reviewable only when these stories run with **live Qwen**. That has not
happened. The deterministic pipeline is ready for it.

### Smoke evaluation

- Harness: `backend/scripts/smoke_eval.py` (10 labeled commands × 2).
- **Baseline dry run** (`--planner baseline --execute`): 20/20 matched expected dispositions; all
  executed cases reached the expected final status. This checks the harness, not Qwen.
- **Live Qwen run: not performed.** The script exits with "Qwen probe failed" when the endpoint is absent.

### Prompt budget

The `hive.prompt.v1` system prompt (catalog, rules, 5 examples) is about 2,000 estimated tokens and
the demo context about 320. With the schema the input is about 2,900 of the 9,216 allowed. The
estimate is a character heuristic, not the Qwen tokenizer; check `usage.prompt_tokens` on the first live run.

## Delivery B: AgenticROS / Corebrum Synapse

Details: `docs/delivery-b-agenticros.md`.

| Item | Status |
|---|---|
| Actual `@corebrum/core` 0.8.16 at Synapse `6f7b7840…`, rosbridge, 3 ROS 2 fake action servers in one shared world | Done (runtime) |
| Early check: success, rejection, feedback, abort, targeted cancel, concurrent second robot | Unpatched 5/8 (feedback, GoalStatus and targeted cancel fail, as source inspection predicted). With patch v1: 8/8 |
| Narrow versioned patch | `bridge/patches/corebrum-core-rosbridge-actions.v1.patch` |
| Executive end-to-end through Synapse | 8/8 stories (Baseline planner, labeled) — `docs/evidence/b-e2e.json` |
| Duplicate/late events, uncertain outcomes, no blind redispatch | Done; a real deadlock that produced a false "failed" pick was found and fixed |
| Disconnect / restart handling | Bridge kill mid-task blocks the run; startup performs a coordinated reset |
| UI over Synapse | Demo completed in the browser; the transport badge shows verified only after a round trip |
| rclpy fallback | Not built (not needed) |

B has adapter round-trip evidence for the transport. It has **not** been combined with live Qwen.

## Blockers and follow-ups

1. **Live Qwen (blocks A acceptance).** Host `Qwen/Qwen3-8B` on a GPU machine (24 GB-class GPU
   recommended for bf16 with a 12k context), e.g.
   `vllm serve Qwen/Qwen3-8B --max-model-len 12288 --seed 1234 --api-key …`. Tunnel it to
   `127.0.0.1:8000`, set `QWEN_BASE_URL`/`QWEN_API_KEY` in `.env`, then run:
   - `POST /api/exec/planner/probe`, which must report `ok`
   - the five stories in the UI with planner "Qwen3"
   - `cd backend && .venv/Scripts/python scripts/smoke_eval.py --execute`
   - `.venv/Scripts/python scripts/b_e2e.py --planner qwen` for Qwen over Synapse

   If the server's vLLM predates `structured_outputs`, set `QWEN_STRUCTURED_MODE=guided_json`.
2. **OR-Tools allocator** (checklist completion) is scheduled behind `Allocator`. It is not started.
3. **Synapse patch upstreaming.** The v1 patch should be proposed upstream or re-verified at any new pin.
4. **Full Synapse workspace build** fails on `rclnodejs` (needs a sourced ROS env). Only the core package is built.
5. Hardware: none exercised. Simulation does not establish navigation or manipulation ability.
