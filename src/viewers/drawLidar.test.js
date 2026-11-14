import { describe, expect, it, vi } from "vitest";
import { drawLidar, MAX_RANGE_M, SIZE } from "./drawLidar.js";

const fakeCtx = () =>
  Object.fromEntries(
    ["clearRect", "beginPath", "arc", "stroke", "fillText", "moveTo", "lineTo", "closePath", "fill", "fillRect"].map((n) => [n, vi.fn()]),
  );

describe("drawLidar", () => {
  it("draws a ring per metre, one square per valid range and the car", () => {
    const ctx = fakeCtx();
    const scan = { angle_min: 0, angle_increment: Math.PI / 2, ranges_mm: [1000, null, 2000, 0] };
    const n = drawLidar(ctx, scan);
    expect(n).toBe(2);
    expect(ctx.arc).toHaveBeenCalledTimes(MAX_RANGE_M);
    expect(ctx.fillText).toHaveBeenCalledWith("1 m", expect.any(Number), expect.any(Number));
    expect(ctx.fillRect).toHaveBeenCalledTimes(2);
    expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, SIZE, SIZE);
    expect(ctx.fill).toHaveBeenCalledTimes(1);
  });

  it("greys the points when stale and survives a missing scan", () => {
    const ctx = fakeCtx();
    const styles = [];
    Object.defineProperty(ctx, "fillStyle", { set: (v) => styles.push(v), get: () => "" });
    expect(drawLidar(ctx, undefined, { stale: true, colors: { faint: "#111", live: "#222" } })).toBe(0);
    expect(styles).toContain("#111");
    expect(styles).not.toContain("#222");
  });
});
