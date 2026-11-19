// The Map tab: polls GET /api/exec/map and draws it with SharedMap.
import React, { useEffect, useState } from "react";
import { execGet } from "../executive/api.js";
import SharedMap from "./SharedMap.jsx";
import { SCHEMA } from "./mapContract.js";

export function useSharedMap(everyMs = 3000) {
  const [state, setState] = useState({ map: null, reachable: null, error: null });
  useEffect(() => {
    let alive = true;
    let timer = null;
    const poll = async () => {
      try {
        const map = await execGet("/map");
        if (alive) setState({ map, reachable: true, error: null });
      } catch (err) {
        if (alive) setState({ map: null, reachable: false, error: err.message });
      }
      if (alive) timer = setTimeout(poll, everyMs);
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [everyMs]);
  return state;
}

export default function MapView({ robots, execState }) {
  const { map, reachable, error } = useSharedMap();
  return (
    <div className="px-8 pb-12 pt-10">
      <p className="font-mono text-[13px] text-faint">
        GET /api/exec/map · {SCHEMA}
      </p>
      <h1 className="mt-2 font-display text-[clamp(32px,3.4vw,48px)] leading-[1.05] tracking-display">Shared map</h1>
      <p className="mb-6 mt-2 max-w-[60ch] text-[16px] leading-relaxed text-muted">
        One map for the whole fleet: occupancy from LiDAR, objects from the cameras and where every robot is.
      </p>
      <SharedMap demoAllowed={import.meta.env.DEV} error={error} execState={execState} fleet={robots} map={map} reachable={reachable} />
    </div>
  );
}
