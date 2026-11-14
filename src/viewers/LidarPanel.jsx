import React, { useEffect, useRef } from "react";
import { nearestObstacle, scanToPoints } from "./lidarGeometry.js";

const SIZE = 480;
const MAX_RANGE_M = 6;

export default function LidarPanel({ payload, stale }) {
  const canvasRef = useRef(null);
  const scan = payload?.data;
  const nearest = nearestObstacle(scan);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.("2d");
    if (!ctx) return;
    const css = getComputedStyle(canvas);
    const color = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    const centre = SIZE / 2;
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.lineWidth = 1;
    ctx.strokeStyle = color("--color-line-strong", "rgba(222,220,209,0.22)");
    ctx.fillStyle = color("--color-faint", "#9c9a92");
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
    ctx.fillStyle = stale ? color("--color-faint", "#9c9a92") : color("--color-live", "#5db872");
    for (const p of scanToPoints(scan, { size: SIZE, maxRangeM: MAX_RANGE_M })) ctx.fillRect(p.x - 1, p.y - 1, 2.5, 2.5);
    ctx.fillStyle = color("--color-coral", "#d97757");
    ctx.beginPath();
    ctx.moveTo(centre, centre - 8);
    ctx.lineTo(centre - 5, centre + 5);
    ctx.lineTo(centre + 5, centre + 5);
    ctx.closePath();
    ctx.fill();
  }, [scan, stale]);

  return (
    <div className="relative grid h-full place-items-center p-2">
      <canvas aria-label="LiDAR scan, car at the centre facing up" className="max-h-full max-w-full" height={SIZE} ref={canvasRef} role="img" width={SIZE} />
      <p className="absolute bottom-2 left-3 font-mono text-[13px] text-muted">
        {!payload
          ? "Waiting for a scan…"
          : nearest
            ? `Nearest ${nearest.rangeM.toFixed(2)} m at ${nearest.angleDeg.toFixed(0)}°`
            : "No obstacle in range"}
      </p>
    </div>
  );
}
