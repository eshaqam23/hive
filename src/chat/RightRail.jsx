// The chat's right rail: the Executive's fleet (compact), the shared mini map and the agent reports.
import React from "react";
import { isSimulated } from "../fleet/execStatus.js";
import { useSharedMap } from "../map/MapView.jsx";
import SharedMap from "../map/SharedMap.jsx";
import { SimTag } from "../shell/SimTag.jsx";
import { robotsIn, taskLabel } from "./chatModel.js";
import { Eyebrow, clock } from "./ui.jsx";

const MAX_REPORTS = 80;

function robotChip(robot, mission) {
  const s = robot.state || {};
  if (s.health && s.health !== "ok") return { label: s.health, tone: "text-danger", dot: "bg-danger" };
  if (s.available === false) return { label: "unavailable", tone: "text-warn", dot: "bg-warn" };
  if (s.current_attempt_id) {
    const task = String(s.current_attempt_id).split(".")[1];
    const busy = mission && task ? `busy · ${taskLabel(task)}` : "busy";
    return { label: busy, tone: "text-coral", dot: "bg-coral" };
  }
  return { label: "idle", tone: "text-muted", dot: "bg-faint" };
}

export function FleetList({ state }) {
  const sim = isSimulated(state?.gateway);
  const robots = state?.robots || [];
  const place = (id) => state?.catalog?.locations?.find((l) => l.location_id === id)?.label ?? id;
  return (
    <section aria-label="Fleet">
      <div className="mb-2 flex items-center justify-between">
        <Eyebrow>Fleet</Eyebrow>
        {state?.gateway?.label && <span className="max-w-[60%] truncate text-[12px] text-faint" title={state.gateway.detail}>{state.gateway.label}</span>}
      </div>
      {robots.length === 0 ? (
        <p className="text-[13px] text-faint">No robots reported yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {robots.map((robot) => {
            const chip = robotChip(robot, state.mission);
            const battery = robot.state?.battery_pct;
            return (
              <li className="rounded-xl border border-line bg-card px-3 py-2" data-robot={robot.robot_id} key={robot.robot_id}>
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-ink">{robot.display_name}</span>
                  {sim && <SimTag className="text-[11px] leading-4" />}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[12px]">
                  <span className={`inline-flex items-center gap-1 ${chip.tone}`}>
                    <span aria-hidden="true" className={`size-1.5 rounded-full ${chip.dot}`} />
                    {chip.label}
                  </span>
                  <span className="text-faint">·</span>
                  <span className={`font-mono ${Number(battery) < 20 ? "text-warn" : "text-muted"}`}>{Number.isFinite(battery) ? `${Math.round(battery)}%` : "—"}</span>
                  <span className="min-w-0 flex-1 truncate text-right text-faint">{robot.state?.carried_object ? `holds ${robot.state.carried_object}` : place(robot.state?.location_id)}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function AgentReports({ state }) {
  const robots = state?.robots || [];
  const rows = (state?.timeline || [])
    .map((e) => ({ e, who: robotsIn(e.message, robots) }))
    .filter((r) => r.who.length || r.e.kind === "fault")
    .slice(-MAX_REPORTS)
    .reverse();
  return (
    <section aria-label="Agent reports">
      <Eyebrow className="mb-2">Agent reports</Eyebrow>
      {rows.length === 0 ? (
        <p className="text-[13px] text-faint">Nothing reported yet. Reports appear when robots take tasks.</p>
      ) : (
        <ol className="space-y-1.5">
          {rows.map(({ e, who }) => (
            <li className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-[12px] leading-snug" key={e.seq}>
              <time className="font-mono text-faint">{clock(e.t)}</time>
              <span className="min-w-0">
                <span className="font-mono text-muted">{who.length ? who.map((r) => r.robot_id).join(", ") : "sim"}</span>
                <span className="text-faint"> · </span>
                <span className={e.kind === "fault" ? "text-warn" : "text-ink/90"}>{e.message}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function MiniMap({ robots, state }) {
  const { map, reachable, error } = useSharedMap();
  return (
    <section aria-label="Map">
      <Eyebrow className="mb-2">Map</Eyebrow>
      <SharedMap compact error={error} execState={state} fleet={robots} map={map} reachable={reachable} />
    </section>
  );
}

export default function RightRail({ state, robots, showMap = true, children = null }) {
  return (
    <aside aria-label="Mission context" className="relative flex flex-col gap-6 border-line bg-sidebar px-5 py-6 lg:h-full lg:min-h-0 lg:overflow-y-auto lg:border-l">
      <FleetList state={state} />
      {showMap && <MiniMap robots={robots} state={state} />}
      <AgentReports state={state} />
      {children}
    </aside>
  );
}
