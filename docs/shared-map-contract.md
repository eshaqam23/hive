# Shared perception map contract — `hive.shared-map.v0`

The Map tab and Chat's mini map draw one shared map of the room. Many cars feed it. Nothing builds that map yet.

- Today `GET /api/exec/map` (`backend/hive_exec/api.py`) answers:

  ```json
  { "available": false, "reason": "shared perception not built yet", "schema": "hive.shared-map.v0" }
  ```

- The UI then shows its empty state: "Shared perception map not built yet".

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
