// View math for the shared map: a view is { cx, cy, scale }, the world point at the canvas centre
// (metres) and pixels per metre. World y is up; screen y is down.

export const MIN_SCALE = 5;
export const MAX_SCALE = 400;

export const clampScale = (s) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));

const nice = (k, m) => (k >= 0 ? m * 10 ** k : m / 10 ** -k);

/* The smallest 1-2-5 value >= raw (grid spacing). */
export function niceStep(raw) {
  const k = Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) {
    const v = nice(k, m);
    if (v >= raw - 1e-12) return v;
  }
  return nice(k + 1, 1);
}

/* The 1-2-5 value nearest raw (in ratio), for a scale bar that stays close to its target length. */
function niceNearest(raw) {
  const k = Math.floor(Math.log10(raw));
  let best = nice(k, 1);
  for (const v of [1, 2, 5, 10].map((m) => nice(k, m))) {
    if (Math.abs(Math.log(v / raw)) < Math.abs(Math.log(best / raw))) best = v;
  }
  return best;
}

const lengthLabel = (m) => (m >= 1 ? `${m} m` : m >= 0.01 ? `${Math.round(m * 100)} cm` : `${Math.round(m * 1000)} mm`);

export function scaleBar(scale, targetPx = 100) {
  const metres = niceNearest(targetPx / scale);
  return { metres, px: Math.round(metres * scale * 10) / 10, label: lengthLabel(metres) };
}

export const toScreen = (view, size, x, y) => [size.width / 2 + (x - view.cx) * view.scale, size.height / 2 - (y - view.cy) * view.scale];

export const toWorld = (view, size, sx, sy) => [view.cx + (sx - size.width / 2) / view.scale, view.cy - (sy - size.height / 2) / view.scale];

/* Zoom by factor, keeping the world point under (sx, sy) where it is. */
export function zoomAt(view, size, sx, sy, factor) {
  const scale = clampScale(view.scale * factor);
  const [wx, wy] = toWorld(view, size, sx, sy);
  return { scale, cx: wx - (sx - size.width / 2) / scale, cy: wy + (sy - size.height / 2) / scale };
}

/* The view that shows the whole grid (ignoring its rotation) with a 10 % margin; 12 m across without one. */
export function fitView(grid, size) {
  if (!grid || !grid.width || !grid.height) return { cx: 0, cy: 0, scale: clampScale(Math.min(size.width, size.height) / 12) };
  const w = grid.width * grid.resolution;
  const h = grid.height * grid.resolution;
  return {
    cx: grid.origin.x + w / 2,
    cy: grid.origin.y + h / 2,
    scale: clampScale(Math.min((size.width * 0.9) / w, (size.height * 0.9) / h)),
  };
}
