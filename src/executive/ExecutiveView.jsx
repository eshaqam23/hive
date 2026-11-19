import React, { useCallback, useEffect, useState } from "react";
import { EXEC_API_URL, execGet, execPost } from "./api.js";
import PlanPanel from "./PlanPanel.jsx";
import WorldMap from "./WorldMap.jsx";
import "./executive.css";

const DEMO_COMMAND =
  "Find the red block in the work area, check the pickup corridor, and bring the block to the drop-off area.";

const STATUS_TEXT = {
  planning: "Planning…",
  awaiting_approval: "Awaiting operator approval",
  executing: "Executing approved revision",
  evaluating: "Evaluating observations",
  recovering: "Task failed — settling and preparing one recovery proposal",
  clarification: "Clarification needed",
  canceling: "Canceling — waiting for robots to confirm",
  completed: "Completed",
  blocked: "Blocked",
  rejected: "Rejected",
  canceled: "Canceled",
  planner_error: "Planner error",
};

export default function ExecutiveView() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [command, setCommand] = useState(DEMO_COMMAND);
  const [planner, setPlanner] = useState("");
  const [answer, setAnswer] = useState("");
  const [scenario, setScenario] = useState("unknown");
  const [faults, setFaults] = useState([]);
  const [availableFaults, setAvailableFaults] = useState({});

  const refresh = useCallback(async () => {
    try {
      const next = await execGet("/state");
      setState(next);
      setError((current) => (current.startsWith("Executive service unreachable") ? "" : current));
    } catch (err) {
      setError(`Executive service unreachable at ${EXEC_API_URL} (${err.message}). Start it with npm run hive.`);
    }
  }, []);

  useEffect(() => {
    refresh();
    execGet("/faults").then((f) => setAvailableFaults(f.available)).catch(() => {});
    const timer = setInterval(refresh, 500);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (state && !planner) setPlanner(state.default_planner);
  }, [state, planner]);

  async function act(fn) {
    setBusy(true);
    setError("");
    try {
      const next = await fn();
      if (next && next.run_id) setState(next);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      refresh();
    }
  }

  if (!state) {
    return (
      <section className="stack exec-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">Executive</p>
            <h1>Mission</h1>
          </div>
        </header>
        <p className="error-text">{error || "Connecting to the Executive service…"}</p>
      </section>
    );
  }

  const mission = state.mission;
  const qwen = state.planners.qwen;
  const gateway = state.gateway;
  const terminal = mission && ["completed", "blocked", "rejected", "canceled", "planner_error"].includes(mission.status);

  return (
    <section className="exec-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Executive</p>
          <h1>Mission</h1>
        </div>
        <div className="badge-row">
          <span className={`badge ${qwen?.live_enabled ? "qwen" : "off"}`} title={qwen?.probe?.error || ""}>
            Qwen: {qwen?.configured ? (qwen.live_enabled ? `probe ok · ${qwen.model}` : "probe failed") : "not configured"}
          </span>
          <span className="badge baseline">Baseline available (labeled)</span>
          <span className={`badge transport ${gateway.verified_transport ? "verified" : ""}`} title={gateway.detail}>
            Transport: {gateway.label}
          </span>
          <span className="badge run">run {state.run_id}</span>
        </div>
      </header>

      {error && <p className="error-text exec-error">{error}</p>}

      <div className="exec-grid">
        <div className="exec-column">
          <section className="panel exec-panel">
            <p className="eyebrow">Command</p>
            <textarea
              className="exec-command"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              disabled={busy || (mission && !terminal)}
            />
            <div className="action-row">
              <select value={planner} onChange={(e) => setPlanner(e.target.value)} disabled={busy}>
                <option value="qwen">Qwen3 (vLLM endpoint)</option>
                <option value="baseline">Baseline templates (not Qwen)</option>
              </select>
              <button
                className="primary"
                disabled={busy || !command.trim() || Boolean(mission)}
                onClick={() => act(() => execPost("/missions", { command, planner }))}
                title={mission ? "Reset the scenario before another run" : ""}
              >
                {busy && !mission ? "Planning…" : "Plan mission"}
              </button>
              {mission && !terminal && (
                <button className="danger" disabled={busy || mission.status === "canceling"} onClick={() => act(() => execPost("/cancel"))}>
                  Cancel mission
                </button>
              )}
            </div>
            {mission && (
              <div className={`mission-status ${mission.status}`}>
                <strong>{STATUS_TEXT[mission.status] || mission.status}</strong>
                <small>
                  {mission.mission_id} · planner {mission.planner_kind} · stage {mission.stage_count}
                  {mission.recovery_used ? " · recovery used" : ""}
                </small>
                {mission.outcome && <p>{mission.outcome.reason}</p>}
                {mission.canonical_goals && (
                  <small>Canonical goals: {mission.canonical_goals.map((g) => g.kind).join(", ")}</small>
                )}
                {mission.status === "clarification" && (
                  <form
                    className="clarify-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      act(() => execPost("/mission/clarify", { answer })).then(() => setAnswer(""));
                    }}
                  >
                    <p>{mission.clarification_question}</p>
                    <input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Your answer" />
                    <button className="primary" disabled={busy || !answer.trim()}>Answer</button>
                  </form>
                )}
                {mission.status === "planner_error" && (
                  <small>
                    The planner failed visibly; nothing was dispatched. Fix the endpoint or reset and choose the
                    Baseline planner explicitly.
                  </small>
                )}
              </div>
            )}
          </section>

          <PlanPanel
            mission={mission}
            robots={state.robots}
            busy={busy}
            onApprove={(rev) =>
              act(() => execPost(`/revisions/${rev.revision}/approve`, { proposal_hash: rev.proposal_hash }))
            }
            onReject={(rev) => act(() => execPost(`/revisions/${rev.revision}/reject`, { reason: "" }))}
            onEdit={(rev, edits) => act(() => execPost(`/revisions/${rev.revision}/edit`, { edits }))}
          />
        </div>

        <div className="exec-column">
          <section className="panel exec-panel">
            <div className="section-title">
              <p className="eyebrow">Robots</p>
              <small>battery reserve {state.catalog.battery.reserve_pct}% · synthetic values</small>
            </div>
            <div className="exec-robots">
              {state.robots.map((r) => (
                <div className="exec-robot" key={r.robot_id}>
                  <div className="card-header">
                    <strong>{r.robot_id}</strong>
                    <span className={`status-pill ${r.state.available && r.state.health === "ok" ? "connected" : "failed"}`}>
                      {r.state.health !== "ok" ? r.state.health : r.state.available ? (r.state.current_attempt_id ? "busy" : "ready") : "unavailable"}
                    </span>
                  </div>
                  <small>{r.display_name}</small>
                  <span>📍 {r.state.location_id}</span>
                  <span>🔋 {r.state.battery_pct.toFixed(1)}%</span>
                  <span>✋ {r.state.carried_object || "empty"}</span>
                  <div className="capability-row">
                    {r.skills.map((s) => (
                      <span key={s}>{s}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel exec-panel">
            <div className="section-title">
              <p className="eyebrow">Observed world</p>
              <small>v{state.world.version} · accepted reports only</small>
            </div>
            <WorldMap catalog={state.catalog} world={state.world} robots={state.robots} />
          </section>

          <section className="panel exec-panel">
            <p className="eyebrow">Scenario</p>
            <div className="action-row">
              <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
                <option value="unknown">Unknown object/route</option>
                <option value="known">Known object (prior survey)</option>
              </select>
              <button
                disabled={busy}
                onClick={() => act(() => execPost("/reset", { scenario, faults }))}
                title="Settles old work, then starts a fresh fake world and run ID"
              >
                Reset run
              </button>
              <a className="button-link" href={`${EXEC_API_URL}/export`} target="_blank" rel="noreferrer">
                Export JSON
              </a>
            </div>
            <div className="fault-list">
              {Object.entries(availableFaults).map(([key, text]) => (
                <label key={key} title={text}>
                  <input
                    type="checkbox"
                    checked={faults.includes(key)}
                    onChange={(e) =>
                      setFaults((current) => (e.target.checked ? [...current, key] : current.filter((f) => f !== key)))
                    }
                  />
                  {key}
                </label>
              ))}
            </div>
            <small>Faults apply on the next reset. Active: {state.faults.join(", ") || "none"}</small>
          </section>

          <section className="panel exec-panel timeline-panel">
            <p className="eyebrow">Timeline</p>
            <ol className="timeline">
              {[...state.timeline].reverse().map((e) => (
                <li key={e.seq} className={e.kind}>
                  <span>{new Date(e.t * 1000).toLocaleTimeString()}</span>
                  <b>{e.kind}</b> {e.message}
                </li>
              ))}
            </ol>
          </section>
        </div>
      </div>
    </section>
  );
}
