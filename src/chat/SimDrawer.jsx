// Simulation controls: exec_v2's scenario reset and fault injection. Shown only when the robot
// gateway is a simulation (fake world or fake fleet), collapsed by default, and labeled as such.
// World: a seeded random world (default) or the fixed demo world; a seed replays a random world.
// "Reveal world truth" shows the simulator's hidden state for debugging; planning never sees it.
import React, { useEffect, useState } from "react";
import { execGet } from "../executive/api.js";
import { TERMINAL_MISSION } from "../fleet/execStatus.js";
import { SimTag } from "../shell/SimTag.jsx";
import { Caret } from "./ui.jsx";

const WORLDS = [
  { value: "random", label: "Random (seeded)" },
  { value: "fixed", label: "Fixed demo" },
];

// A replay seed: empty (a fresh one) or a whole number the backend accepts (0..2^53).
const seedError = (text) => (text === "" || (/^[0-9]+$/.test(text) && Number(text) <= 2 ** 53) ? null : "Seed must be a whole number, 0 or more.");

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
  const [world, setWorld] = useState("random");
  const [seed, setSeed] = useState("");
  const [showTruth, setShowTruth] = useState(false);
  const [truth, setTruth] = useState(null);
  const [truthError, setTruthError] = useState(null);
  const runId = state?.run_id;

  useEffect(() => {
    if (!open || !showTruth) return undefined;
    let alive = true;
    setTruthError(null);
    execGet("/debug/truth")
      .then((data) => alive && setTruth(data?.truth ?? null))
      .catch((err) => alive && setTruthError(err.message));
    return () => {
      alive = false;
    };
  }, [open, showTruth, runId]);

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
  const badSeed = world === "random" ? seedError(seed.trim()) : null;
  const reset = () => {
    const replay = world === "random" && seed.trim() !== "" ? { seed: Number(seed.trim()) } : {};
    const what = world === "random" ? `random world${replay.seed != null ? ` seed ${replay.seed}` : ""}` : "fixed demo world";
    act("/reset", { scenario, faults: picked, world, ...replay }, `Reset: ${scenario} scenario, ${what}${picked.length ? ` with ${picked.join(", ")}` : ""}.`);
  };
  const sim = state?.sim;
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
          <fieldset className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted">
            <legend className="sr-only">World</legend>
            <span className="w-20 flex-none" aria-hidden="true">
              World
            </span>
            {WORLDS.map((w) => (
              <label className="flex items-center gap-1" key={w.value}>
                <input checked={world === w.value} name="sim-world" onChange={() => setWorld(w.value)} type="radio" value={w.value} />
                {w.label}
              </label>
            ))}
          </fieldset>
          <label className="flex items-center gap-2 text-[13px] text-muted">
            <span className="w-20 flex-none">Replay seed</span>
            <input
              className="field min-h-8 w-40 py-1 font-mono text-[13px]"
              disabled={world !== "random"}
              inputMode="numeric"
              min="0"
              onChange={(e) => setSeed(e.target.value)}
              placeholder="new seed"
              step="1"
              type="number"
              value={seed}
            />
          </label>
          {badSeed && (
            <p className="text-[13px] text-danger" role="alert">
              {badSeed}
            </p>
          )}
          <p className="font-mono text-[12px] text-faint">
            current world: {sim ? `${sim.world}${sim.seed != null ? ` seed ${sim.seed}` : ""}` : "unknown"}
          </p>
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
            <button className="btn btn-secondary px-4 py-1.5 text-[14px]" disabled={busy || !!badSeed} onClick={reset} type="button">
              Reset simulation
            </button>
            {running && <span className="text-[12px] text-warn">Resetting cancels the mission in progress.</span>}
          </div>
          <button
            aria-pressed={showTruth}
            className="rounded-md border border-dashed border-warn/60 px-3 py-1 text-[13px] text-warn hover:border-warn"
            onClick={() => setShowTruth(!showTruth)}
            type="button"
          >
            {showTruth ? "Hide world truth" : "Reveal world truth"}
          </button>
          {showTruth && <WorldTruth error={truthError} truth={truth} />}
        </div>
      )}
    </section>
  );
}

// The simulator's hidden state. Debug only: the planner and the normal map never see it.
function WorldTruth({ truth, error }) {
  const objects = Object.entries(truth?.objects || {});
  const robots = Object.entries(truth?.robots || {});
  const corridor = Object.entries(truth?.corridor || {});
  return (
    <section aria-label="World truth" className="space-y-2 rounded-xl border border-dashed border-warn/50 px-3 py-2 text-[13px]">
      <p className="text-[12px] text-warn">Simulation only: hidden simulator truth for debugging. Planning never sees it.</p>
      {error && (
        <p className="text-danger" role="alert">
          Truth unavailable: {error}
        </p>
      )}
      {!truth && !error && <p className="text-faint">Loading truth…</p>}
      {truth && (
        <>
          <p className="font-mono text-[12px] text-faint">
            world {truth.world}
            {truth.seed != null ? ` seed ${truth.seed}` : ""}
          </p>
          <div>
            <h4 className="text-muted">Objects</h4>
            {objects.length === 0 && <p className="text-faint">none</p>}
            <ul className="font-mono text-[12px] text-ink">
              {objects.map(([id, o]) => (
                <li className="break-all" key={id}>
                  {id} ({o.label}) at {o.location_id || "?"}
                  {o.held_by ? `, held by ${o.held_by}` : ""}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="text-muted">Corridor</h4>
            <ul className="font-mono text-[12px] text-ink">
              {corridor.map(([route, st]) => (
                <li key={route}>
                  {route}: {st}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="text-muted">Robots</h4>
            <ul className="font-mono text-[12px] text-ink">
              {robots.map(([id, r]) => (
                <li key={id}>
                  {id} at {r.location_id || "?"}, battery {r.battery_pct != null ? `${r.battery_pct}%` : "?"}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </section>
  );
}
