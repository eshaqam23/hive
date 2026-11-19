// A made-up shared map for the Map tab's dev-only "Demo data" toggle: a 10 m x 7 m room at 0.1 m with
// an unseen corner, three robots (one not placed yet), two objects, two regions and one ray-cast LiDAR
// scan. Not real data: the UI labels it DEMO wherever it is drawn.
import { SCHEMA, knownFraction } from "./mapContract.js";

const RES = 0.1;
const W = 100;
const H = 70;

function room() {
  const data = new Array(W * H).fill(0);
  const set = (c0, c1, r0, r1, v) => {
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) data[r * W + c] = v;
  };
  set(0, W - 1, 0, 0, 100); // walls
  set(0, W - 1, H - 1, H - 1, 100);
  set(0, 0, 0, H - 1, 100);
  set(W - 1, W - 1, 0, H - 1, 100);
  set(60, 60, 0, 19, 100); // a partition at x = 6 m with a 1 m door
  set(60, 60, 30, 45, 100);
  set(20, 29, 45, 54, 100); // crate
  set(75, 84, 50, 55, 100); // bench
  set(61, 98, 1, 30, -1); // the corner behind the partition nobody has looked at yet
  return data;
}

const DATA = room();

function castScan(origin, rays = 160, maxRange = 6) {
  const points = [];
  for (let i = 0; i < rays; i++) {
    const a = origin.theta + (i / rays) * 2 * Math.PI;
    for (let d = 0.15; d <= maxRange; d += 0.05) {
      const x = origin.x + d * Math.cos(a);
      const y = origin.y + d * Math.sin(a);
      const c = Math.floor(x / RES);
      const r = Math.floor(y / RES);
      if (c < 0 || r < 0 || c >= W || r >= H) break;
      if (DATA[r * W + c] >= 65) {
        points.push([Math.round(x * 100) / 100, Math.round(y * 100) / 100]);
        break;
      }
    }
  }
  return points;
}

const LIDAR_POSE = { x: 3.2, y: 2.1, theta: 0.6 };

export const DEMO_MAP = {
  available: true,
  schema: SCHEMA,
  occupancy: { resolution: RES, origin: { x: 0, y: 0, theta: 0 }, width: W, height: H, data: DATA, revision: 42, epoch: "demo-1" },
  robots: [
    { robot_id: "lidar_01", pose: LIDAR_POSE, source: "scan_match", age_s: 0.3 },
    { robot_id: "camera_01", pose: { x: 7.8, y: 4.2, theta: -2.4 }, source: "odom", age_s: 1.2 },
    { robot_id: "gripper_01", pose: null, source: null, age_s: null },
  ],
  objects: [
    { id: "obj_1", class: "red_block", pose: { x: 8.1, y: 3.6, theta: 0 }, covariance: [[0.02, 0.004], [0.004, 0.03]], sources: ["camera_01"], age_s: 2.4 },
    { id: "obj_2", class: "crate", pose: { x: 2.5, y: 5.0, theta: 0 }, covariance: [[0.01, 0], [0, 0.01]], sources: ["lidar_01"], age_s: 0.3 },
  ],
  regions: [
    { id: "work_area", label: "Work area", polygon: [[6.8, 3.2], [9.4, 3.2], [9.4, 6.4], [6.8, 6.4]] },
    { id: "dock", label: "Charging dock", polygon: [[0.4, 0.4], [1.6, 0.4], [1.6, 1.4], [0.4, 1.4]] },
  ],
  scans: [{ robot_id: "lidar_01", origin: LIDAR_POSE, points: castScan(LIDAR_POSE), age_s: 0.3 }],
  known_fraction: knownFraction({ data: DATA }),
};
