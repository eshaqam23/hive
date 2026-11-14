import { describe, expect, it } from "vitest";
import { nearestObstacle, scanToPoints } from "./lidarGeometry.js";

const scan = { angle_min: 0, angle_increment: Math.PI / 2, ranges_mm: [1000, null, 1000, 1000] };

describe("scanToPoints", () => {
  it("projects with forward up and skips nulls", () => {
    const pts = scanToPoints(scan, { size: 200, maxRangeM: 2 });
    expect(pts).toHaveLength(3);
    const exp = [{ x: 100, y: 50 }, { x: 100, y: 150 }, { x: 150, y: 100 }];
    pts.forEach((p, i) => {
      expect(p.x).toBeCloseTo(exp[i].x, 6);
      expect(p.y).toBeCloseTo(exp[i].y, 6);
    });
  });
  it("skips zero, negative and non-finite ranges", () => {
    expect(scanToPoints({ angle_min: 0, angle_increment: 1, ranges_mm: [0, -5, NaN, Infinity] }, { size: 100, maxRangeM: 1 })).toEqual([]);
  });
  it("clamps beyond max range", () => {
    const [p] = scanToPoints({ angle_min: 0, angle_increment: 1, ranges_mm: [9000] }, { size: 200, maxRangeM: 2 });
    expect(Math.hypot(p.x - 100, p.y - 100)).toBeCloseTo(100);
  });
  it("handles missing ranges", () => expect(scanToPoints({}, { size: 10, maxRangeM: 1 })).toEqual([]));
});

describe("nearestObstacle", () => {
  it("finds the closest", () => {
    const r = nearestObstacle(scan);
    expect(r.rangeM).toBe(1);
    expect(r.angleDeg).toBeCloseTo(0);
  });
  it("picks the minimum and reports its angle", () => {
    const r = nearestObstacle({ angle_min: 0, angle_increment: Math.PI / 2, ranges_mm: [3000, 500, 2000] });
    expect(r.rangeM).toBe(0.5);
    expect(r.angleDeg).toBeCloseTo(90);
  });
  it("null when empty or all invalid", () => {
    expect(nearestObstacle({ angle_min: 0, angle_increment: 1, ranges_mm: [null, 0] })).toBeNull();
    expect(nearestObstacle(null)).toBeNull();
  });
});
