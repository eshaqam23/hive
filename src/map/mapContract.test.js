import { describe, expect, it } from "vitest";
import { CELL, SCHEMA, cellClass, classifyGrid, knownFraction, mapTag, parseSharedMap } from "./mapContract.js";
import { DEMO_MAP } from "./demoMap.js";
import { clampScale, fitView, niceStep, scaleBar, toScreen, toWorld, zoomAt } from "./mapMath.js";

const grid = (width, height, data, extra = {}) => ({ resolution: 0.5, origin: { x: 0, y: 0, theta: 0 }, width, height, data, revision: 3, epoch: 1, ...extra });
const map = (over = {}) => ({
  available: true,
  schema: SCHEMA,
  occupancy: grid(2, 2, [0, 100, -1, 50]),
  robots: [{ robot_id: "lidar_01", pose: { x: 1, y: 1, theta: 0 }, source: "odom", age_s: 0.4 }],
  objects: [],
  regions: [],
  known_fraction: 0.75,
  ...over,
});

describe("parseSharedMap", () => {
  it("passes the Executive's not-built-yet reply through as unavailable", () => {
    const raw = { available: false, reason: "shared perception not built yet", schema: SCHEMA };
    expect(parseSharedMap(raw)).toEqual({ available: false, reason: "shared perception not built yet", schema: SCHEMA });
  });

  it("is unavailable for nothing, another schema, or a grid whose data does not match its size", () => {
    expect(parseSharedMap(null)).toMatchObject({ available: false, reason: "no map reply" });
    expect(parseSharedMap({ ok: true })).toMatchObject({ available: false });
    expect(parseSharedMap(map({ schema: "hive.shared-map.v9" }))).toMatchObject({ available: false, reason: "unsupported schema hive.shared-map.v9" });
    expect(parseSharedMap(map({ occupancy: grid(2, 2, [0, 0, 0]) }))).toMatchObject({ available: false, reason: "occupancy data has 3 cells, expected 4" });
    expect(parseSharedMap(map({ occupancy: grid(2, 2, [0, 0, 0, 0], { resolution: 0 }) }))).toMatchObject({ available: false, reason: "occupancy resolution must be > 0" });
  });

  it("keeps a valid map and fills missing lists", () => {
    const parsed = parseSharedMap(map({ objects: undefined, regions: undefined }));
    expect(parsed.available).toBe(true);
    expect(parsed.objects).toEqual([]);
    expect(parsed.regions).toEqual([]);
    expect(parsed.scans).toEqual([]);
    expect(parsed.known_fraction).toBe(0.75);
    expect(parsed.robots[0].pose).toEqual({ x: 1, y: 1, theta: 0 });
  });

  it("computes known_fraction when the map leaves it out, and allows a map with no grid yet", () => {
    expect(parseSharedMap(map({ known_fraction: undefined })).known_fraction).toBe(0.75);
    const bare = parseSharedMap(map({ occupancy: null, known_fraction: undefined }));
    expect(bare).toMatchObject({ available: true, occupancy: null, known_fraction: 0 });
  });
});

describe("cells", () => {
  it("classifies ROS occupancy values: -1 unknown, <=25 free, >=65 occupied, between unknown", () => {
    expect([-1, 0, 25, 26, 64, 65, 100].map(cellClass)).toEqual([CELL.unknown, CELL.free, CELL.free, CELL.unknown, CELL.unknown, CELL.occupied, CELL.occupied]);
  });

  it("marks free cells next to unknown ones as frontier", () => {
    // 3x3, row-major from the origin row: unknown column on the right
    const g = grid(3, 3, [0, 0, -1, 0, 0, -1, 100, 0, -1]);
    const classes = Array.from(classifyGrid(g));
    expect(classes).toEqual([CELL.free, CELL.frontier, CELL.unknown, CELL.free, CELL.frontier, CELL.unknown, CELL.occupied, CELL.frontier, CELL.unknown]);
  });

  it("counts known cells", () => {
    expect(knownFraction(grid(2, 2, [0, 100, -1, -1]))).toBe(0.5);
    expect(knownFraction(null)).toBe(0);
  });
});

describe("demo fixture", () => {
  it("is a valid v0 map, a 10 m x 7 m room at 0.1 m, with an unknown region and one robot without a pose", () => {
    const parsed = parseSharedMap(DEMO_MAP);
    expect(parsed.available).toBe(true);
    expect(parsed.occupancy).toMatchObject({ width: 100, height: 70, resolution: 0.1 });
    expect(parsed.known_fraction).toBeGreaterThan(0.5);
    expect(parsed.known_fraction).toBeLessThan(1);
    expect(parsed.robots.some((r) => r.pose === null)).toBe(true);
    expect(parsed.scans.length).toBeGreaterThan(0);
    expect(parsed.objects.length).toBeGreaterThan(0);
    expect(parsed.regions.length).toBeGreaterThan(0);
  });
});

describe("mapMath", () => {
  it("picks 1-2-5 steps", () => {
    expect([0.07, 0.1, 0.3, 0.9, 1.2, 4, 7, 30].map(niceStep)).toEqual([0.1, 0.1, 0.5, 1, 2, 5, 10, 50]);
  });

  it("sizes the scale bar near 100 px", () => {
    expect(scaleBar(50)).toEqual({ metres: 2, px: 100, label: "2 m" });
    expect(scaleBar(400)).toEqual({ metres: 0.2, px: 80, label: "20 cm" });
    expect(scaleBar(8)).toEqual({ metres: 10, px: 80, label: "10 m" });
    expect(scaleBar(30)).toEqual({ metres: 5, px: 150, label: "5 m" });
  });

  it("maps world to screen with y up, and back", () => {
    const view = { cx: 2, cy: 1, scale: 100 };
    const size = { width: 800, height: 600 };
    expect(toScreen(view, size, 2, 1)).toEqual([400, 300]);
    expect(toScreen(view, size, 3, 2)).toEqual([500, 200]);
    expect(toWorld(view, size, 500, 200)).toEqual([3, 2]);
  });

  it("zooms about the pointer and clamps the scale", () => {
    const size = { width: 800, height: 600 };
    const view = zoomAt({ cx: 0, cy: 0, scale: 50 }, size, 600, 300, 2);
    expect(view.scale).toBe(100);
    expect(toWorld(view, size, 600, 300)).toEqual([4, 0]); // the point under the pointer stays put
    expect(clampScale(1)).toBe(5);
    expect(clampScale(1000)).toBe(400);
  });

  it("fits a grid into the view with a margin", () => {
    const view = fitView(grid(20, 10, new Array(200).fill(0)), { width: 1000, height: 500 });
    expect(view.cx).toBe(5);
    expect(view.cy).toBe(2.5);
    expect(view.scale).toBeCloseTo(90); // 10 m wide into 1000 px with 10 % margin
    expect(fitView(null, { width: 800, height: 600 })).toEqual({ cx: 0, cy: 0, scale: 50 });
  });
});

describe("provenance (A8)", () => {
  const base = { available: true, schema: SCHEMA, occupancy: null };
  const prov = { source: "map_area", robot_id: "lidar_01", attempt_id: "run-1:t1:1", coverage: 0.95, evidence: "emulated" };

  it("keeps a map_area provenance and fails safe on an unknown evidence", () => {
    expect(parseSharedMap({ ...base, provenance: prov }).provenance).toEqual(prov);
    expect(parseSharedMap({ ...base, provenance: { ...prov, evidence: "hardware" } }).provenance.evidence).toBe("simulated");
    expect(parseSharedMap({ ...base, provenance: { ...prov, evidence: undefined } }).provenance.evidence).toBe("simulated");
    expect(parseSharedMap({ ...base, provenance: "lidar" }).provenance).toBe(null);
    expect(parseSharedMap(base).provenance).toBe(null);
  });

  it("tags a map SIM or EMULATED, nothing for real; a map without provenance is SIM", () => {
    const tag = (evidence) => mapTag(parseSharedMap({ ...base, provenance: { ...prov, evidence } }));
    expect(tag("simulated")).toBe("SIM");
    expect(tag("emulated")).toBe("EMULATED");
    expect(tag("real")).toBe(null);
    expect(tag("hardware")).toBe("SIM");
    expect(mapTag(parseSharedMap(base))).toBe("SIM");
    expect(mapTag(parseSharedMap({ available: false, reason: "no map reported in this run" }))).toBe(null);
    expect(mapTag(null)).toBe(null);
  });
});
