// The shared perception map the Executive will serve at GET /api/exec/map, schema hive.shared-map.v0.
// Nothing builds it yet: today the route answers { available: false }. This file is the contract the
// map UI is drawn against, so the real map just plugs in. docs/shared-map-contract.md says the same.
//
// Frames and units: everything is in the map frame ("map"), metres and radians, x right / y up,
// theta counter-clockwise from +x. Ages are seconds since the data was last refreshed.

export const SCHEMA = "hive.shared-map.v0";

/**
 * A pose in the map frame.
 * @typedef {Object} Pose2D
 * @property {number} x      metres
 * @property {number} y      metres
 * @property {number} theta  radians, counter-clockwise from +x
 */

/**
 * The fused occupancy grid. Same conventions as ROS nav_msgs/OccupancyGrid.
 * @typedef {Object} OccupancyGrid
 * @property {number} resolution  metres per cell, > 0
 * @property {Pose2D} origin      pose of the outer corner of cell (0, 0); the grid rotates about it
 * @property {number} width       cells along the grid's x axis
 * @property {number} height      cells along the grid's y axis
 * @property {number[]} data      width * height values, row-major, row 0 at the origin:
 *                                -1 unknown, 0..100 occupancy probability (free <= 25, occupied >= 65)
 * @property {number} revision    increases on every update of this map
 * @property {string|number} epoch changes when the map is started again (a new map, not an update);
 *                                revisions are only comparable within one epoch
 */

/**
 * A robot as the map sees it. pose is null until some source has placed it.
 * @typedef {Object} MapRobot
 * @property {string} robot_id     the Executive's robot id (camera_01, lidar_01, gripper_01)
 * @property {Pose2D|null} pose
 * @property {string|null} source  what placed it: "odom", "scan_match", "camera", ...
 * @property {number|null} age_s
 */

/**
 * An object found by perception.
 * @typedef {Object} MapObject
 * @property {string} id
 * @property {string} class          detector class, e.g. "red_block"
 * @property {Pose2D} pose
 * @property {number[][]} covariance position covariance [[xx, xy], [xy, yy]], m^2
 * @property {string[]} sources      robot ids whose sensors saw it
 * @property {number} age_s
 */

/**
 * A named area of the map.
 * @typedef {Object} MapRegion
 * @property {string} id
 * @property {string} label
 * @property {number[][]} polygon  [[x, y], ...] in metres, map frame, not closed (last != first)
 */

/**
 * Optional: the latest LiDAR scan per robot, already in the map frame, for the "LiDAR scans" layer.
 * An addition to the five fields the build prompt names; a map without it just has an empty layer.
 * @typedef {Object} MapScan
 * @property {string} robot_id
 * @property {Pose2D} origin       sensor pose when the scan was taken
 * @property {number[][]} points   [[x, y], ...] hit points, metres, map frame
 * @property {number} age_s
 */

/**
 * GET /api/exec/map once shared perception exists.
 * @typedef {Object} SharedMap
 * @property {true} available
 * @property {"hive.shared-map.v0"} schema
 * @property {OccupancyGrid|null} occupancy
 * @property {MapRobot[]} robots
 * @property {MapObject[]} objects
 * @property {MapRegion[]} regions
 * @property {MapScan[]} [scans]
 * @property {number} known_fraction  0..1, share of grid cells whose value is not -1
 */

/**
 * GET /api/exec/map today, and whenever the map cannot be served.
 * @typedef {Object} SharedMapUnavailable
 * @property {false} available
 * @property {string} reason
 * @property {string} [schema]
 */

/* ROS map_server's default thresholds, as occupancy values */
export const FREE_MAX = 25;
export const OCCUPIED_MIN = 65;

export const CELL = { unknown: 0, free: 1, occupied: 2, frontier: 3 };

/* A value between the thresholds is drawn as unknown: the map is not sure either way. */
export function cellClass(value) {
  if (value < 0) return CELL.unknown;
  if (value <= FREE_MAX) return CELL.free;
  if (value >= OCCUPIED_MIN) return CELL.occupied;
  return CELL.unknown;
}

/* Every cell's class, with frontier = a free cell that touches an unknown one (4-neighbour). */
export function classifyGrid(grid) {
  const { width, height, data } = grid;
  const out = new Uint8Array(width * height);
  for (let i = 0; i < out.length; i++) out[i] = cellClass(data[i]);
  const unknown = (c, r) => c >= 0 && r >= 0 && c < width && r < height && out[r * width + c] === CELL.unknown;
  for (let r = 0; r < height; r++) {
    for (let c = 0; c < width; c++) {
      const i = r * width + c;
      if (out[i] === CELL.free && (unknown(c - 1, r) || unknown(c + 1, r) || unknown(c, r - 1) || unknown(c, r + 1))) out[i] = CELL.frontier;
    }
  }
  return out;
}

export function knownFraction(grid) {
  if (!grid?.data?.length) return 0;
  let known = 0;
  for (const v of grid.data) if (v >= 0) known++;
  return known / grid.data.length;
}

const list = (value) => (Array.isArray(value) ? value : []);
const unavailable = (reason, schema) => ({ available: false, reason, ...(schema ? { schema } : {}) });

function gridProblem(g) {
  if (!(g.resolution > 0)) return "occupancy resolution must be > 0";
  if (!Number.isInteger(g.width) || !Number.isInteger(g.height) || g.width < 0 || g.height < 0) return "occupancy width and height must be whole numbers";
  if (!Array.isArray(g.data)) return "occupancy data must be an array";
  if (g.data.length !== g.width * g.height) return `occupancy data has ${g.data.length} cells, expected ${g.width * g.height}`;
  return null;
}

/** @returns {SharedMap|SharedMapUnavailable} */
export function parseSharedMap(raw) {
  if (!raw || typeof raw !== "object") return unavailable("no map reply");
  if (raw.available !== true) return unavailable(raw.reason || "map not available", raw.schema);
  if (raw.schema !== SCHEMA) return unavailable(`unsupported schema ${raw.schema}`, raw.schema);
  const occupancy = raw.occupancy ?? null;
  if (occupancy) {
    const problem = gridProblem(occupancy);
    if (problem) return unavailable(problem, SCHEMA);
  }
  return {
    available: true,
    schema: SCHEMA,
    occupancy: occupancy && { ...occupancy, origin: { x: 0, y: 0, theta: 0, ...occupancy.origin } },
    robots: list(raw.robots),
    objects: list(raw.objects),
    regions: list(raw.regions),
    scans: list(raw.scans),
    known_fraction: Number.isFinite(raw.known_fraction) ? raw.known_fraction : knownFraction(occupancy),
  };
}
