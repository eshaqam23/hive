// The shared perception map, drawn from a hive.shared-map.v0 reply (see mapContract.js). Only the UI
// exists: until the Executive builds the map it shows a designed empty state inside the final layout.
// `compact` is the canvas alone, for Chat's mini map.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { LINK_TONE, linkState } from "../fleet/presets.jsx";
import { useTheme } from "../shell/theme.js";
import { CELL, classifyGrid, parseSharedMap } from "./mapContract.js";
import { DEMO_MAP } from "./demoMap.js";
import { fitView, niceStep, scaleBar, toScreen, toWorld, zoomAt } from "./mapMath.js";

const LAYERS = [
  ["occupancy", "Occupancy"],
  ["scans", "LiDAR scans"],
  ["robots", "Robots"],
  ["objects", "Objects"],
  ["regions", "Regions"],
];
const ALL_ON = Object.fromEntries(LAYERS.map(([key]) => [key, true]));

const LEGEND = [
  ["free", "var(--map-free)"],
  ["occupied", "var(--map-occupied)"],
  ["unknown", "var(--map-unknown)"],
  ["frontier", "var(--map-frontier)"],
];

/* What each preset's sensors would put on the map. */
const CONTRIBUTES = {
  lidarRcCar: "LiDAR /scan into occupancy, plus its pose",
  cameraRcCar: "camera detections as objects, plus its pose",
  gripperRcCar: "its pose from odometry",
};

const FEEDS_LINE = "It will fuse LiDAR /scan, camera detections and robot poses from every connected car.";
const FULL_SIZE = { width: 800, height: 500 };
const COMPACT_SIZE = { width: 320, height: 180 };
const STALE_S = 5;

const deg = (rad) => (rad * 180) / Math.PI;
const fmtAge = (age) => (Number.isFinite(age) ? `${age.toFixed(1)} s` : "age —");
const fmtPose = (pose) => (pose ? `pose: ${pose.x.toFixed(2)}, ${pose.y.toFixed(2)} m · ${Math.round(deg(pose.theta ?? 0))}°` : "pose: unknown");

function useSize(ref, fallback) {
  const [size, setSize] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ width: Math.round(width), height: Math.round(height) });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/* Resolve a CSS colour (a var, hex, rgb()) to RGBA bytes by painting one pixel. */
function rgba(ctx, css) {
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = css;
  ctx.fillRect(0, 0, 1, 1);
  return Array.from(ctx.getImageData(0, 0, 1, 1).data);
}

function palette(ctx) {
  const style = getComputedStyle(document.documentElement);
  const pick = (name, fallback) => rgba(ctx, style.getPropertyValue(name).trim() || fallback);
  return {
    [CELL.unknown]: pick("--map-unknown", "#2a2927"),
    [CELL.free]: pick("--map-free", "#4a4843"),
    [CELL.occupied]: pick("--map-occupied", "#faf9f5"),
    [CELL.frontier]: pick("--map-frontier", "#6fb3c9"),
  };
}

/* The grid as a data URL, one pixel per cell, canvas row r = grid row r (the SVG flips y). */
function occupancyImage(grid) {
  if (!grid?.width || !grid?.height || typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = grid.width;
  canvas.height = grid.height;
  const ctx = canvas.getContext?.("2d", { willReadFrequently: true }); // palette() reads pixels back
  if (!ctx) return null;
  const colors = palette(ctx);
  const classes = classifyGrid(grid);
  const image = ctx.createImageData(grid.width, grid.height);
  for (let i = 0; i < classes.length; i++) image.data.set(colors[classes[i]], i * 4);
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL();
}

/* One row per saved car (mapped to its Executive robot_id by preset), then map robots no car claims. */
function robotRows(fleet, execState, mapRobots) {
  const catalog = Array.isArray(execState?.robots) ? execState.robots : [];
  const onMap = new Map(mapRobots.map((r) => [r.robot_id, r]));
  const rows = fleet.map((robot) => {
    const robotId = catalog.find((c) => c.preset === robot.preset)?.robot_id ?? null;
    return { key: robot.id, name: robot.name, robotId, entry: robotId ? (onMap.get(robotId) ?? null) : null };
  });
  const claimed = new Set(rows.map((row) => row.robotId).filter(Boolean));
  for (const r of mapRobots) {
    if (claimed.has(r.robot_id)) continue;
    rows.push({ key: `map:${r.robot_id}`, name: catalog.find((c) => c.robot_id === r.robot_id)?.display_name ?? r.robot_id, robotId: r.robot_id, entry: r });
  }
  return rows;
}

function ellipse(cov, scale) {
  const [[a, b], [, c]] = cov;
  const mid = (a + c) / 2;
  const spread = Math.sqrt(((a - c) / 2) ** 2 + b ** 2);
  return {
    rx: 2 * Math.sqrt(Math.max(mid + spread, 0)) * scale, // 2 sigma
    ry: 2 * Math.sqrt(Math.max(mid - spread, 0)) * scale,
    angle: -deg(0.5 * Math.atan2(2 * b, a - c)),
  };
}

function GridLines({ view, size }) {
  const step = niceStep(48 / view.scale);
  const [x0, y1] = toWorld(view, size, 0, 0);
  const [x1, y0] = toWorld(view, size, size.width, size.height);
  const lines = [];
  for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) {
    const [sx] = toScreen(view, size, x, 0);
    lines.push(<line key={`x${x.toFixed(4)}`} x1={sx} x2={sx} y1={0} y2={size.height} />);
  }
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
    const [, sy] = toScreen(view, size, 0, y);
    lines.push(<line key={`y${y.toFixed(4)}`} x1={0} x2={size.width} y1={sy} y2={sy} />);
  }
  return (
    <g stroke="var(--map-grid)" strokeWidth={1} aria-hidden="true">
      {lines}
    </g>
  );
}

function ScaleBar({ view, size }) {
  const bar = scaleBar(view.scale);
  const x = 16;
  const y = size.height - 16;
  return (
    <g aria-label={`Scale: ${bar.label}`} fill="var(--color-muted)" stroke="var(--color-muted)">
      <line x1={x} x2={x + bar.px} y1={y} y2={y} strokeWidth={2} />
      <line x1={x} x2={x} y1={y - 5} y2={y + 1} strokeWidth={2} />
      <line x1={x + bar.px} x2={x + bar.px} y1={y - 5} y2={y + 1} strokeWidth={2} />
      <text x={x} y={y - 9} stroke="none" fontSize={12} className="font-mono">
        {bar.label}
      </text>
    </g>
  );
}

function MapCanvas({ map, rows, layers, view, size, image, compact }) {
  const grid = map.available ? map.occupancy : null;
  const at = (x, y) => toScreen(view, size, x, y);
  const nameOf = (robotId) => rows.find((row) => row.robotId === robotId)?.name ?? robotId;
  const world = `translate(${size.width / 2 - view.cx * view.scale} ${size.height / 2 + view.cy * view.scale}) scale(${view.scale} ${-view.scale})`;
  return (
    <>
      <GridLines size={size} view={view} />
      {layers.occupancy && grid && image && (
        <g transform={world}>
          <image
            height={grid.height * grid.resolution}
            href={image}
            preserveAspectRatio="none"
            style={{ imageRendering: "pixelated" }}
            transform={`rotate(${deg(grid.origin.theta)} ${grid.origin.x} ${grid.origin.y})`}
            width={grid.width * grid.resolution}
            x={grid.origin.x}
            y={grid.origin.y}
          />
        </g>
      )}
      {map.available && layers.regions &&
        map.regions.map((region) => {
          const pts = region.polygon.map(([x, y]) => at(x, y));
          const [lx, ly] = pts.reduce(([sx, sy], [x, y]) => [sx + x / pts.length, sy + y / pts.length], [0, 0]);
          return (
            <g key={region.id}>
              <polygon fill="rgb(217 119 87 / 0.1)" points={pts.map((p) => p.join(",")).join(" ")} stroke="var(--color-coral)" strokeDasharray="6 4" strokeWidth={1.5} />
              {!compact && (
                <text fill="var(--color-coral)" fontSize={12} paintOrder="stroke" stroke="var(--map-bg)" strokeLinejoin="round" strokeWidth={3} textAnchor="middle" x={lx} y={ly}>
                  {region.label}
                </text>
              )}
            </g>
          );
        })}
      {map.available && layers.scans &&
        map.scans.map((scan) => (
          <g fill="var(--map-scan)" key={scan.robot_id} opacity={scan.age_s > STALE_S ? 0.4 : 0.9}>
            {scan.points.map(([x, y], i) => {
              const [sx, sy] = at(x, y);
              return <circle cx={sx} cy={sy} key={i} r={compact ? 1 : 1.8} />;
            })}
          </g>
        ))}
      {map.available && layers.objects &&
        map.objects.map((obj) => {
          const [sx, sy] = at(obj.pose.x, obj.pose.y);
          const e = Array.isArray(obj.covariance) ? ellipse(obj.covariance, view.scale) : null;
          return (
            <g key={obj.id}>
              {e && <ellipse cx={sx} cy={sy} fill="none" rx={e.rx} ry={e.ry} stroke="var(--color-ink)" strokeDasharray="3 3" transform={`rotate(${e.angle} ${sx} ${sy})`} />}
              <circle cx={sx} cy={sy} fill="var(--color-ink)" r={4} stroke="var(--map-bg)" strokeWidth={1.5} />
              {!compact && (
                <text className="font-mono" fill="var(--color-ink)" fontSize={11} paintOrder="stroke" stroke="var(--map-bg)" strokeLinejoin="round" strokeWidth={3} x={sx + 8} y={sy - 6}>
                  {obj.class}
                </text>
              )}
            </g>
          );
        })}
      {map.available && layers.robots &&
        map.robots
          .filter((r) => r.pose)
          .map((r) => {
            const [sx, sy] = at(r.pose.x, r.pose.y);
            return (
              <g data-testid={`robot-marker-${r.robot_id}`} key={r.robot_id} opacity={r.age_s > STALE_S ? 0.5 : 1}>
                <path d="M 11 0 L -7 7 L -3 0 L -7 -7 Z" fill="var(--color-coral)" stroke="var(--color-panel)" strokeWidth={1.5} transform={`translate(${sx} ${sy}) rotate(${-deg(r.pose.theta ?? 0)})`} />
                {!compact && (
                  <text className="font-mono" fill="var(--color-ink)" fontSize={12} paintOrder="stroke" stroke="var(--map-bg)" strokeLinejoin="round" strokeWidth={3} textAnchor="middle" x={sx} y={sy + 22}>
                    {nameOf(r.robot_id)}
                  </text>
                )}
              </g>
            );
          })}
      {!compact && <ScaleBar size={size} view={view} />}
    </>
  );
}

function EmptyState({ compact, reachable, error, reason }) {
  if (compact) {
    return (
      <div className="pointer-events-none absolute inset-0 grid place-items-center p-3 text-center">
        <p className="text-[14px] font-medium text-muted">Shared map not built yet</p>
      </div>
    );
  }
  const detail =
    reachable === false
      ? `Executive offline${error ? ` (${error})` : ""}: the map is asked for again every 3 s.`
      : reachable == null
        ? "Asking the Executive for the map…"
        : `Executive: ${reason}`;
  return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center p-6">
      <div className="max-w-[440px] rounded-2xl border border-line bg-panel/90 px-6 py-5 text-center shadow-card backdrop-blur-sm">
        <svg aria-hidden="true" className="mx-auto mb-3 text-faint" fill="none" height="36" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 36 36" width="36">
          <rect height="26" rx="3" strokeDasharray="3 3" width="26" x="5" y="5" />
          <circle cx="18" cy="18" r="3" />
          <path d="M18 9v3M18 24v3M9 18h3M24 18h3" />
        </svg>
        <p className="font-display text-[22px] leading-tight tracking-title">Shared perception map not built yet</p>
        <p className="mt-2 text-[15px] leading-relaxed text-muted">{FEEDS_LINE}</p>
        <p className="mt-3 font-mono text-[13px] text-faint">{detail}</p>
      </div>
    </div>
  );
}

const ctlClass = "grid h-9 w-9 place-items-center rounded-lg border border-line bg-panel/90 text-[18px] text-ink hover:bg-hover";

export default function SharedMap({ map: raw = null, reachable = null, error = null, fleet = [], execState = null, compact = false, demoAllowed = false }) {
  const theme = useTheme();
  const [layers, setLayers] = useState(ALL_ON);
  const [demo, setDemo] = useState(false);
  const showDemo = demo && demoAllowed && !compact;
  const map = useMemo(() => parseSharedMap(showDemo ? DEMO_MAP : raw), [showDemo, raw]);
  const grid = map.available ? map.occupancy : null;

  const boxRef = useRef(null);
  const svgRef = useRef(null);
  const size = useSize(boxRef, compact ? COMPACT_SIZE : FULL_SIZE);

  // Pan and zoom live in `view`; null means "fit the grid", which is also what a new map epoch resets to.
  const [view, setView] = useState(null);
  const fitKey = `${showDemo}:${grid?.epoch ?? ""}`;
  const [fittedFor, setFittedFor] = useState(fitKey);
  if (fittedFor !== fitKey) {
    setFittedFor(fitKey);
    setView(null);
  }
  const shown = (!compact && view) || fitView(grid, size);
  const latest = useRef({ shown, size });
  latest.current = { shown, size };

  // Repaint the grid only when it really changed (a new revision or epoch) or the theme did.
  const gridKey = grid ? `${grid.epoch}:${grid.revision}:${grid.width}x${grid.height}` : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const image = useMemo(() => occupancyImage(grid), [gridKey, theme]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || compact) return undefined;
    const onWheel = (event) => {
      event.preventDefault(); // zoom the map, not the page
      const rect = svg.getBoundingClientRect();
      const factor = Math.exp(-event.deltaY * 0.0015);
      setView((v) => zoomAt(v ?? latest.current.shown, latest.current.size, event.clientX - rect.left, event.clientY - rect.top, factor));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [compact]);

  const drag = useRef(null);
  const pan = compact
    ? {}
    : {
        onPointerDown: (event) => {
          drag.current = { x: event.clientX, y: event.clientY, from: shown };
          event.currentTarget.setPointerCapture?.(event.pointerId);
        },
        onPointerMove: (event) => {
          const d = drag.current;
          if (!d) return;
          setView({ ...d.from, cx: d.from.cx - (event.clientX - d.x) / d.from.scale, cy: d.from.cy + (event.clientY - d.y) / d.from.scale });
        },
        onPointerUp: () => {
          drag.current = null;
        },
        onPointerCancel: () => {
          drag.current = null;
        },
      };
  const zoom = (factor) => setView(zoomAt(shown, size, size.width / 2, size.height / 2, factor));

  const rows = robotRows(fleet, execState, map.available ? map.robots : []);
  const canvas = (
    <div className={`relative overflow-hidden border border-line bg-[var(--map-bg)] ${compact ? "h-[180px] rounded-xl" : "h-[min(62vh,640px)] min-h-[360px] rounded-2xl"}`} ref={boxRef}>
      <svg
        aria-label={map.available ? "Shared map canvas" : "Shared map canvas, empty"}
        className={`block h-full w-full select-none ${compact ? "" : "cursor-grab touch-none active:cursor-grabbing"}`}
        height={size.height}
        ref={svgRef}
        role="img"
        viewBox={`0 0 ${size.width} ${size.height}`}
        width={size.width}
        {...pan}
      >
        <MapCanvas compact={compact} image={image} layers={layers} map={map} rows={rows} size={size} view={shown} />
      </svg>
      {!map.available && <EmptyState compact={compact} error={error} reachable={reachable} reason={map.reason} />}
      {showDemo && (
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 grid place-items-center">
          <span className="-rotate-12 font-display text-[clamp(72px,14vw,180px)] font-semibold tracking-display text-warn/25" data-testid="demo-watermark">
            DEMO
          </span>
        </div>
      )}
      {!compact && (
        <div className="absolute right-3 top-3 flex flex-col gap-1.5">
          <button aria-label="Zoom in" className={ctlClass} onClick={() => zoom(1.25)} type="button">
            +
          </button>
          <button aria-label="Zoom out" className={ctlClass} onClick={() => zoom(0.8)} type="button">
            −
          </button>
          <button aria-label="Fit map" className={`${ctlClass} text-[12px] font-medium`} onClick={() => setView(null)} type="button">
            Fit
          </button>
        </div>
      )}
    </div>
  );

  if (compact) return canvas;

  const known = map.available ? `${Math.round(map.known_fraction * 100)}%` : "—";
  const revision = `rev ${grid?.revision ?? "—"} · epoch ${grid?.epoch ?? "—"}`;
  const built = map.available;

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div aria-label="Layers" className="flex flex-wrap gap-2" role="group">
            {LAYERS.map(([key, label]) => (
              <button aria-pressed={layers[key]} className="chip" key={key} onClick={() => setLayers((l) => ({ ...l, [key]: !l[key] }))} type="button">
                {label}
              </button>
            ))}
          </div>
          {demoAllowed && (
            <button aria-pressed={demo} className="chip border border-dashed border-warn/60" onClick={() => setDemo((d) => !d)} title="Development only: draw a made-up map to check the layout" type="button">
              Demo data <span className="font-mono text-[12px] text-faint">dev</span>
            </button>
          )}
        </div>
        {canvas}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
          <ul aria-label="Legend" className="flex flex-wrap gap-4 text-[14px] text-muted">
            {LEGEND.map(([key, color]) => (
              <li className="flex items-center gap-2" key={key}>
                <span aria-hidden="true" className="h-3.5 w-3.5 rounded-sm border border-line-strong" style={{ background: color }} />
                <span>{key}</span>
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-4 font-mono text-[13px] text-faint">
            {showDemo && <span className="rounded border border-warn/60 px-1.5 font-semibold text-warn">DEMO</span>}
            <span>
              <span className="text-[15px] font-semibold text-ink" data-testid="known">
                {known}
              </span>{" "}
              known
            </span>
            <span data-testid="revision">{revision}</span>
          </div>
        </div>
      </div>

      <aside className="flex flex-col gap-5">
        <section className="rounded-2xl border border-line bg-card p-4">
          <h2 className="text-[15px] font-semibold">Robots</h2>
          <ul aria-label="Robots on the map" className="mt-3 flex flex-col gap-3">
            {rows.length === 0 && <li className="text-[14px] text-faint">No robots yet.</li>}
            {rows.map((row) => (
              <li className="rounded-xl bg-raised px-3 py-2.5" key={row.key}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[15px] font-medium">{row.name}</span>
                  {row.robotId ? (
                    row.robotId !== row.name && <span className="font-mono text-[12px] text-faint">{row.robotId}</span>
                  ) : (
                    <span className="text-[12px] text-faint">Not in Executive catalog</span>
                  )}
                </div>
                <p className={`mt-1 font-mono text-[13px] ${row.entry?.pose ? "text-ink" : "text-faint"}`}>{fmtPose(row.entry?.pose)}</p>
                <div className="mt-1.5 flex flex-wrap gap-1.5 font-mono text-[12px]">
                  <span className="rounded border border-line-strong px-1.5 text-muted" title="What placed it on the map">
                    {row.entry?.source ?? "no source"}
                  </span>
                  <span className={`rounded border border-line-strong px-1.5 ${row.entry?.age_s > STALE_S ? "text-warn" : "text-muted"}`} title="Age of the pose">
                    {fmtAge(row.entry?.age_s)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-2xl border border-line bg-card p-4">
          <h2 className="text-[15px] font-semibold">Sources</h2>
          <ul aria-label="Sources" className="mt-3 flex flex-col gap-3">
            {fleet.length === 0 && <li className="text-[14px] text-faint">No saved cars yet. Add one on Fleet.</li>}
            {fleet.map((robot) => {
              const state = linkState(robot);
              return (
                <li className="border-t border-line pt-3 first:border-t-0 first:pt-0" key={robot.id}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[15px] font-medium">{robot.name}</span>
                    <span className={`font-mono text-[12px] ${LINK_TONE[state]}`}>{state}</span>
                  </div>
                  <p className="mt-1 text-[13px] leading-snug text-muted">
                    {built ? "contributes" : "will contribute"}: {CONTRIBUTES[robot.preset] ?? "its pose"}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      </aside>
    </div>
  );
}
