import React, { useEffect, useRef } from "react";
import { drawLidar, SIZE } from "./drawLidar.js";
import { nearestObstacle } from "./lidarGeometry.js";

export default function LidarPanel({ payload, stale }) {
  const canvasRef = useRef(null);
  const scan = payload?.data;
  const nearest = nearestObstacle(scan);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.("2d");
    if (!ctx) return;
    const css = getComputedStyle(canvas);
    const read = (name) => css.getPropertyValue(name).trim() || undefined;
    drawLidar(ctx, scan, {
      stale,
      colors: { line: read("--color-line-strong"), faint: read("--color-faint"), live: read("--color-live"), coral: read("--color-coral") },
    });
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
