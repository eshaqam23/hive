# Shared perception map contract — `hive.shared-map.v0`

The Map tab and Chat's mini map draw one shared map of the room. Many cars will feed it; fused shared perception is not built yet.

- Since A8, `GET /api/exec/map` (`backend/hive_exec/api.py`, `backend/hive_exec/shared_map.py`) serves the run's latest **map_area** map: its occupancy grid, every catalog robot with `pose: null`, no objects or regions, and a `provenance` block. See [Served from a map_area record](#served-from-a-map_area-record-a8).
- With no map record in the run (a reset starts a new run) it answers:

  ```json
  { "available": false, "reason": "no map reported in this run", "schema": "hive.shared-map.v0" }
  ```

- The UI then shows its empty state with that reason.

This document is the contract the UI is built against, so a real map can plug in without UI changes. `src/map/mapContract.js` says the same thing as JSDoc and holds the parser (`parseSharedMap`) and the cell classifier the UI uses.

## Inputs it will fuse

| Source | Topic / data | What it contributes |
|---|---|---|
| LiDAR car (`lidar_01`) | `/scan` (sensor_msgs/LaserScan) | occupancy, its own pose by scan matching, the "LiDAR scans" layer |
| Camera car (`camera_01`) | camera detections | objects with position covariance, its own pose from odometry |
| Gripper car (`gripper_01`) | odometry | its own pose |

## Conventions

- **Frame:**
  - Everything is in the map frame (`"map"`).
  - Units are metres and radians.
  - x points right and y points up.
  - `theta` is counter-clockwise from +x.
- **Ages:** `age_s` is the number of seconds since that item was last refreshed. It is `null` when the item was never seen.
- **Revision and epoch:**
  - `revision` goes up on every update.
  - `epoch` changes only when a new map is started.
  - Two revisions can be compared only within one epoch. The UI fits its view again when the epoch changes.

## Shape when available

```jsonc
{
  "available": true,
  "schema": "hive.shared-map.v0",
  "occupancy": {                       // or null before the first grid
    "resolution": 0.05,                // metres per cell, > 0
    "origin": { "x": -2.5, "y": -1.0, "theta": 0 },  // outer corner of cell (0, 0)
    "width": 200,                      // cells along the grid's x axis
    "height": 140,                     // cells along the grid's y axis
    "data": [-1, 0, 0, 100, ...],      // width*height, row-major, row 0 at the origin
    "revision": 42,
    "epoch": "2026-11-19T10:00:00Z"    // string or number
  },
  "robots": [
    { "robot_id": "lidar_01", "pose": { "x": 3.2, "y": 2.1, "theta": 0.6 }, "source": "scan_match", "age_s": 0.4 },
    { "robot_id": "gripper_01", "pose": null, "source": null, "age_s": null }
  ],
  "objects": [
    {
      "id": "obj-7", "class": "red_block",
      "pose": { "x": 6.1, "y": 3.4, "theta": 0 },
      "covariance": [[0.010, 0.002], [0.002, 0.020]],   // position, m²
      "sources": ["camera_01"],
      "age_s": 1.8
    }
  ],
  "regions": [
    { "id": "dock", "label": "Dock", "polygon": [[0.5, 0.5], [2.0, 0.5], [2.0, 1.5], [0.5, 1.5]] }  // not closed
  ],
  "scans": [                           // optional, see below
    { "robot_id": "lidar_01", "origin": { "x": 3.2, "y": 2.1, "theta": 0.6 }, "points": [[4.0, 2.3], ...], "age_s": 0.1 }
  ],
  "known_fraction": 0.62               // 0..1, cells whose value is not -1
}
```

### Occupancy values

The values follow `nav_msgs/OccupancyGrid`, with ROS map_server's default thresholds:

| Value | Drawn as |
|---|---|
| `-1` | unknown |
| `0..25` | free |
| `65..100` | occupied |
| `26..64` | unknown (the map is not sure either way) |

A **frontier** cell is a free cell with an unknown 4-neighbour. The UI works frontiers out itself, so the server does not send them.

### Robots

`robots` lists every robot the Executive knows, including robots with no pose yet. `pose: null` is drawn as "pose: unknown". `source` names what placed the robot, for example `odom`, `scan_match` or `camera`.

The UI joins these robots to live-data's saved robots by preset:

| Preset | Robot id |
|---|---|
| `cameraRcCar` | `camera_01` |
| `lidarRcCar` | `lidar_01` |
| `gripperRcCar` | `gripper_01` |

### `scans` — an addition

The build prompt names these fields:

- `occupancy`
- `robots`
- `objects`
- `regions`
- `known_fraction`

The Map tab also has a **LiDAR scans** layer, so the contract adds an optional `scans` list for it. It holds the latest hit points per robot, already in the map frame. A map without `scans` is valid and shows an empty layer.

## Served from a map_area record (A8)

The Executive records a map only from the `area_mapped` observation of a succeeded `map_area` (`world.MapRecord`). Its `map_ref` is `{id, path, sha256}`: `path` is relative to the fleet's root and `sha256` is the image file's hash. The route reads that file and serves it only when all of this holds; otherwise it answers not-available with the reason:

- **Root.** `HIVE_AGENT_ROOT` (the hive-agent repo root) for an agent fleet's record; the Hive repo root for the fake fleets' records (`fake_world.sim_map_root`; tests point it elsewhere with `HIVE_SIM_MAP_ROOT`). An agent record without `HIVE_AGENT_ROOT` is not served.
- **Path.** Relative, no `..`, first part `runs`, and its realpath inside `<root>/runs/`: no absolute path, no symlink out. The YAML beside it (same stem, `.yaml`) passes the same check.
- **Hash.** The image's sha256 equals `map_ref.sha256`.
- **Image.** Binary PGM `P5`, maxval 255, `#` comments allowed in the header, exactly `width × height` bytes after it, at most `MAP_MAX_CELLS` (4,000,000) cells.
- **YAML** (what map_server / map_saver write): `image` names that same file; `mode` is `trinary` (the default when absent); `negate` 0 or 1; `resolution` > 0; `origin` `[x, y, yaw]`; `0 ≤ free_thresh < occupied_thresh ≤ 1`.

Cells follow ROS map_server's trinary rule (nav2 `map_io`): `shade = value / 255`, `occ = shade` if `negate` else `1 − shade`; `occ > occupied_thresh` is `100`, `occ < free_thresh` is `0`, anything else `-1`. PGM row 0 is the top of the image and the contract's row 0 is at the origin, so the rows are flipped. With the default thresholds, 254 is free, 205 unknown and 0 occupied.

Other fields:

- `origin` is the YAML's `[x, y, yaw]` as `{x, y, theta}`.
- `revision` is the record's `seq` (1, 2, ... in the run) and `epoch` is the record's `id`, so every new map refits the view.
- `known_fraction` is worked out from the served grid.
- `robots` is every catalog robot with `pose: null`, `source: null`, `age_s: null`; `objects` and `regions` are `[]`.

### `provenance` — an addition

```jsonc
"provenance": {
  "source": "map_area",
  "robot_id": "lidar_01",
  "attempt_id": "run-1a2b:t1:1",   // or null
  "coverage": 0.95,                // what the skill reported, 0..1
  "evidence": "simulated"          // "simulated" | "emulated" | "real"
}
```

`evidence` comes from the fleet that reported the map: `simulated` for the fake fleets, and the fleet report's `evidence` for a hive-agent fleet (`emulated` or `real`; anything else is `simulated`). It is optional in the contract: a fused map may leave it out.

The Map tab and Chat's mini map tag a served map the way the Fleet tab tags robots: **SIM** for `simulated`, **EMULATED** for `emulated`, no tag for `real`. A map with no `provenance`, or an `evidence` the UI does not know, is tagged **SIM** (fail-safe). While the Demo data toggle is on, only **DEMO** is shown. `mapTag` in `src/map/mapContract.js` decides the tag.

## Shape when not available

```json
{ "available": false, "reason": "…", "schema": "hive.shared-map.v0" }
```

`schema` is optional here. The UI shows `reason` under the empty state.

## What the UI rejects

`parseSharedMap` turns each of these into the not-available state. Each gets a reason, so a bad map is shown as missing rather than drawn wrong:

- No reply, or `available` is not `true`.
- `schema` is anything other than `hive.shared-map.v0`.
- `resolution` is not greater than 0.
- `width` or `height` is not a whole number of 0 or more.
- `data` is not an array, or its length is not `width × height`.

Missing lists become `[]` and a missing `origin` becomes `{0, 0, 0}`. A missing `known_fraction` is worked out from the grid.

## Demo fixture

`src/map/demoMap.js` is a hand-made map in this shape. It renders only through the Map tab's **Demo data** toggle. The toggle is shown in dev builds only and is off by default. The map is watermarked **DEMO**, so it can never pass for real perception.
