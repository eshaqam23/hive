# Instance agent contract

What a robot's instance agent must do to take work from the HIVE Executive. The Executive plans,
validates, gets approval and schedules. The agent on each robot runs one skill at a time and reports
what really happened. Everything here is what the code does today on `feature/agent-integration`
(A8: `map_area`, per-skill timeouts, the bridge margin and the optional fleet report); where the fake
fleet or hive-agent falls short of the contract, the gap is named.

Sources: `ros/hive_interfaces/action/ExecuteSkill.action`, `backend/hive_exec/contracts.py`
(`TaskEnvelope`), `backend/hive_exec/catalog.py`, `backend/hive_exec/fleet_report.py`, `bridge/server.mjs`,
`ros/hive_fake_robots/hive_fake_robots/fake_fleet.py`, `backend/hive_exec/sim/fake_world.py`.

## Transport path

```
Executive (FastAPI :8788) -- ws --> Synapse bridge (:8790, @corebrum/core) -- rosbridge (:9090) --> ROS 2 action server
```

The bridge only moves envelopes, events and cancels. It decides nothing about the plan.

## Action per robot

Each robot serves one ROS 2 action:

| robot_id     | action                      | type                                |
|--------------|-----------------------------|-------------------------------------|
| `camera_01`  | `/camera_01/execute_skill`  | `hive_interfaces/action/ExecuteSkill` |
| `lidar_01`   | `/lidar_01/execute_skill`   | `hive_interfaces/action/ExecuteSkill` |
| `gripper_01` | `/gripper_01/execute_skill` | `hive_interfaces/action/ExecuteSkill` |

The pattern is `/<robot_id>/execute_skill`. The action name comes from `RobotDescriptor.ros_action`
in `catalog.py`.

## Goal

```
string run_id
string attempt_id
string robot_id
string skill
string envelope_json
```

`run_id`, `attempt_id`, `robot_id` and `skill` are copies for routing and logging. The envelope in
`envelope_json` is the source of truth. An agent must reject a goal whose envelope disagrees with
these fields or with the robot it runs on.

## Task envelope (hive.task-envelope.v1)

`envelope_json` holds one JSON object. The bridge builds it with `JSON.stringify` of the envelope
the Executive sent, so the text is compact and an integral float such as `timeout_s` arrives as
`60`, not `60.0`.

| field            | type    | meaning |
|------------------|---------|---------|
| `schema_version` | string  | always `"hive.task-envelope.v1"` |
| `run_id`         | string  | the run the fleet was last reset to; anything else is stale |
| `mission_id`     | string  | the mission this task belongs to |
| `revision`       | integer | plan revision (1 = initial plan, later = recovery or replan) |
| `task_id`        | string  | task inside that revision, e.g. `t2` |
| `attempt_id`     | string  | unique per dispatch: `<revision>.<task_id>.a<n>-<4 hex>`, e.g. `1.t2.a1-2d21` |
| `robot_id`       | string  | the robot that must run it |
| `skill`          | string  | one of `navigate_to`, `inspect_area`, `capture_image`, `scan_route`, `pick_object`, `place_object`, `map_area` |
| `params`         | object  | skill parameters, see [Skills per robot](#skills-per-robot) |
| `timeout_s`      | number  | > 0 and ≤ 600; the skill's catalog timeout, see [Timeouts](#timeouts-and-uncertain) |

No other fields are allowed (`additionalProperties: false`).

### Schema

The JSON Schema is generated from the pydantic `TaskEnvelope` and checked into the repo:
`backend/hive_exec/schemas/task_envelope.v1.json`. A test fails if the file and the model drift
apart (`test_saved_task_envelope_schema_matches_model`).

### Example from a real run

Captured from b_e2e over the real Synapse bridge, rosbridge and the fake fleet, run `run-25b81714`
(story "stop one task; others keep running"). File
`runs/run-25b81714/agent_commands/camera_01/t1-r1-a1.json`, 278 bytes:

```json
{"schema_version":"hive.task-envelope.v1","run_id":"run-25b81714","mission_id":"m8-731b8a","revision":1,"task_id":"t1","attempt_id":"1.t1.a1-4c6c","robot_id":"camera_01","skill":"inspect_area","params":{"area_id":"work_area","object_query":{"label":"red_block"}},"timeout_s":60}
```

The second command of that run, for the LiDAR car, is
`runs/run-25b81714/agent_commands/lidar_01/t2-r1-a1.json`:

```json
{"schema_version":"hive.task-envelope.v1","run_id":"run-25b81714","mission_id":"m8-731b8a","revision":1,"task_id":"t2","attempt_id":"1.t2.a1-2d21","robot_id":"lidar_01","skill":"scan_route","params":{"route_id":"pickup_corridor"},"timeout_s":60}
```

### Agent command files

For every dispatched attempt the Executive writes the exact `envelope_json` text to
`runs/<run_id>/agent_commands/<robot_id>/<task_id>-r<revision>-a<attempt>.json` (gitignored). The
bytes equal what the bridge sends, so a robot log can be diffed against the file. `/api/exec/state`
shows the same command per task as `agent_command`: a one-line form such as
`inspect_area(area_id="work_area", object_query="red_block")`, the full envelope, the attempt id
and the file path.

## Skills per robot

From `catalog.py`. Durations are synthetic fake-fleet values, not measurements.

| robot_id     | preset         | skills |
|--------------|----------------|--------|
| `camera_01`  | `cameraRcCar`  | `inspect_area`, `capture_image`, `navigate_to` |
| `lidar_01`   | `lidarRcCar`   | `scan_route`, `navigate_to`, `map_area` |
| `gripper_01` | `gripperRcCar` | `pick_object`, `place_object`, `navigate_to` |

| skill           | params | effect is committed when | duration (s) |
|-----------------|--------|--------------------------|--------------|
| `navigate_to`   | `location_id`, `route_id` (string or null) | robot reports arrival at `location_id` | 4 |
| `inspect_area`  | `area_id`, `object_query` (`{"label": ...}`) | observation report accepted | 3 |
| `capture_image` | `area_id` | image report accepted | 2 |
| `scan_route`    | `route_id` | scan report accepted | 3 |
| `pick_object`   | `object_id` | gripper confirms object held | 3 |
| `place_object`  | `object_id`, `location_id` | gripper confirms release at `location_id` | 2 |
| `map_area`      | `area_id` (`"arena"`, the only map area) | robot reports a saved map with coverage ≥ target | 6 |

Moving between `pickup_zone` and `dropoff_zone` needs `route_id = "pickup_corridor"` and a fresh
observation that the corridor is clear; the validator checks that before a plan can be approved.

## Feedback

```
string run_id
string attempt_id
string phase
float32 progress
```

| phase      | when | progress |
|------------|------|----------|
| `accepted` | first feedback after the goal is accepted; send exactly once | `0.0` |
| `running`  | while working, as often as useful | `0.0`–`1.0` |

The bridge turns the first `accepted` into an `accepted` event and each `running` into a `running`
event with `progress`. Feedback for another `attempt_id`, or after a terminal result, is dropped.

## Result

```
string run_id
string attempt_id
string status
string result_json
string error_code
string error_message
string observations_json
float32 energy_used_pct
```

### Terminal statuses

| status      | meaning | ROS goal state | what the Executive does |
|-------------|---------|----------------|-------------------------|
| `succeeded` | the skill finished and its effect is committed | succeed (4) | applies `result_json` and `observations_json` to its world model, moves on |
| `failed`    | the skill did not finish, and the agent knows where things stand | abort (6) | stops the other active tasks and proposes one bounded recovery for approval, or blocks if recovery was already used |
| `uncertain` | the agent cannot tell whether the effect happened | abort (6) | blocks the mission with no retry; a later credible result updates the world model but never advances the plan |
| `canceled`  | stopped on request before the effect was committed | canceled (5) | after an operator task stop, proposes recovery while other tasks keep running; any other cancel is handled as `failed` |
| `rejected`  | refused at goal time; nothing ran | goal rejected | same as `failed` |

The bridge cross-checks the HIVE status against the ROS goal state. A mismatch, for example
`succeeded` from an aborted goal, or any status not in the table, is reported as `uncertain`.

### error_code and error_message

Empty strings when there is no error. Otherwise a short `snake_case` code and one human sentence,
e.g. `route_blocked` / `pickup_corridor blocked`, `motor_stall` / `drive motor stalled; robot
stopped in place`, `release_unconfirmed` / `gripper opened but release not confirmed`.

### result_json and observations_json

Both are JSON text: `result_json` an object (`{}` when empty), `observations_json` an array (`[]`
when empty). Unparseable text is treated as empty.

`result_json` holds what the skill produced, e.g. `{"route_id": "pickup_corridor", "status": "clear"}`
for `scan_route`, `{"found": ["red_block_1"]}` for `inspect_area`, `{"object_id": ..., "held": true,
"location_id": ...}` for `pick_object`, `{"area_id": "arena", "coverage": 0.95, "map_ref": {...},
"observation": {...}}` for `map_area`. A canceled attempt returns `{"effect_committed": false}`.

`observations_json` holds facts about the world, each with `kind` and `entity_id`:

| kind | from | other fields |
|------|------|--------------|
| `object_seen`   | `inspect_area`, `place_object` | `area_id` or `location_id`, `label`, `attrs` (`profile`, `confidence`, `placed_by`) |
| `object_absent` | `inspect_area` | `area_id`, `label` |
| `route_clear`, `route_blocked` | `scan_route` | `route_id` |
| `image`         | `capture_image` | `area_id`, `attrs.image_ref` |
| `area_mapped`   | `map_area` | `entity_id` `map:<area_id>`, `area_id`, `attrs.coverage` (0..1), `attrs.map_ref` `{id, path, sha256}` |

### map_area and area_mapped (A8)

`map_area(area_id)` maps the whole area and saves the map. Its evidence is one `area_mapped`
observation. An `area_mapped` entry in `observations_json` wins; otherwise the Executive reads it from
`result_json.observation`, where hive-agent and the fake fleet both put it, on success and on failure.

- **The map record.** The Executive records a map only from a `succeeded` `map_area` whose
  `area_mapped` is for the goal's area, has `attrs.coverage` a number in [0, 1], and has a `map_ref`
  with string `id`, `path` and `sha256`. An `area_mapped` goal is satisfied by such a record.
- **`map_ref`.** `path` is relative to the fleet's root (the hive-agent repo root, `HIVE_AGENT_ROOT`
  on the Executive's side; the Hive repo root for the fake fleets) and must stay under its `runs/`;
  `sha256` is the image file's hash. The Executive never reads the file when it records the map: only
  `GET /api/exec/map` reads it, path- and hash-checked (`docs/shared-map-contract.md`).
- **Short coverage.** Coverage below the target (0.95) is `failed` with `COVERAGE_INCOMPLETE`, carrying
  the partial map's `map_ref`. The Executive keeps no map record from a failure.

### object_held is Executive-only

The `object_held` goal ("pick up the red block" with no drop-off) adds no skill and nothing an agent
sees: the plan ends at `pick_object`, and the goal is satisfied by the possession record from a
succeeded `pick_object` with `held: true`.
The Executive takes effects only from accepted results. Feedback never changes the world model.

## Cancel

The Executive cancels one attempt by goal id. The bridge sends every goal with goal id
`hive:<attempt_id>` and cancels with `cancel_action_goal(/<robot_id>/execute_skill, hive:<attempt_id>)`.
That targeting needs the HIVE patch to @corebrum/core
(`bridge/patches/corebrum-core-rosbridge-actions.v1.patch`); unpatched core ignores the goal id.

The agent must:

1. accept the cancel request;
2. stop before the effect's commit point if it still can, and return `canceled` with
   `result_json = {"effect_committed": false}`;
3. if the effect was already committed, return the real `succeeded` result instead. The Executive
   keeps it and logs that the stop landed too late.

Operator stops use `POST /api/exec/tasks/{task_id}/cancel` (one task) or `POST /api/exec/cancel`
(the mission). The task shows `canceling` until the robot's `canceled` result arrives. Neither call
is gated on planner or transport health.

## Idempotency

Contract: the same `attempt_id` always gives the same recorded result and is never executed twice.
A re-sent goal for an attempt the agent already ran must return that attempt's recorded result, not
run the skill again.

The Executive never re-sends an `attempt_id`. A retry or recovery is a new attempt with a new id.
The bridge ignores a second submit of an `attempt_id` it already holds ("duplicate submission
ignored").

Gap today, kept on purpose: the fake fleet and hive-agent both reject a repeated `attempt_id` with
`duplicate_attempt` instead of returning the recorded result, and the fake fleet's record is cleared by
every coordinated reset. The Executive never re-sends an `attempt_id`, so it never meets the gap.

## Timeouts and "uncertain"

`timeout_s` is the agent's budget for the skill. Since A8 it is per skill (`catalog.py`
`SkillDef.timeout_s`; the envelope allows at most 600):

| skill | `timeout_s` |
|-------|-------------|
| `navigate_to` | 240 |
| `pick_object` | 300 |
| `place_object` | 120 |
| `map_area` | 480 |
| `inspect_area`, `capture_image`, `scan_route` | 60 |

Each must cover the slowest run of that skill on an agent; hive-agent's A9 gate checks them against
the agents' own bounds. When the budget runs out the agent must stop and send a terminal result:
`failed` if it knows the effect did not happen, `uncertain` if it cannot tell. This doc used to name the
code `timeout`; hive-agent reports `COMMAND_EXPIRED`. The Executive treats both as an ordinary failure.

**The bridge's clock.** `bridge/server.mjs` waits `timeout_s + HIVE_BRIDGE_TIMEOUT_MARGIN_S` (default
30) seconds for the result. That needs the bridge core built with
`bridge/patches/corebrum-core-rosbridge-actions.v2.patch`, which passes the per-goal wait through: an
unpatched or v1-only core cuts every goal at @corebrum/core's fixed 120 s (`npm run hive -- --agents`
refuses such a core). When the wait runs out, the bridge first sends the targeted cancel for that
attempt (its effect is unconfirmed), then reports `uncertain` with `transport_lost`, because the robot
may still be working. A rosbridge link that drops is reported the same way. A transport failure is never
reported as success.

The fake fleet ignores `timeout_s`; its `hang_*` faults run until the bridge's wait ends them.

`uncertain` means the Executive does not know whether the effect happened. It blocks the mission
and does not retry, since retrying a pick or place that might have happened could do it twice.

## Rejections

A goal is rejected at goal time, before anything moves. The fake fleet rejects for:

| error_code | cause |
|------------|-------|
| `bad_envelope` | `envelope_json` is not JSON |
| `stale_run` | envelope or goal `run_id` differs from the current run |
| `wrong_robot` | envelope `robot_id` is not this robot |
| `duplicate_attempt` | `attempt_id` already submitted (see [Idempotency](#idempotency)) |
| `robot_busy` | this robot is running another attempt |
| world prechecks | `unsupported_skill`, `robot_unavailable`, `battery_low`, `unreachable`, `no_route`, `not_observable`, `already_holding`, `not_holding`, `wrong_location` |

A ROS goal rejection carries no reason, so the bridge asks `/hive/fleet_control` for
`{"command": "rejection", "attempt_id": ...}`. An agent without that service gets the generic code
`goal_rejected`.

## Fleet report (optional, A8)

A `/hive/fleet_control` `status` reply is `{"robots": {<robot_id>: {...}}, ...}`. An agent fleet may add
the fields below; every one is optional. The fake fleet sends none of them, and nothing changes for it.
`backend/hive_exec/fleet_report.py` parses them.

```jsonc
{
  "fleet": {                                   // top level
    "schema": "hive.fleet-report.v1",
    "kind": "hive_agent",
    "profile": "laptop",                       // or "desktop"
    "evidence": "emulated",                    // or "real"
    "agent_commit": "0123abc"
  },
  "robots": {
    "lidar_01": {
      "location_id": null,                     // null = unknown
      // "battery_pct" absent = unknown
      "capabilities": {
        "schema": "hive.capabilities.v1",
        "skills": {
          "map_area": {"ready": true, "code": null, "reason": null},
          "scan_route": {"ready": false, "code": "NOT_TAUGHT", "reason": "no route taught"}
        },
        "areas": [],                           // inspect_area / capture_image
        "routes": ["pickup_corridor"],         // scan_route
        "places": ["lidar_post"],              // navigate_to
        "map_areas": ["arena"],                // map_area
        "place_locations": []                  // place_object
      }
    }
  }
}
```

What the Executive does with it:

- **Labels.** `simulated` is false only for `kind` `hive_agent` with `evidence` `real`.
  `GET /api/exec/health` and `/api/exec/state` carry `simulated` and the `fleet` block. The UI tags
  robots SIM for the fake fleets, EMULATED for an agent fleet with `evidence` `emulated`, and nothing for
  `real`; the simulator drawer shows only for the fake fleets.
- **Resets.** An agent fleet always gets `world` `"fixed"` and `seed` `null`. Scenario `known` (a
  simulated prior survey) and simulator faults are refused with 409 and never reach it.
- **Battery.** An agent fleet that leaves out `battery_pct` keeps the battery unknown, never a default.
  The validator gives the warning `battery_unmeasured` for that robot (not an error), and the context and
  UI read "battery unknown".
- **Location.** An agent fleet's `location_id` replaces the Executive's belief at every refresh; `null`,
  absent or not a catalog location is unknown. Travel from an unknown location is the validator error
  `location_unknown`.
- **Narrowing.** With `capabilities`, a task is ineligible for the robot when its skill is absent or not
  `ready` (the finding carries the agent's `code` and `reason`), or when its area, route, place, map area
  or place location is not listed. The planner context lists only the robot's ready skills and listed ids,
  as bounded text; agent codes and reasons never reach the prompt. A malformed `capabilities` block offers
  nothing. A report only removes what the catalog offers: unknown skills and ids are dropped, and the
  catalog stays the vocabulary.

hive-agent does not send a fleet report yet; its A9 adds it.

## Robot ids and frontend presets

| robot_id     | display name   | frontend preset |
|--------------|----------------|-----------------|
| `camera_01`  | Camera RC Car  | `cameraRcCar` |
| `lidar_01`   | LiDAR RC Car   | `lidarRcCar` |
| `gripper_01` | Gripper RC Car | `gripperRcCar` |

## What the fake fleet does today

`ros/run_fake_fleet.sh` starts one ROS 2 node, `hive_fake_fleet`, that serves all three actions,
plus rosbridge on `127.0.0.1:9090`. It always uses `ROS_DOMAIN_ID=77` with
`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, never a real car's domain.

- **One shared world.** Skill results come from `backend/hive_exec/sim/fake_world.py`, the same
  module the in-process fake gateway uses, so both transports give the same results.
- **Timing.** Each skill takes its catalog duration times `time_scale` (`HIVE_SIM_TIME_SCALE`),
  split into 4 steps: `accepted` at 0, then `running` at 0.25, 0.5 and 0.75, then the result. The
  effect is applied only at the last step, so a cancel before then commits nothing.
- **`/hive/fleet_control`** (`hive_interfaces/srv/FleetControl`) takes `status`, `reset` (new
  `run_id` and fault list; running goals end `canceled` with code `reset`), `inject_fault`,
  `rejection` and `truth`.
- **Faults** for testing: `route_blocked`, `block_missing`, `two_red_blocks`,
  `gripper_unavailable`, `nav_fault_after_pickup`, `gripper_fault_after_pickup`, `uncertain_place`,
  `hang_navigate`, `hang_scan`, `low_battery_gripper`, `map_incomplete` (A8: `map_area` stops at 60 %
  coverage, `COVERAGE_INCOMPLETE` with the partial map).
- **`map_area`** (A8) writes a small synthetic map pair, labelled synthetic, under
  `runs/sim_maps/<run_id>/` in the Hive repo and reports its coverage (0.95 when complete) with its `map_ref`.
- **No fleet report**: its robots are tagged SIM.
- **Cancel** is always accepted and returns `canceled` with `{"effect_committed": false}`.
- **Not done:** `timeout_s` is not enforced (the bridge's wait is the clock), and a repeated
  `attempt_id` is rejected rather than answered with its recorded result.

All fake-fleet data is simulated and shown with a SIM tag in the UI.

## Health

`GET /api/exec/health` reports each dependency on its own: the gateway (`connected`,
`verified_round_trip`), each planner (for the LLM: whether the executive role is bound to a
provider, and the last probe), and for Synapse the `bridge` and `rosbridge` links. Starting a mission
or approving a revision returns 503 naming what is down (`gateway` or `planner:<kind>`). Cancel and
stop never wait on health.
