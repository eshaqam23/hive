import React, { useEffect, useMemo, useState } from "react";
import { describeAction, describeGoal } from "./api.js";

const EDITABLE_FIELDS = {
  navigate_to: { location_id: ["pickup_zone", "dropoff_zone", "camera_post", "lidar_post"] },
  inspect_area: { area_id: ["work_area", "pickup_zone", "dropoff_zone"] },
  capture_image: { area_id: ["work_area", "pickup_zone", "dropoff_zone"] },
  place_object: { location_id: ["dropoff_zone", "pickup_zone"] },
};

export default function PlanPanel({ mission, robots, busy, onApprove, onReject, onEdit }) {
  const revisions = mission?.revisions || [];
  const [selected, setSelected] = useState(null);
  const latest = revisions[revisions.length - 1];
  const rev = revisions.find((r) => r.revision === selected) || latest;

  useEffect(() => {
    setSelected(null);
  }, [revisions.length]);

  if (!mission) {
    return (
      <section className="panel exec-panel">
        <p className="eyebrow">Plan</p>
        <p className="muted">Enter a mission to see the planner's interpretation, tasks and validation.</p>
      </section>
    );
  }

  return (
    <section className="panel exec-panel">
      <div className="section-title">
        <div>
          <p className="eyebrow">Plan</p>
          <h2>{rev ? `Revision ${rev.revision}` : "No executable plan"}</h2>
        </div>
        {revisions.length > 1 && (
          <div className="chips">
            {revisions.map((r) => (
              <button
                key={r.revision}
                className={`chip rev-chip ${rev?.revision === r.revision ? "connected" : ""}`}
                onClick={() => setSelected(r.revision)}
              >
                r{r.revision} {r.kind}
              </button>
            ))}
          </div>
        )}
      </div>

      {mission.last_proposal && !rev && <ProposalSummary proposal={mission.last_proposal} />}
      {rev && <RevisionDetail rev={rev} mission={mission} robots={robots} isLatest={rev === latest} busy={busy}
        onApprove={onApprove} onReject={onReject} onEdit={onEdit} />}
    </section>
  );
}

function ProposalSummary({ proposal }) {
  return (
    <div className="interpretation">
      <strong>{proposal.summary}</strong>
      <small>
        disposition {proposal.disposition}
        {proposal.scope ? ` · scope ${proposal.scope}` : ""}
      </small>
      {proposal.mission_goals?.length > 0 && (
        <div className="goal-row">
          <span className="muted">Mission goals:</span>
          {proposal.mission_goals.map((g, i) => (
            <span className="chip" key={i}>{describeGoal(g)}</span>
          ))}
        </div>
      )}
      {proposal.stage_goals?.length > 0 && (
        <div className="goal-row">
          <span className="muted">Stage goals:</span>
          {proposal.stage_goals.map((g, i) => (
            <span className="chip" key={i}>{describeGoal(g)}</span>
          ))}
        </div>
      )}
      {proposal.issues?.length > 0 && <small>Issues: {proposal.issues.join("; ")}</small>}
    </div>
  );
}

function RevisionDetail({ rev, mission, robots, isLatest, busy, onApprove, onReject, onEdit }) {
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState([]);
  const canAct = isLatest && mission.status === "awaiting_approval";
  const tasksByRobot = useMemo(() => {
    const out = {};
    robots.forEach((r) => (out[r.robot_id] = []));
    rev.tasks.forEach((t) => {
      if (t.robot_id) (out[t.robot_id] ||= []).push(t);
    });
    return out;
  }, [rev, robots]);
  const bindings = [...new Set(rev.tasks.map((t) => t.binding_id))];

  function addEdit(edit) {
    setEdits((current) => [...current.filter((e) => !(e.op === edit.op && e.binding_id === edit.binding_id && e.task_id === edit.task_id && e.field === edit.field)), edit]);
  }

  return (
    <div className="revision">
      <ProposalSummary proposal={rev.proposal} />
      <div className="planner-record">
        <span className={`badge ${rev.planner.kind}`}>{rev.planner.label}</span>
        <small>
          {rev.kind} · status {rev.status.replaceAll("_", " ")} · hash {rev.proposal_hash}
          {rev.planner.latency_ms != null ? ` · ${Math.round(rev.planner.latency_ms)} ms` : ""}
          {rev.planner.repaired ? " · repaired once" : ""}
          {rev.planner.kind === "qwen" && rev.planner.first_pass_ok === false ? " · first pass invalid" : ""}
        </small>
      </div>

      <div className={`validation ${rev.validation.ok ? "ok" : "bad"}`}>
        <strong>Validation {rev.validation.ok ? "passed" : "failed"}</strong>
        <small>world v{rev.validation.world_version}</small>
        {rev.validation.findings.length > 0 && (
          <ul>
            {rev.validation.findings.map((f, i) => (
              <li key={i} className={f.severity}>
                <b>{f.code}</b> {f.task_id ? `[${f.task_id}] ` : ""}
                {f.message}
              </li>
            ))}
          </ul>
        )}
      </div>

      <table className="task-table">
        <thead>
          <tr>
            <th>Task</th>
            <th>Binding → robot</th>
            <th>Action</th>
            <th>After</th>
            <th>State</th>
          </tr>
        </thead>
        <tbody>
          {rev.tasks.map((t) => {
            const rt = rev.runtime?.[t.task_id];
            const fields = EDITABLE_FIELDS[t.action.kind] || {};
            return (
              <tr key={t.task_id}>
                <td>{t.task_id}</td>
                <td>
                  {t.binding_id} → <b>{t.robot_id || "none"}</b>
                </td>
                <td>
                  <code>{describeAction(t.action)}</code>
                  {editing &&
                    Object.entries(fields).map(([field, options]) => (
                      <select
                        key={field}
                        className="inline-select"
                        defaultValue={t.action[field]}
                        onChange={(e) => addEdit({ op: "set_param", task_id: t.task_id, field, value: e.target.value })}
                      >
                        {options.map((o) => (
                          <option key={o}>{o}</option>
                        ))}
                      </select>
                    ))}
                  {editing && (
                    <button className="link-button" onClick={() => addEdit({ op: "remove_task", task_id: t.task_id })}>
                      remove
                    </button>
                  )}
                </td>
                <td>{t.depends_on.join(", ") || "—"}</td>
                <td>
                  <span className={`state ${rt?.state || "planned"}`}>{rt?.state || "planned"}</span>
                  {rt && ["running", "accepted"].includes(rt.state) && (
                    <progress value={rt.progress} max="1" />
                  )}
                  {rt?.error && <small className="error-text"> {rt.error.code}: {rt.error.message}</small>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="per-robot">
        {Object.entries(tasksByRobot).map(([rid, tasks]) => (
          <div key={rid} className={`robot-lane ${tasks.length ? "" : "idle"}`}>
            <strong>{rid}</strong>
            {tasks.length ? (
              tasks.map((t) => (
                <span key={t.task_id} className={`lane-task ${rev.runtime?.[t.task_id]?.state || "planned"}`}>
                  {t.task_id} {t.skill}
                </span>
              ))
            ) : (
              <small>idle — no justified work</small>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <div className="edit-box">
          {bindings.map((b) => (
            <label key={b}>
              Robot for <b>{b}</b>
              <select
                defaultValue={rev.allocations[b] || ""}
                onChange={(e) => addEdit({ op: "set_robot", binding_id: b, robot_id: e.target.value })}
              >
                {robots.map((r) => (
                  <option key={r.robot_id}>{r.robot_id}</option>
                ))}
              </select>
            </label>
          ))}
          <small>{edits.length} pending edit(s). Edits create a new revision that must be validated and approved.</small>
        </div>
      )}

      {canAct && (
        <div className="action-row">
          <button
            className="primary"
            disabled={busy || !rev.validation.ok || rev.status !== "awaiting_approval"}
            onClick={() => onApprove(rev)}
            title={rev.validation.ok ? "Authorize exactly this revision" : "Validation must pass first"}
          >
            Approve r{rev.revision}
          </button>
          {editing ? (
            <>
              <button disabled={busy || !edits.length} onClick={() => onEdit(rev, edits).then(() => { setEditing(false); setEdits([]); })}>
                Submit edits
              </button>
              <button onClick={() => { setEditing(false); setEdits([]); }}>Discard edits</button>
            </>
          ) : (
            <button disabled={busy} onClick={() => setEditing(true)}>Edit</button>
          )}
          <button className="danger" disabled={busy} onClick={() => onReject(rev)}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
