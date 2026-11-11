# Delivery B: AgenticROS / Corebrum Synapse path

```
HIVE Executive (Windows or Ubuntu, Python)            planning, validation, approval, scheduling
  └─ SynapseGateway (backend/hive_exec/gateway/synapse.py)
       └─ WebSocket ws://127.0.0.1:8790
            └─ bridge/server.mjs  (Node, imports the ACTUAL @corebrum/core rosbridge transport)
                 └─ rosbridge_server ws://127.0.0.1:9090
                      └─ hive_fake_robots/fake_fleet  (3 ROS 2 action servers, one shared fake world)
                           /camera_01/execute_skill  /lidar_01/execute_skill  /gripper_01/execute_skill
                           /hive/fleet_control (status | reset | inject_fault | rejection)
```

`ExecuteSkill` and `FleetControl` are custom HIVE contracts, not built-in Synapse skills. The
goal carries the `hive.task-envelope.v1` JSON. The result carries the terminal HIVE status,
result, error and observations, and feedback reports the `accepted`/`running` phases. HIVE still
validates mission semantics; ROS actions only provide feedback, results and cancellation.

## Pinned dependency

- Corebrum Synapse `6f7b7840e2b3345f66412a0fb800f294cb94667c` (2026-11-10, "Point Synapse at Zenoh
  1.10.2 and expose cortex fleet commands to agents"). This is the commit the earlier research
  inspected; it was re-verified at runtime here.
- `@corebrum/core` 0.8.16 is built from `packages/core` at that commit by `bridge/scripts/build-core.sh`,
  with dependency versions taken exactly from Synapse's `pnpm-lock.yaml`. The optional `rclnodejs`
  and `node-datachannel` are not installed; they are not needed by the rosbridge transport. tsc
  reports one type error in the optional WebRTC loader for that reason, and the emitted JS is unaffected.
- Building the full Synapse workspace with pnpm fails on this machine because `rclnodejs` compiles
  against a sourced ROS install. That failure is unrelated to the core transport.

## Compatibility check (spec 10 "early check")

`node bridge/check.mjs --json <out>` runs against the actual core transport and the ROS 2 fake servers.

| Check | Unpatched core | HIVE patch v1 |
|---|---|---|
| success | PASS | PASS |
| feedback reaches client | **FAIL** (0 messages: `send_action_goal` never sets `feedback: true`) | PASS (4 messages) |
| terminal GoalStatus exposed | **FAIL** (`ActionResult` drops rosbridge `status`) | PASS (4/5/6) |
| rejection distinguishable | PASS (`result:false`, "Action goal was rejected") | PASS |
| abort | PASS (HIVE status in result values) | PASS |
| targeted cancel | **FAIL** (cancel sent under a new id; rosbridge cancels by goal id, so the goal kept running) | PASS (ROS status 5) |
| cancel isolation (other robot unaffected) | PASS | PASS |
| concurrent routing to two robots | PASS | PASS |

Evidence: `docs/evidence/synapse-check-unpatched.json`, `docs/evidence/synapse-check-patched-v1.json`.

**Patch** `bridge/patches/corebrum-core-rosbridge-actions.v1.patch` is narrow and versioned and touches
only the rosbridge action client and types. It (1) requests feedback when `onFeedback` is given,
(2) lets callers supply the rosbridge goal id and cancels by that id, and (3) passes the GoalStatus
through. Without it, cancellation cannot be confirmed through Synapse. The bridge cross-checks the
HIVE status against the ROS status and reports `uncertain` on any mismatch.

## End-to-end (Executive through Synapse)

`backend/scripts/b_e2e.py` runs the stories through `SynapseGateway`. Evidence is in
`docs/evidence/b-e2e.json`. The planner for this run was **Baseline (labeled)** because no Qwen
endpoint was available, so this verifies the transport, not live Qwen.

| Story | Result |
|---|---|
| Unknown object/route: sensing stage, then transport after fresh approval | completed |
| Known-object delivery | completed |
| Camera-only (other robots idle) | completed |
| Ambiguous request: clarification, no dispatch | clarification |
| Navigation fault after pickup: possession kept, approved recovery | completed |
| Gripper fault after pickup: honest blocked outcome | blocked |
| Targeted cancellation, shown as canceling until ROS confirms | canceled |
| Uncertain place: blocked, no retry | blocked |

Also observed:
- Killing the bridge mid-task blocks the mission ("outcome is uncertain"). The attempt stays
  `accepted` (never assumed done) until a coordinated reset.
- On startup the Executive performs a coordinated reset, which stops goals a previous process left running.
- A submit whose reply is lost is marked `uncertain` (not failed). Found during this work: the
  first run deadlocked on exactly this case and falsely reported a failed pick that had in fact
  happened. That is fixed, and late results for uncertain attempts are reconciled without advancing the plan.

## Run it (Ubuntu 24.04 / WSL `Ubuntu-24.04`)

```bash
sudo apt install ros-jazzy-rosbridge-suite build-essential python3-colcon-common-extensions
# Node >= 20 (NodeSource or nvm)
bash ros/build.sh                                  # colcon build into ~/hive_ros_ws
HIVE_CORE_PATCH=1 bash bridge/scripts/build-core.sh  # @corebrum/core at the pinned commit + patch v1
HIVE_SIM_TIME_SCALE=0.25 bash ros/run_fake_fleet.sh  # terminal 1: action servers + rosbridge :9090
node bridge/check.mjs                              # compatibility check
node bridge/server.mjs                             # terminal 2: bridge :8790
```

Then on Windows (WSL2 forwards localhost) or Ubuntu:

```bash
cd backend && .venv/Scripts/python scripts/b_e2e.py   # stories through Synapse
HIVE_GATEWAY=synapse npm run hive                     # UI; transport badge turns green after a round trip
```

When run from Git Bash, prefix `wsl` commands with `MSYS_NO_PATHCONV=1`, otherwise `/mnt/c/...` paths get rewritten.

## Not done / limits

- The rclpy fallback adapter was not built because the Synapse path works. Nothing here relies on a fallback.
- Status reconciliation after a disconnect is "block until coordinated reset". There is no per-goal
  status query through rosbridge.
- Only fake servers have been exercised. Hardware needs agreed units, frames, IDs, skill versions,
  result evidence and cancellation semantics, verified one robot and one skill at a time.
