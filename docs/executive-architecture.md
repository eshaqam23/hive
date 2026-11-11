# HIVE Executive: architecture and repository mapping

Implements `HIVE_Prototype_Build_Spec.md` v1.1. Delivery A (fake fleet) and Delivery B
(AgenticROS/Corebrum Synapse) share the same planner, contracts, validation, approval,
scheduler and UI. Only the `RobotGateway` implementation changes.

## Repository mapping (inspected 2026-11-11, `main` @ 9a8652b)

| Existing | Kept as-is | Executive addition |
|---|---|---|
| `src/App.jsx` (Fleet / Robot / Command views, localStorage fleet) | All views and manual control unchanged | One nav item + `src/executive/*` |
| `src/styles.css` (cream/amber panels, chips, pills) | Unchanged | `src/executive/executive.css` reuses the palette and `panel`/`chip`/`status-pill` classes |
| `backend/server.py` (stdlib HTTP, port 8787, `ros2 topic pub` manual control) | Unchanged | Separate FastAPI service `backend/hive_exec` on port 8788 |
| `scripts/hive.mjs` launcher | Same flags | Also starts the Executive from `backend/.venv` (`--no-executive` to skip) |
| no persistence, no tests | — | In-memory state + JSON export; `backend/tests` (pytest) |

The unmerged `feature/pipeline-frontend` / `pipeline-design-ideas` branches (React Flow
pipeline UI in `frontend/`) were not used; `exec` branches from `main`.

## Components

| Spec component | Module |
|---|---|
| Contracts (`PlanProposal`, `TaskSpec`, `ExecutionPlan`, `TaskEnvelope`, `TaskEvent`) | `hive_exec/contracts.py`, schema in `hive_exec/schemas/plan_proposal.v1.json` |
| Capabilities and fake world fixture | `hive_exec/catalog.py` (synthetic values) |
| Observed world / trusted robot state | `hive_exec/world.py` |
| Simulator truth + fake skill semantics | `hive_exec/sim/fake_world.py` (only fake skill implementations import it) |
| Context builder | `hive_exec/context.py` (observed world only; 9,216-token input budget, no truncation) |
| Qwen planner | `hive_exec/planner/qwen.py`, prompt `hive_exec/planner/prompt.py` (`hive.prompt.v1`) |
| Baseline planner (labeled) | `hive_exec/planner/baseline.py` |
| Allocator (interface + capability matching) | `hive_exec/allocator.py` |
| Validator | `hive_exec/validator.py` |
| Scheduler + mission state machine | `hive_exec/executive.py` |
| Robot gateway | `hive_exec/gateway/base.py`; `fake.py` (A); `synapse.py` (B) |
| HTTP API | `hive_exec/api.py` |

## Trust boundaries

- Qwen proposes only `PlanProposal` JSON. It cannot name unknown IDs (closed `Literal` vocabularies), cannot approve, dispatch, write state or call ROS tools.
- Required capabilities come from the catalog; model `preferred_robot_id` is a soft preference.
- IDs, revisions, proposal hashes, approval, run/attempt IDs and timestamps are backend-owned.
- The planner context and UI state are built from the observed world. Hidden truth is only reachable through `/api/exec/debug/truth` (labeled debug).

## Mission lifecycle

```
planning -> awaiting_approval --approve(rev, hash)--> executing -> evaluating
   |             |  edit -> new revision (re-validated)      |          |-- goals satisfied -> completed
   |             |  reject -> rejected                       |          |-- not found / route blocked -> blocked
   |-- clarify -> clarification --answer--> planning        |          |-- ambiguous -> clarification
   |-- reject  -> rejected                                   |          '-- otherwise -> planning (continuation)
   '-- planner failure -> planner_error (visible)            |
                                        task failed/rejected -> recovering -> one recovery proposal -> awaiting_approval
                                        uncertain outcome    -> blocked (no blind pick/place retry)
                                        cancel               -> canceling -> canceled (after terminal confirmation)
```

Approval re-validates against the current state. A changed world, a newer revision or a
hash mismatch rejects the approval as stale.

## Event handling

Events carry `run_id`, `attempt_id`, `event_id` and `seq`. Events from other runs are
discarded; duplicate event IDs and non-increasing sequences are ignored; events after a
terminal status are ignored. Effects are applied once per attempt from accepted terminal
results. Late results for an obsolete revision are reconciled into the world without
advancing the plan. Battery comes from robot telemetry, so completed work is not charged twice.

## Effect commit points

| Skill | Commit point |
|---|---|
| navigate_to | robot reports arrival |
| inspect_area / capture_image / scan_route | report accepted |
| pick_object | gripper confirms object held (possession) |
| place_object | gripper confirms release at the location (goal) |

Cancel before the final step yields `canceled` with no effect. After the commit point the
robot reports the actual result, and it is applied honestly.

## Deferred (per spec section 3)

OR-Tools allocation (scheduled follow-up behind `Allocator`), physics, SLAM, perception,
grasp planning, persistent crash recovery, large benchmarks. Process restart ends the
active fake run; reset before another run.
