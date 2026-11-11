import React from "react";

const ROBOT_COLORS = { camera_01: "#3d7ea6", lidar_01: "#7a5ab8", gripper_01: "#c0582b" };

// Schematic map of named locations. Shows ONLY the observed world (accepted reports),
// never simulator truth. Positions are UI layout, not measured coordinates.
export default function WorldMap({ catalog, world, robots }) {
  const locations = Object.fromEntries(catalog.locations.map((l) => [l.location_id, l]));
  const scale = (l) => ({ x: l.x * 5, y: l.y * 3.2 });
  const routeStatus = Object.fromEntries((world.routes || []).map((r) => [r.route_id, r]));
  const objects = (world.objects || []).filter((o) => o.location_id && !o.held_by);

  return (
    <svg className="world-map" viewBox="0 0 500 320" role="img" aria-label="Observed world map">
      {catalog.locations
        .filter((l) => l.kind === "area")
        .map((l) => {
          const p = scale(l);
          return (
            <g key={l.location_id}>
              <rect x={p.x - 70} y={p.y - 45} width="140" height="110" rx="14" className="map-area" />
              <text x={p.x - 62} y={p.y - 28} className="map-label">
                {l.label}
              </text>
            </g>
          );
        })}
      {catalog.routes.map((r) => {
        const a = scale(locations[r.a]);
        const b = scale(locations[r.b]);
        const st = routeStatus[r.route_id];
        const cls = !st || !st.fresh ? "unknown" : st.status;
        return (
          <g key={r.route_id}>
            <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`map-route ${cls}`} />
            <text x={(a.x + b.x) / 2 - 40} y={(a.y + b.y) / 2 - 8} className="map-label small">
              {r.route_id}: {cls}
            </text>
          </g>
        );
      })}
      {catalog.locations
        .filter((l) => l.kind !== "area")
        .map((l) => {
          const p = scale(l);
          return (
            <g key={l.location_id}>
              <circle cx={p.x} cy={p.y} r="16" className={`map-node ${l.kind}`} />
              <text x={p.x + 20} y={p.y + 4} className="map-label small">
                {l.label}
              </text>
            </g>
          );
        })}
      {objects.map((o, i) => {
        const p = scale(locations[o.location_id]);
        return (
          <g key={o.object_id}>
            <rect x={p.x - 6 + i * 14} y={p.y + 18} width="12" height="12" rx="2" className={`map-object ${o.label}`} />
            <text x={p.x - 6 + i * 14} y={p.y + 44} className="map-label tiny">
              {o.object_id}
              {o.fresh ? "" : " (stale)"}
            </text>
          </g>
        );
      })}
      {robots.map((r, i) => {
        const loc = locations[r.state.location_id];
        if (!loc) return null;
        const p = scale(loc);
        const x = p.x - 22 + i * 4;
        const y = p.y - 26 - i * 2;
        return (
          <g key={r.robot_id}>
            <rect x={x} y={y} width="26" height="14" rx="4" fill={ROBOT_COLORS[r.robot_id] || "#555"} />
            <text x={x + 3} y={y + 11} className="map-robot-label">
              {r.robot_id.split("_")[0].slice(0, 3)}
            </text>
            {r.state.carried_object && (
              <rect
                x={x + 28}
                y={y + 1}
                width="10"
                height="10"
                rx="2"
                className={`map-object ${(world.objects || []).find((o) => o.object_id === r.state.carried_object)?.label || ""}`}
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}
