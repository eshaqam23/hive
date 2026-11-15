import React, { useEffect, useState } from "react";
import RobotViewer from "../RobotViewer.jsx";
import { PANELS } from "../viewers/panels.js";
import { resolvePanel } from "../viewers/registry.js";
import { currentTab, MODEL_TAB as MODEL, stageTabs } from "./stage.js";

const TAB_LABEL = { image: "Camera", scan: "LiDAR" };

/* With `active` and `onActive` the page owns the chosen tab (it subscribes only to what is on stage, R26);
   without them the view keeps its own. */
export default function LiveView({ robot, selectedTopic, payloads = {}, stale = {}, imageStats = {}, error = null, base, onForceFull, active: chosen, onActive }) {
  const connected = robot.status === "connected";
  const discovered = robot.discovery?.topics ?? [];
  const tabs = connected ? stageTabs(discovered, selectedTopic) : [];
  const picked = connected ? discovered.find((t) => t.name === selectedTopic?.name) : null;
  const [own, setOwn] = useState(null);
  const controlled = onActive !== undefined;
  const active = controlled ? chosen : own;
  const setActive = controlled ? onActive : setOwn;

  // a row clicked in the topic list opens in the view (the page does this itself when it owns the tab)
  useEffect(() => {
    if (picked && !controlled) setOwn(picked.name);
  }, [picked?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = currentTab(tabs, active);
  const label = (t) => (TAB_LABEL[t.kind] && tabs.find((x) => x.kind === t.kind) === t ? TAB_LABEL[t.kind] : t.name);
  const payload = current ? payloads[current.name] : null;
  const stats = current ? imageStats[current.name] : null;
  const Panel = current ? PANELS[resolvePanel({ kind: current.kind, type: current.type, topic: current.name })] : null;
  const shown = [...tabs.map((t) => ({ id: t.name, text: label(t) })), { id: MODEL, text: MODEL }];

  return (
    <div className="flex h-full flex-col">
      {connected && (
        <div className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-3 py-2" role="tablist">
          {shown.map((tab) => {
            const on = tab.id === (current ? current.name : MODEL);
            return (
              <button
                aria-selected={on}
                className={`min-h-9 rounded-full px-3 text-[13px] font-medium transition-colors ${on ? "bg-raised text-coral" : "text-muted hover:text-ink"}`}
                key={tab.id}
                onClick={() => setActive(tab.id)}
                role="tab"
                type="button"
              >
                {tab.text}
              </button>
            );
          })}
          {(payload?.hz ?? stats?.hz) != null && <span className="ml-auto font-mono text-[13px] text-live">● {Number(payload?.hz ?? stats.hz).toFixed(1)} Hz</span>}
        </div>
      )}
      {connected && error && (
        <p className="flex-none bg-warn/15 px-3 py-1.5 text-center text-[13px] font-medium text-warn" role="status">
          {error}
        </p>
      )}
      <div className="relative min-h-0 flex-1">
        {current && Panel ? (
          <Panel base={base} onForceFull={onForceFull} payload={payload} robot={robot} stale={Boolean(stale[current.name])} stats={stats} topic={current} />
        ) : (
          <RobotViewer className="absolute inset-0" key={robot.preset} preset={robot.preset} />
        )}
        {current && payload?.bytes_per_s != null && (
          <p className="pointer-events-none absolute bottom-2 right-3 font-mono text-[12px] text-faint">
            {(payload.bytes_per_s / 1e6).toFixed(2)} MB/s from car
          </p>
        )}
      </div>
    </div>
  );
}
