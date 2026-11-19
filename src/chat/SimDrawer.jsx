// Simulation controls: exec_v2's scenario reset and fault injection. Shown only when the robot
// gateway is a simulation (fake world or fake fleet), collapsed by default, and labeled as such.
import React, { useEffect, useState } from "react";
import { execGet } from "../executive/api.js";
import { TERMINAL_MISSION } from "../fleet/execStatus.js";
import { SimTag } from "../shell/SimTag.jsx";
import { Caret } from "./ui.jsx";

const SCENARIOS = [
  { value: "unknown", label: "Unknown object/route" },
  { value: "known", label: "Known object (prior survey)" },
];

export default function SimDrawer({ state, busy, act }) {
  const [open, setOpen] = useState(false);
  const [faults, setFaults] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [scenario, setScenario] = useState(state?.scenario || "unknown");
  const [picked, setPicked] = useState([]);

  useEffect(() => {
    if (!open || faults) return undefined;
    let alive = true;
    execGet("/faults")
      .then((data) => alive && setFaults(data))
      .catch((err) => alive && setLoadError(err.message));
    return () => {
      alive = false;
    };
  }, [open, faults]);

  const available = Object.entries(faults?.available || {});
  const active = state?.faults || faults?.active || [];
  const running = state?.mission && !TERMINAL_MISSION.has(state.mission.status);
  const toggle = (name) => setPicked((p) => (p.includes(name) ? p.filter((f) => f !== name) : [...p, name]));
  const reset = () => act("/reset", { scenario, faults: picked }, `Reset: ${scenario} scenario${picked.length ? ` with ${picked.join(", ")}` : ""}.`);
  const inject = (name) => act("/faults", { fault: name }, `Injected ${name}.`);

  return (
    <section aria-label="Simulation controls" className="rounded-2xl border border-dashed border-warn/50">
      <button aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 px-4 py-2.5 text-left" onClick={() => setOpen(!open)} type="button">
        <span className="text-faint">
          <Caret open={open} />
        </span>
        <span className="whitespace-nowrap text-[14px] font-medium text-ink">Sim controls</span>
        <SimTag className="text-[11px] leading-4" />
        <span className="whitespace-nowrap text-[12px] text-warn">simulation only</span>
        {active.length > 0 && <span className="basis-full truncate pl-6 font-mono text-[12px] text-faint">active: {active.join(", ")}</span>}
      </button>
      {open && (
        <div className="space-y-3 border-t border-dashed border-warn/40 px-4 py-3">
          <p className="text-[13px] text-faint">These act on the simulated world only. Nothing here reaches a real robot.</p>
          <label className="flex items-center gap-2 text-[13px] text-muted">
            <span className="w-20 flex-none">Scenario</span>
            <select className="field min-h-8 py-1 text-[13px]" onChange={(e) => setScenario(e.target.value)} value={scenario}>
              {SCENARIOS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {loadError && (
            <p className="text-[13px] text-danger" role="alert">
              Faults unavailable: {loadError}
            </p>
          )}
          {!faults && !loadError && <p className="text-[13px] text-faint">Loading faults…</p>}
          {available.length > 0 && (
            <fieldset>
              <legend className="mb-1 text-[13px] text-muted">Faults (checked ones apply on reset)</legend>
              <ul className="space-y-1">
                {available.map(([name, description]) => (
                  <li className="flex items-start gap-2" key={name}>
                    <input checked={picked.includes(name)} className="mt-1" id={`fault-${name}`} onChange={() => toggle(name)} type="checkbox" />
                    <label className="min-w-0 flex-1 text-[13px]" htmlFor={`fault-${name}`}>
                      <span className="break-all font-mono text-ink">{name}</span>
                      <span className="block text-faint">{description}</span>
                    </label>
                    <button
                      className="flex-none rounded-md border border-line-strong px-2 py-0.5 text-[12px] text-muted hover:border-ink hover:text-ink disabled:opacity-40"
                      disabled={busy || active.includes(name)}
                      onClick={() => inject(name)}
                      type="button"
                    >
                      {active.includes(name) ? "active" : "inject now"}
                    </button>
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-secondary px-4 py-1.5 text-[14px]" disabled={busy} onClick={reset} type="button">
              Reset simulation
            </button>
            {running && <span className="text-[12px] text-warn">Resetting cancels the mission in progress.</span>}
          </div>
        </div>
      )}
    </section>
  );
}
