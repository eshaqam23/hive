const valid = (r) => typeof r === "number" && Number.isFinite(r) && r > 0;

// Canvas centre is the car; angle 0 points up and positive angles go counter-clockwise (left on screen).
export function scanToPoints(scan, { size, maxRangeM }) {
  const { angle_min = 0, angle_increment = 0, ranges_mm = [] } = scan ?? {};
  const centre = size / 2;
  const points = [];
  ranges_mm.forEach((mm, i) => {
    if (!valid(mm)) return;
    const radius = (Math.min(mm / 1000, maxRangeM) / maxRangeM) * centre;
    const angle = angle_min + i * angle_increment;
    points.push({ x: centre - radius * Math.sin(angle), y: centre - radius * Math.cos(angle) });
  });
  return points;
}

export function nearestObstacle(scan) {
  if (!scan || !Array.isArray(scan.ranges_mm)) return null;
  let best = -1;
  scan.ranges_mm.forEach((mm, i) => {
    if (valid(mm) && (best < 0 || mm < scan.ranges_mm[best])) best = i;
  });
  if (best < 0) return null;
  const angle = (scan.angle_min ?? 0) + best * (scan.angle_increment ?? 0);
  return { rangeM: scan.ranges_mm[best] / 1000, angleDeg: (angle * 180) / Math.PI };
}
