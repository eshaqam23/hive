import { scanToPoints } from "./lidarGeometry.js";

export const SIZE = 480;
export const MAX_RANGE_M = 6;
const FALLBACK = { line: "rgba(222,220,209,0.22)", faint: "#9c9a92", live: "#5db872", coral: "#d97757" };

// Draws the radar plot: range rings every metre, axes, the scan points and the car at the centre.
export function drawLidar(ctx, scan, { stale = false, colors = {} } = {}) {
  const c = { ...FALLBACK, ...colors };
  const centre = SIZE / 2;
  ctx.clearRect(0, 0, SIZE, SIZE);
  ctx.lineWidth = 1;
  ctx.strokeStyle = c.line;
  ctx.fillStyle = c.faint;
  ctx.font = "11px sans-serif";
  for (let m = 1; m <= MAX_RANGE_M; m += 1) {
    const r = (m / MAX_RANGE_M) * centre;
    ctx.beginPath();
    ctx.arc(centre, centre, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText(`${m} m`, centre + 3, centre - r + 11);
  }
  ctx.beginPath();
  ctx.moveTo(centre, 0);
  ctx.lineTo(centre, SIZE);
  ctx.moveTo(0, centre);
  ctx.lineTo(SIZE, centre);
  ctx.stroke();
  ctx.fillStyle = stale ? c.faint : c.live;
  const points = scanToPoints(scan, { size: SIZE, maxRangeM: MAX_RANGE_M });
  for (const p of points) ctx.fillRect(p.x - 1, p.y - 1, 2.5, 2.5);
  ctx.fillStyle = c.coral;
  ctx.beginPath();
  ctx.moveTo(centre, centre - 8);
  ctx.lineTo(centre - 5, centre + 5);
  ctx.lineTo(centre + 5, centre + 5);
  ctx.closePath();
  ctx.fill();
  return points.length;
}
