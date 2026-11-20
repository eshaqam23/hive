// One plan revision as the operator sees it (R1/R4): header with the deterministic checks, the task
// table (what each robot was sent and where it stands), the latest report under each row, and the
// buttons that apply to the revision's state. Edit mode uses only the Executive's typed edit ops.
import React, { useMemo, useState } from "react";
import {
  LEGEND,
  REVISION_STATUS_TEXT,
  STATUS_META,
  checkSummary,
  describeWhat,
  formatTaskList,
  planSource,
  reportLine,
  rowFindings,
  taskLabel,
  taskStatus,
} from "./chatModel.js";
import { CopyButton, TONE_DOT, TONE_TEXT } from "./ui.jsx";

const STOPPABLE = new Set(["dispatched", "accepted", "running"]);
const MAX_EDITS = 16;

/* ------------------------------------------------------------------ status */

export function StatusChip({ status }) {
  return (
    <span className="inline-flex flex-col gap-1" data-status={status.key}>
      <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] font-medium ${TONE_TEXT[status.tone] || "text-muted"}`}>
        <span aria-hidden="true" className={`size-1.5 rounded-full ${TONE_DOT[status.tone] || "bg-muted"}`} />
        {status.label}
        {status.code && <span className="font-mono text-[12px]">{status.code}</span>}
      </span>
      {status.key === "in_progress" && (
        <span
          aria-label={`${Math.round(status.progress * 100)}% done`}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={Math.round(status.progress * 100)}
          className="block h-1 w-20 overflow-hidden rounded-full bg-raised"
          role="progressbar"
        >
          <span className="block h-full rounded-full bg-coral transition-[width] duration-300" style={{ width: `${status.progress * 100}%` }} />
        </span>
      )}
      {status.key === "blocked" && status.waitsOn.length > 0 && (
        <span className="whitespace-nowrap text-[12px] text-faint">waits on {formatTaskList(status.waitsOn)}</span>
      )}
    </span>
  );
}

export function Legend() {
  return (
    <ul aria-label="Status legend" className="flex flex-wrap gap-x-3 gap-y-1">
      {LEGEND.map((key) => (
        <li className="inline-flex items-center gap-1.5 text-[12px] text-faint" key={key}>
          <span aria-hidden="true" className={`size-1.5 rounded-full ${TONE_DOT[STATUS_META[key].tone]}`} />
          {STATUS_META[key].label}
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ edit draft */

const FIELD_LABEL = { location_id: "location", area_id: "area", route_id: "route", object_query: "object", object_id: "object id" };

// Object labels are open vocabulary, so the object picker offers, in this order and once each:
// the task's own label and the draft's (the order stays put while editing), labels this
// revision's tasks and goals name, the catalog's profiles, then labels of objects the robots
// have observed. Labels without a profile can be found, not carried.
const queryLabel = (x) => x?.object_query?.label;
function revisionLabels(rev, state) {
  const p = rev?.proposal || {};
  const goals = [...(p.mission_goals || []), ...(p.stage_goals || []), ...(state?.mission?.canonical_goals || [])];
  return [...(rev?.tasks || []).map((t) => queryLabel(t.action)), ...goals.map(queryLabel)];
}
function objectOptions(current, rev, state) {
  // own keys only: `in` would also match prototype names such as "constructor"
  const profiled = new Set(Object.keys(state?.catalog?.object_profiles || {}));
  const observed = (state?.world?.objects || []).map((o) => o?.label);
  const labels = [...current, ...revisionLabels(rev, state), ...profiled, ...observed].filter((l) => typeof l === "string" && l !== "");
  return [...new Set(labels)].map((k) => ({ value: k, label: `${k.replaceAll("_", " ")}${profiled.has(k) ? "" : " (no profile - cannot carry)"}` }));
}

function fieldOptions(field, catalog) {
  const locs = catalog?.locations || [];
  if (field === "location_id") return locs.map((l) => ({ value: l.location_id, label: l.label }));
  if (field === "area_id") return locs.filter((l) => l.kind === "area" || l.kind === "zone").map((l) => ({ value: l.location_id, label: l.label }));
  if (field === "route_id") return [{ value: "", label: "(none)" }, ...(catalog?.routes || []).map((r) => ({ value: r.route_id, label: r.route_id.replaceAll("_", " ") }))];
  return null;
}

/* the action fields an operator may change (everything but kind), with their current value */
function editableFields(action) {
  const fields = Object.keys(action || {}).filter((k) => k !== "kind" && action[k] !== null);
  if (action?.kind === "navigate_to" && !fields.includes("route_id")) fields.push("route_id");
  return fields;
}

const fieldText = (field, value) => (field === "object_query" ? (value?.label ?? "") : (value ?? ""));
const fieldValue = (field, text) => (field === "object_query" ? { label: text } : field === "route_id" && text === "" ? null : text);

const emptyDraft = () => ({ robots: {}, removed: [], params: {} });

/* the typed edit ops for a draft, compared with the revision it edits */
export function draftEdits(rev, draft) {
  const edits = [];
  Object.entries(draft.robots).forEach(([binding, robotId]) => {
    if (robotId && robotId !== rev.allocations?.[binding]) edits.push({ op: "set_robot", binding_id: binding, robot_id: robotId });
  });
  draft.removed.forEach((taskId) => edits.push({ op: "remove_task", task_id: taskId }));
  Object.entries(draft.params).forEach(([taskId, fields]) => {
    if (draft.removed.includes(taskId)) return;
    const task = rev.tasks.find((t) => t.task_id === taskId);
    Object.entries(fields).forEach(([field, value]) => {
      if (JSON.stringify(task?.action?.[field] ?? null) !== JSON.stringify(value ?? null)) edits.push({ op: "set_param", task_id: taskId, field, value });
    });
  });
  return edits;
}

/* ------------------------------------------------------------------ rows */

function Findings({ findings }) {
  if (!findings.length) return null;
  return (
    <ul className="mt-1.5 space-y-0.5">
      {findings.map((f, i) => (
        <li className={`text-[13px] ${f.severity === "error" ? "text-danger" : f.severity === "warning" ? "text-warn" : "text-faint"}`} key={i} role={f.severity === "error" ? "alert" : undefined}>
          <span className="font-mono text-[12px]">{f.code}</span> {f.message}
        </li>
      ))}
    </ul>
  );
}

function EnvelopeDetail({ command }) {
  const body = command?.envelope ?? null;
  const text = JSON.stringify(body ?? command ?? null, null, 2);
  return (
    <div className="mt-2 rounded-lg border border-line bg-sidebar p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[12px] text-faint">
          {body ? `Task envelope${command.attempt ? ` · attempt ${command.attempt}` : ""}${command.file ? ` · ${command.file}` : ""}` : "Not sent yet: the envelope is written when the task is dispatched."}
        </span>
        <CopyButton text={text} />
      </div>
      <pre className="max-h-72 overflow-auto font-mono text-[12px] leading-relaxed text-muted">{text}</pre>
    </div>
  );
}

function RowActions({ rt, task, status, mission, readOnly, busy, onStop, onReplan }) {
  if (readOnly || !rt) return null;
  if (STOPPABLE.has(rt.state) && ["executing", "recovering"].includes(mission.status)) {
    return (
      <button
        className="btn btn-secondary whitespace-nowrap px-3 py-1 text-[13px]"
        disabled={busy}
        onClick={() => onStop(task.task_id)}
        type="button"
      >
        Stop current
      </button>
    );
  }
  if (status.key === "failed" || status.key === "uncertain" || status.key === "rejected") {
    if (["completed", "blocked", "canceled", "rejected"].includes(mission.status)) return null;
    const can = ["awaiting_approval", "planner_error"].includes(mission.status);
    const why = can ? undefined : `Re-planning is only available while awaiting approval or after a planner error (now ${mission.status}).`;
    return (
      <span className="inline-flex flex-col items-end gap-0.5">
        <button className="btn btn-secondary whitespace-nowrap px-3 py-1 text-[13px]" disabled={!can || busy} onClick={onReplan} title={why} type="button">
          Replan
        </button>
        {!can && <span className="max-w-40 text-right text-[11px] leading-tight text-faint">{mission.status === "recovering" ? "Executive is re-planning" : `mission ${mission.status}`}</span>}
      </span>
    );
  }
  return null;
}

function PlanRow({ task, rev, state, mission, editing, draft, setDraft, readOnly, busy, onStop, onReplan }) {
  const [open, setOpen] = useState(false);
  const rt = rev.runtime?.[task.task_id];
  const status = taskStatus(task, rev);
  const report = reportLine(rt, state.world);
  const findings = rowFindings(rev, task);
  const robot = state.robots?.find((r) => r.robot_id === task.robot_id);
  const removed = draft.removed.includes(task.task_id);
  const command = task.agent_command;
  const line = command?.line ?? "";

  const setRobot = (robotId) => setDraft((d) => ({ ...d, robots: { ...d.robots, [task.binding_id]: robotId } }));
  const toggleRemove = () =>
    setDraft((d) => ({ ...d, removed: removed ? d.removed.filter((id) => id !== task.task_id) : [...d.removed, task.task_id] }));
  const setParam = (field, value) =>
    setDraft((d) => ({ ...d, params: { ...d.params, [task.task_id]: { ...(d.params[task.task_id] || {}), [field]: value } } }));
  const draftRobot = draft.robots[task.binding_id] ?? task.robot_id ?? "";
  const paramOf = (field) => (draft.params[task.task_id] && field in draft.params[task.task_id] ? draft.params[task.task_id][field] : task.action[field]);

  return (
    <>
      <tr
        className={`align-top ${removed ? "opacity-45" : ""} ${findings.some((f) => f.severity === "error") ? "bg-danger/5" : ""}`}
        data-task={task.task_id}
      >
        <td className="py-2.5 pl-4 pr-2 font-mono text-[13px] font-semibold text-ink">{taskLabel(task.task_id)}</td>
        <td className="px-2 py-2.5 text-[14px] text-ink">
          {editing ? (
            <div className="space-y-1.5">
              <span className="inline-flex items-center gap-1.5 rounded-md bg-raised px-2 py-0.5 font-mono text-[12px] text-faint" title="The skill kind is locked">
                <LockGlyph /> {task.action.kind}
              </span>
              {editableFields(task.action).map((field) => {
                const options = field === "object_query" ? objectOptions([fieldText(field, task.action[field]), fieldText(field, paramOf(field))], rev, state) : fieldOptions(field, state.catalog);
                const id = `edit-${rev.revision}-${task.task_id}-${field}`;
                return (
                  <label className="flex items-center gap-2 text-[13px] text-muted" htmlFor={id} key={field}>
                    <span className="w-16 flex-none text-faint">{FIELD_LABEL[field] || field}</span>
                    {options ? (
                      <select
                        aria-label={`${FIELD_LABEL[field] || field} for ${taskLabel(task.task_id)}`}
                        className="field min-h-8 min-w-0 flex-1 py-1 text-[13px]"
                        disabled={removed}
                        id={id}
                        onChange={(e) => setParam(field, fieldValue(field, e.target.value))}
                        value={fieldText(field, paramOf(field))}
                      >
                        {options.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        aria-label={`${FIELD_LABEL[field] || field} for ${taskLabel(task.task_id)}`}
                        className="field min-h-8 min-w-0 flex-1 py-1 font-mono text-[13px]"
                        disabled={removed}
                        id={id}
                        onChange={(e) => setParam(field, fieldValue(field, e.target.value))}
                        value={fieldText(field, paramOf(field))}
                      />
                    )}
                  </label>
                );
              })}
            </div>
          ) : (
            describeWhat(task.action, state.catalog)
          )}
        </td>
        <td className="px-2 py-2.5 text-[14px]">
          {editing ? (
            <label className="block">
              <select aria-label={`Robot for ${taskLabel(task.task_id)}`} className="field min-h-8 w-full py-1 text-[13px]" disabled={removed} onChange={(e) => setRobot(e.target.value)} value={draftRobot}>
                {!task.robot_id && <option value="">no robot</option>}
                {(state.robots || []).map((r) => (
                  <option key={r.robot_id} value={r.robot_id}>
                    {r.display_name} ({r.robot_id})
                  </option>
                ))}
              </select>
              <span className="mt-0.5 block font-mono text-[11px] text-faint">binding {task.binding_id}</span>
            </label>
          ) : task.robot_id ? (
            <span className="block">
              <span className="text-ink">→ {robot?.display_name ?? task.robot_id}</span>
              <span className="block font-mono text-[12px] text-faint">{task.robot_id}</span>
            </span>
          ) : (
            <span className="text-danger">no robot</span>
          )}
        </td>
        <td className="max-w-0 px-2 py-2.5">
          <button
            aria-expanded={open}
            className="block w-full truncate rounded-md text-left font-mono text-[12px] text-muted hover:text-ink"
            onClick={() => setOpen(!open)}
            title={line}
            type="button"
          >
            {line || "—"}
          </button>
        </td>
        <td className="px-2 py-2.5">
          <StatusChip status={status} />
        </td>
        <td className="py-2.5 pl-2 pr-4 text-right">
          {editing ? (
            <button
              aria-label={removed ? `Keep ${taskLabel(task.task_id)}` : `Remove ${taskLabel(task.task_id)}`}
              className="btn btn-secondary whitespace-nowrap px-3 py-1 text-[13px]"
              onClick={toggleRemove}
              type="button"
            >
              {removed ? "Keep" : "Remove"}
            </button>
          ) : (
            <RowActions busy={busy} mission={mission} onReplan={onReplan} onStop={onStop} readOnly={readOnly} rt={rt} status={status} task={task} />
          )}
        </td>
      </tr>
      {(report || findings.length > 0 || open) && (
        <tr className={removed ? "opacity-45" : ""}>
          <td />
          <td className="px-2 pb-3 pr-4" colSpan={5}>
            {report && (
              <p className={`text-[13px] leading-snug ${TONE_TEXT[report.tone]}`} data-report={task.task_id}>
                <span className="text-faint">↳ </span>
                {report.text}
              </p>
            )}
            <Findings findings={findings} />
            {open && <EnvelopeDetail command={command} />}
          </td>
        </tr>
      )}
    </>
  );
}

function LockGlyph() {
  return (
    <svg aria-hidden="true" className="size-3" viewBox="0 0 16 16">
      <rect fill="none" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.6" width="10" x="3" y="7" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

/* ------------------------------------------------------------------ card */

export default function PlanCard({ rev, state, mission, latest, readOnly = false, busy = false, act, header = null }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(emptyDraft);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [editError, setEditError] = useState(null);
  const checks = checkSummary(rev);
  const awaiting = !readOnly && latest && mission.status === "awaiting_approval";
  const approvable = awaiting && rev.status === "awaiting_approval";
  const edits = useMemo(() => draftEdits(rev, draft), [rev, draft]);

  const approve = () => act(`/revisions/${rev.revision}/approve`, { proposal_hash: rev.proposal_hash }, `Revision ${rev.revision} approved.`);
  const reject = async () => {
    const res = await act(`/revisions/${rev.revision}/reject`, { reason: reason.trim() }, `Revision ${rev.revision} rejected.`);
    if (res.ok) setRejecting(false);
  };
  const submitEdit = async () => {
    setEditError(null);
    const res = await act(`/revisions/${rev.revision}/edit`, { edits }, "Edit submitted as a new revision.");
    if (res.ok) {
      setEditing(false);
      setDraft(emptyDraft());
    } else setEditError(res.error);
  };
  const stop = (taskId) => act(`/tasks/${taskId}/cancel`, {}, `Stop sent to ${taskLabel(taskId)}.`);
  const replan = () => act("/mission/replan", {}, "Re-plan requested.");

  return (
    <section
      aria-label={`Plan revision ${rev.revision}`}
      className={`overflow-hidden rounded-2xl border bg-card ${approvable ? "border-coral/70 shadow-glow" : "border-line"} ${editing ? "ring-1 ring-coral/40" : ""}`}
      data-revision={rev.revision}
    >
      {header}
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold text-ink">
            Plan · rev {rev.revision} · {rev.tasks.length} task{rev.tasks.length === 1 ? "" : "s"} · {planSource(rev)}{" "}
            <span className={checks.ok ? "text-live" : "text-danger"}>
              → deterministic checks {checks.ok ? "passed" : "failed"} {checks.passed}/{checks.total}
            </span>
          </h3>
          <p className="mt-0.5 text-[12px] text-faint">
            {REVISION_STATUS_TEXT[rev.status] || rev.status}
            {rev.proposal?.scope ? ` · ${rev.proposal.scope.replaceAll("_", " ")}` : ""}
            {` · hash ${String(rev.proposal_hash).slice(0, 12)}`}
          </p>
        </div>
        <Legend />
      </header>

      {checks.planErrors.length > 0 && (
        <div className="border-b border-line px-4 py-2">
          <Findings findings={checks.planErrors} />
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left">
          <thead>
            <tr className="border-b border-line font-mono text-[11px] uppercase tracking-[0.08em] text-faint">
              <th className="py-2 pl-4 pr-2 font-normal" scope="col">Task</th>
              <th className="px-2 py-2 font-normal" scope="col">What</th>
              <th className="px-2 py-2 font-normal" scope="col">Sent to</th>
              <th className="w-[32%] px-2 py-2 font-normal" scope="col">Command to agent</th>
              <th className="px-2 py-2 font-normal" scope="col">Status</th>
              <th className="py-2 pl-2 pr-4 font-normal" scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/60">
            {rev.tasks.map((task) => (
              <PlanRow
                busy={busy}
                draft={draft}
                editing={editing}
                key={task.task_id}
                mission={mission}
                onReplan={replan}
                onStop={stop}
                readOnly={readOnly}
                rev={rev}
                setDraft={setDraft}
                state={state}
                task={task}
              />
            ))}
          </tbody>
        </table>
      </div>

      {awaiting && !editing && (
        <footer className="border-t border-line px-4 py-3">
          {rejecting ? (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                reject();
              }}
            >
              <label className="min-w-0 flex-1">
                <span className="sr-only">Reason for rejecting</span>
                <input
                  autoFocus
                  className="field min-h-8 w-full py-1.5 text-[14px]"
                  maxLength={280}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="One-line reason (optional)"
                  value={reason}
                />
              </label>
              <button className="btn btn-danger px-4 py-1.5 text-[14px]" disabled={busy} type="submit">
                Reject rev {rev.revision}
              </button>
              <button className="btn btn-secondary px-4 py-1.5 text-[14px]" onClick={() => setRejecting(false)} type="button">
                Back
              </button>
            </form>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn btn-primary px-4 py-1.5 text-[14px]"
                disabled={!approvable || !checks.ok || busy}
                onClick={approve}
                title={approvable && checks.ok ? undefined : "This revision failed its checks; edit it, re-plan or reject it."}
                type="button"
              >
                {rev.revision > 1 ? `Approve rev ${rev.revision}` : "Approve & run"}
              </button>
              <button className="btn btn-secondary px-4 py-1.5 text-[14px]" disabled={busy} onClick={() => setEditing(true)} type="button">
                {rev.revision > 1 ? "Edit" : "Edit plan"}
              </button>
              <button className="btn btn-secondary px-4 py-1.5 text-[14px]" disabled={busy} onClick={() => setRejecting(true)} type="button">
                Reject
              </button>
              <span className="ml-auto text-[13px] text-faint">
                {approvable && checks.ok ? "Nothing moves until you approve." : "This revision cannot run: its checks failed."}
              </span>
            </div>
          )}
        </footer>
      )}

      {editing && (
        <footer className="border-t border-line px-4 py-3">
          {editError && (
            <p className="mb-2 text-[13px] text-danger" role="alert">
              {editError}
            </p>
          )}
          {edits.length > MAX_EDITS && <p className="mb-2 text-[13px] text-warn">At most {MAX_EDITS} edits per revision.</p>}
          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn btn-primary px-4 py-1.5 text-[14px]"
              disabled={!edits.length || edits.length > MAX_EDITS || busy}
              onClick={submitEdit}
              type="button"
            >
              Submit {edits.length} edit{edits.length === 1 ? "" : "s"}
            </button>
            <button
              className="btn btn-secondary px-4 py-1.5 text-[14px]"
              onClick={() => {
                setEditing(false);
                setDraft(emptyDraft());
                setEditError(null);
              }}
              type="button"
            >
              Discard
            </button>
            <span className="ml-auto text-[13px] text-faint">A submitted edit becomes rev {rev.revision + 1} and needs approval.</span>
          </div>
        </footer>
      )}
    </section>
  );
}
