// What the mission chat shows, derived only from GET /api/exec/state. Nothing here invents text: every
// line comes from a field the Executive returned, so a reload repaints exactly the same thread.
import { TERMINAL_MISSION } from "../fleet/execStatus.js";

export const DEMO_COMMAND =
  "Find the red block in the work area, check the pickup corridor, and bring the block to the drop-off area.";

const num = (id) => Number(String(id).replace(/\D/g, "")) || 0;

/* t1 -> T1 */
export const taskLabel = (id) => String(id ?? "").toUpperCase();

/* ["t1","t2","t3","t5"] -> "T1–T3, T5": runs of three or more collapse to a range. */
export function formatTaskList(ids = []) {
  const sorted = [...new Set(ids)].sort((a, b) => num(a) - num(b));
  const parts = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && num(sorted[j + 1]) === num(sorted[j]) + 1) j += 1;
    if (j - i >= 2) parts.push(`${taskLabel(sorted[i])}–${taskLabel(sorted[j])}`);
    else for (let k = i; k <= j; k += 1) parts.push(taskLabel(sorted[k]));
    i = j + 1;
  }
  return parts.join(", ");
}

/* ------------------------------------------------------------------ task status */

export const STATUS_META = {
  queued: { label: "queued", tone: "faint" },
  blocked: { label: "blocked", tone: "faint" },
  dispatched: { label: "dispatched", tone: "muted" },
  accepted: { label: "accepted", tone: "muted" },
  in_progress: { label: "in progress", tone: "coral" },
  canceling: { label: "canceling", tone: "warn" },
  done: { label: "done", tone: "live" },
  failed: { label: "failed", tone: "danger" },
  rejected: { label: "rejected", tone: "danger" },
  canceled: { label: "canceled", tone: "faint" },
  uncertain: { label: "uncertain", tone: "warn" },
  skipped: { label: "skipped", tone: "faint" },
  not_run: { label: "not run", tone: "faint" },
};

export const LEGEND = ["queued", "blocked", "in_progress", "done", "failed", "canceled", "uncertain", "skipped"];

/* TaskRuntime.state (backend) -> the chat's status key */
const RUNTIME_KEY = {
  dispatched: "dispatched",
  accepted: "accepted",
  running: "in_progress",
  canceling: "canceling",
  succeeded: "done",
  failed: "failed",
  rejected: "rejected",
  canceled: "canceled",
  uncertain: "uncertain",
  skipped: "skipped",
};

/* revisions whose tasks may still run; any other revision without a runtime never ran */
const OPEN_REVISION = new Set(["awaiting_approval", "approved", "executing", "invalid"]);

/* Where one task of a revision stands: {key, label, tone, progress, code, waitsOn}. */
export function taskStatus(task, rev) {
  const rt = rev?.runtime?.[task.task_id];
  const key = rt ? RUNTIME_KEY[rt.state] : undefined;
  const make = (k, extra = {}) => ({ key: k, ...STATUS_META[k], progress: null, code: null, waitsOn: [], ...extra });
  if (key) {
    return make(key, {
      progress: key === "in_progress" ? Math.max(0, Math.min(1, Number(rt.progress) || 0)) : null,
      code: rt.error?.code ?? null,
    });
  }
  if (rt && rt.state !== "pending") return { ...make("queued"), key: "unknown", label: String(rt.state), tone: "muted" };
  if (!rt && !OPEN_REVISION.has(rev?.status)) return make("not_run");
  const waitsOn = (task.depends_on || []).filter((d) => rev?.runtime?.[d]?.state !== "succeeded");
  return waitsOn.length ? make("blocked", { waitsOn }) : make("queued");
}

/* ------------------------------------------------------------------ plan header */

/* Findings that belong on a row: its own task_id, or its binding's when the finding names no task. */
export function rowFindings(rev, task) {
  return (rev?.validation?.findings || []).filter(
    (f) => f.task_id === task.task_id || (!f.task_id && f.binding_id && f.binding_id === task.binding_id),
  );
}

/* "deterministic checks passed x/y": y tasks, x of them without an error finding. Errors that name
   no task or binding are counted apart, as plan-level errors. */
export function checkSummary(rev) {
  const tasks = rev?.tasks || [];
  const findings = rev?.validation?.findings || [];
  const ids = new Set(tasks.map((t) => t.task_id));
  const bindings = new Set(tasks.map((t) => t.binding_id));
  const passed = tasks.filter((t) => !rowFindings(rev, t).some((f) => f.severity === "error")).length;
  const planErrors = findings.filter(
    (f) => f.severity === "error" && !(f.task_id && ids.has(f.task_id)) && !(f.binding_id && bindings.has(f.binding_id)),
  );
  return { passed, total: tasks.length, ok: Boolean(rev?.validation?.ok), planErrors };
}

/* LLM proposed | Baseline | operator edit | recovery */
export function planSource(rev) {
  if (rev?.kind === "edit") return "operator edit";
  if (rev?.kind === "recovery") return "recovery";
  return rev?.planner?.kind === "llm" ? "LLM proposed" : "Baseline";
}

export const REVISION_STATUS_TEXT = {
  awaiting_approval: "awaiting approval",
  approved: "approved",
  executing: "executing",
  finished: "finished",
  superseded: "superseded",
  rejected: "rejected",
  invalid: "invalid",
  not_executable: "not executable",
};

/* ------------------------------------------------------------------ rows */

const locationLabel = (id, catalog) => catalog?.locations?.find((l) => l.location_id === id)?.label ?? id;
const plain = (id) => String(id ?? "").replaceAll("_", " ");
const objectName = (a) => a.object_id ?? (a.object_query ? plain(a.object_query.label) : "object");

/* WHAT: a short description of the action, in the catalog's own place names. */
export function describeWhat(action, catalog) {
  if (!action) return "";
  switch (action.kind) {
    case "navigate_to":
      return `Drive to ${locationLabel(action.location_id, catalog)}${action.route_id ? ` via ${plain(action.route_id)}` : ""}`;
    case "inspect_area":
      return `Look for ${action.object_query ? plain(action.object_query.label) : "objects"} in ${locationLabel(action.area_id, catalog)}`;
    case "capture_image":
      return `Photograph ${locationLabel(action.area_id, catalog)}`;
    case "scan_route":
      return `Scan ${plain(action.route_id)}`;
    case "pick_object":
      return `Pick up ${objectName(action)}`;
    case "place_object":
      return `Place ${objectName(action)} at ${locationLabel(action.location_id, catalog)}`;
    default:
      return plain(action.kind);
  }
}

function formatValue(key, value) {
  if (Array.isArray(value)) return `${key}: ${value.join(", ") || "none"}`;
  if (value === true) return key;
  if (value === false) return `not ${key}`;
  if (value && typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
  return `${key}: ${value}`;
}

export const formatResult = (result) =>
  Object.entries(result || {})
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => formatValue(k, v))
    .join(" · ");

/* The accepted observations a task's attempts reported (world.reports), as "kind entity". */
export function observationsFor(rt, world) {
  const attempts = new Set(rt?.attempt_ids || []);
  return (world?.reports || [])
    .filter((r) => attempts.has(r.attempt_id))
    .map((r) => `${r.kind} ${r.entity_id}`)
    .join("; ");
}

/* The latest report under a row: the result summary, or the error message plus what was observed. */
export function reportLine(rt, world) {
  if (!rt) return null;
  if (rt.error) {
    const seen = observationsFor(rt, world);
    const tone = rt.state === "uncertain" ? "warn" : rt.state === "canceled" ? "muted" : "danger";
    return { tone, text: `${rt.error.message}${seen ? ` · observed ${seen}` : ""}` };
  }
  const text = formatResult(rt.result);
  return text ? { tone: "muted", text } : null;
}

/* ------------------------------------------------------------------ thinking card */

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/* Pairs each planned revision with its planning_log entry (same PlannerRecord, in order). Edit
   revisions were never planned, so they have none. Returns {byRevision: Map, trailing: entries}. */
export function matchPlanningLog(mission) {
  const log = mission?.planning_log || [];
  const byRevision = new Map();
  let cursor = 0;
  for (const rev of mission?.revisions || []) {
    if (rev.kind === "edit") continue;
    for (let i = cursor; i < log.length; i += 1) {
      if (log[i].planner && sameJson(log[i].planner, rev.planner)) {
        byRevision.set(rev.revision, log[i]);
        cursor = i + 1;
        break;
      }
    }
  }
  return { byRevision, trailing: log.slice(cursor).filter((e) => ![...byRevision.values()].includes(e)) };
}

const isRepairNote = (e) => e.kind === "planning" && !String(e.message).startsWith("Requesting ");

/* Repair notes the Executive logged while planning revision n (between its request and its
   "Revision n (" entry), or, with n = null, since the last planning request. */
export function repairNotes(timeline = [], n = null) {
  let end = timeline.length;
  if (n !== null) {
    end = timeline.findIndex((e) => e.kind === "revision" && String(e.message).startsWith(`Revision ${n} (`));
    if (end < 0) return [];
  }
  let start = end - 1;
  while (start >= 0 && !(timeline[start].kind === "planning" && String(timeline[start].message).startsWith("Requesting "))) start -= 1;
  if (start < 0) return [];
  return timeline.slice(start + 1, end).filter(isRepairNote);
}

/* provider · model for an LLM record, the record's own label for the baseline */
export function plannerLabel(record, planners) {
  if (!record) return "Planner";
  if (record.kind !== "llm") return record.label || "Baseline (no LLM)";
  const llm = planners?.llm;
  const provider = (llm?.provider && llm.provider === record.provider_id && llm.provider_label) || record.provider_id || record.label;
  return [provider, record.model].filter(Boolean).join(" · ");
}

/* The label of a planner that is running now, from state.planners. */
export function livePlannerLabel(kind, planners) {
  const p = planners?.[kind === "qwen" ? "llm" : kind];
  if (!p) return kind || "Planner";
  if (p.kind !== "llm") return p.label || "Baseline (no LLM)";
  return p.configured ? [p.provider_label || p.provider, p.model].filter(Boolean).join(" · ") : "LLM (not bound)";
}

const PHASE_TEXT = {
  initial: "Initial",
  continuation: "Continuation",
  recovery: "Recovery",
  clarification_answer: "Clarification",
  error: "Planner error",
};

/* What the planner was given (context.py build_context), as named inputs. */
export function plannerReads({ phase, rev, robots, record }) {
  const reads = ["skill catalog", `${robots?.length ?? 0} robot states`];
  if (rev) reads.push(`observed world v${rev.world_version}${rev.evidence_ids?.length ? ` · ${rev.evidence_ids.length} evidence` : ""}`);
  if (phase === "recovery") reads.push("failure report");
  if (phase === "continuation") reads.push("stage history");
  if (phase === "clarification_answer" || rev?.kind === "clarification") reads.push("your answer");
  if (record?.input_tokens_est) reads.push(`~${record.input_tokens_est} tokens`);
  return reads;
}

function logLine(entry) {
  if (entry.phase === "error") {
    return `${PHASE_TEXT.error}: ${entry.error}${entry.attempts ? ` · ${entry.attempts} attempt${entry.attempts === 1 ? "" : "s"}` : ""}`;
  }
  const r = entry.planner || {};
  const checks = entry.ok === true ? " · checks passed" : entry.ok === false ? " · checks failed" : "";
  const attempts = r.attempts ? ` · ${r.attempts} attempt${r.attempts === 1 ? "" : "s"}` : "";
  return `${PHASE_TEXT[entry.phase] || entry.phase} proposal · ${entry.disposition}${checks}${attempts}${r.repaired ? " · repaired once" : ""}${r.first_pass_ok === false ? " · first pass invalid" : ""}`;
}

function recordLines(record) {
  if (!record) return [];
  const lines = (record.errors || []).map((e) => ({ kind: "error", text: `Planner error: ${e}` }));
  if (record.fallback_used) {
    lines.push({ kind: "log", text: `Fallback provider answered${record.failovers?.length ? ` after ${record.failovers.join(", ")}` : ""}` });
  }
  return lines;
}

const noteLine = (e) => ({
  kind: "repair",
  text: `${e.message}${e.data?.findings?.length ? ` ${e.data.findings.join("; ")}` : ""}`,
});

/* The thinking card of one planned revision: header facts and numbered lines, all from state. */
export function revisionThinking(rev, { mission, timeline, robots, planners }) {
  const entry = matchPlanningLog(mission).byRevision.get(rev.revision) || null;
  const record = rev.planner;
  const lines = [{ kind: "summary", text: `Summary: ${rev.proposal.summary}` }];
  (rev.proposal.issues || []).forEach((i) => lines.push({ kind: "issue", text: `Issue: ${i}` }));
  if (entry) lines.push({ kind: "log", text: logLine(entry) });
  lines.push(...recordLines(record));
  repairNotes(timeline, rev.revision).forEach((e) => lines.push(noteLine(e)));
  Object.entries(rev.allocations || {}).forEach(([binding, rid]) => {
    const why = (rev.validation?.findings || []).filter((f) => f.binding_id === binding && !f.task_id).map((f) => f.message);
    const forced = rev.operator_constraints?.[binding] ? " · operator constraint" : "";
    lines.push({ kind: "allocation", text: `Allocation: ${binding} → ${rid ?? "no robot"}${forced}${why.length ? ` · ${why.join("; ")}` : ""}` });
  });
  return {
    label: plannerLabel(record, planners),
    latencyMs: record?.latency_ms ?? null,
    reads: plannerReads({ phase: entry?.phase, rev, robots, record }),
    lines,
    live: false,
  };
}

/* A thinking card with no revision: planning now (live), or a planning_log entry that produced
   none (a clarify or reject disposition, or a planner error). */
export function pendingThinking({ mission, timeline, robots, planners }) {
  if (!mission) return null;
  const { trailing } = matchPlanningLog(mission);
  const planning = ["planning", "recovering"].includes(mission.status);
  const entry = trailing.at(-1) || null;
  if (!planning && !entry) return null;
  const record = entry?.planner || null;
  const lines = [];
  const proposal = mission.last_proposal;
  if (entry && proposal && entry.disposition && proposal.disposition === entry.disposition) {
    lines.push({ kind: "summary", text: `Summary: ${proposal.summary}` });
    (proposal.issues || []).forEach((i) => lines.push({ kind: "issue", text: `Issue: ${i}` }));
  }
  if (entry) lines.push({ kind: entry.phase === "error" ? "error" : "log", text: logLine(entry) });
  lines.push(...recordLines(record));
  repairNotes(timeline, null).forEach((e) => lines.push(noteLine(e)));
  return {
    label: record ? plannerLabel(record, planners) : livePlannerLabel(mission.planner_kind, planners),
    latencyMs: planning ? null : (record?.latency_ms ?? null),
    reads: plannerReads({ phase: entry?.phase, rev: null, robots, record }),
    lines,
    live: planning,
  };
}

/* ------------------------------------------------------------------ replan diff */

const stable = (value) => {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, stable(value[k])]));
  }
  return value;
};
const actionKey = (a) => JSON.stringify(stable(a));

function changedFields(a, b) {
  const keys = [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].filter((k) => k !== "kind");
  return keys
    .filter((k) => JSON.stringify(stable(a?.[k] ?? null)) !== JSON.stringify(stable(b?.[k] ?? null)))
    .map((k) => ({ field: k, from: a?.[k] ?? null, to: b?.[k] ?? null }));
}

/* What changed from prev to cur. Tasks pair up by an identical action first (a recovery renumbers
   its tasks), then by the same task_id and skill; a skill change is a removal plus an addition. */
export function revisionDiff(prev, cur) {
  const left = [...(prev?.tasks || [])];
  const pairs = [];
  const loose = [];
  for (const t of cur?.tasks || []) {
    const i = left.findIndex((p) => p.skill === t.skill && actionKey(p.action) === actionKey(t.action));
    if (i >= 0) pairs.push([left.splice(i, 1)[0], t]);
    else loose.push(t);
  }
  const added = [];
  for (const t of loose) {
    const i = left.findIndex((p) => p.task_id === t.task_id && p.skill === t.skill);
    if (i >= 0) pairs.push([left.splice(i, 1)[0], t]);
    else added.push(t);
  }
  const byNum = (x, y) => num(x.task.task_id) - num(y.task.task_id);
  const reassigned = [];
  const changed = [];
  const kept = [];
  for (const [p, t] of pairs) {
    const fields = changedFields(p.action, t.action);
    if (p.task_id === t.task_id && JSON.stringify(p.depends_on || []) !== JSON.stringify(t.depends_on || [])) {
      fields.push({ field: "depends_on", from: p.depends_on || [], to: t.depends_on || [] });
    }
    if (p.robot_id !== t.robot_id) reassigned.push({ prev: p, task: t, from: p.robot_id, to: t.robot_id });
    if (fields.length) changed.push({ prev: p, task: t, fields });
    if (p.robot_id === t.robot_id && !fields.length) kept.push({ prev: p, task: t });
  }
  const stateOf = (task) => prev?.runtime?.[task.task_id]?.state ?? null;
  return {
    added: added.map((task) => ({ task })).sort(byNum),
    // finished in the previous revision: carried forward as done, not dropped
    done: left.filter((task) => stateOf(task) === "succeeded").map((task) => ({ task })).sort(byNum),
    removed: left.filter((task) => stateOf(task) !== "succeeded").map((task) => ({ task, state: stateOf(task) })).sort(byNum),
    reassigned: reassigned.sort(byNum),
    changed: changed.sort(byNum),
    kept: kept.sort(byNum),
  };
}

const ENDED_BADLY = new Set(["failed", "rejected", "canceled", "uncertain"]);

/* The task in rev that ended badly, as {task, rt}, latest first. */
export function failingTask(rev) {
  const rows = (rev?.tasks || []).map((task) => ({ task, rt: rev.runtime?.[task.task_id] }));
  return rows.find((r) => r.rt && ENDED_BADLY.has(r.rt.state)) || null;
}

/* "triggered by …" for revision rev (rev > 1), from the revision before it. */
export function replanTrigger(mission, rev) {
  const prev = (mission?.revisions || []).find((r) => r.revision === rev.revision - 1);
  if (!prev) return null;
  if (rev.kind === "edit") return `operator edit of rev ${prev.revision}`;
  if (rev.kind === "clarification") return "your clarification answer";
  const bad = failingTask(prev);
  if (bad) return `${taskLabel(bad.task.task_id)} ${bad.rt.error?.code ?? bad.rt.state}`;
  if (prev.status === "finished") return `rev ${prev.revision} finished`;
  if (prev.status === "superseded") return "re-plan request";
  return rev.kind;
}

/* ------------------------------------------------------------------ completion */

/* When the mission ended: the terminal timeline entry, else the last task to finish. */
export function finishedAt(mission, timeline = []) {
  const terminal = [...timeline].reverse().find((e) => e.kind === mission?.status);
  if (terminal) return terminal.t;
  const ends = (mission?.revisions || []).flatMap((r) => Object.values(r.runtime || {}).map((rt) => rt.finished_at || 0));
  const latest = Math.max(0, ...ends);
  return latest || timeline.at(-1)?.t || null;
}

/* 301 -> "5 min 01 s" */
export function durationText(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

const COMPLETION_TITLE = {
  completed: "Mission complete",
  blocked: "Mission blocked",
  canceled: "Mission canceled",
  rejected: "Mission rejected",
  planner_error: "Planner error",
  answered: "Answered",
};

/* A completed find-only mission's reason says what was found; a delivery's is the generic sentence. */
const findsSomething = (mission) => (mission?.canonical_goals || []).some((g) => g.kind === "object_located");

/* The completion card: title, duration and how many steps succeeded, plus the step that failed. */
export function completionSummary(mission, timeline) {
  if (!mission || !TERMINAL_MISSION.has(mission.status)) return null;
  const ran = (mission.revisions || []).filter((r) => r.runtime && Object.keys(r.runtime).length);
  // a step is a task that was sent to a robot; skipped tasks never ran
  const steps = ran.flatMap((r) => r.tasks.map((t) => r.runtime[t.task_id]).filter((rt) => rt && rt.state !== "skipped"));
  const succeeded = steps.filter((rt) => rt.state === "succeeded").length;
  let failing = null;
  for (const r of [...ran].reverse()) {
    const bad = failingTask(r);
    if (bad) {
      failing = { revision: r.revision, ...bad };
      break;
    }
  }
  const end = finishedAt(mission, timeline);
  const duration = end && mission.created_at ? end - mission.created_at : null;
  const n = steps.length;
  const stepsText =
    n === 0 ? "no steps ran" : succeeded === n ? `all ${n} step(s) succeeded` : `${succeeded} of ${n} step(s) succeeded`;
  return {
    status: mission.status,
    title: COMPLETION_TITLE[mission.status] || mission.status,
    duration,
    durationText: durationText(duration),
    stepsText,
    steps: n,
    succeeded,
    failing,
    reason: mission.outcome?.reason ?? null,
    showReason: mission.status === "canceled" || (mission.status === "completed" && findsSomething(mission)),
  };
}

/* The questions already answered this mission, oldest first: those asked before any plan (the
   initial request, or an answer to an earlier question) and those asked once a plan had run. */
const BEFORE_PLAN = new Set(["initial", "clarification_answer"]);
export function clarificationRounds(mission) {
  const answered = (mission?.clarifications || []).filter((c) => c && c.answer != null);
  return {
    before: answered.filter((c) => BEFORE_PLAN.has(c.phase)),
    after: answered.filter((c) => !BEFORE_PLAN.has(c.phase)),
  };
}

/* ------------------------------------------------------------------ composer */

/* Whether a new mission can be sent now, and if not, why (mirrors health.py not_ready_reason). */
export function composerBlock({ reachable, error, state, health, planner }) {
  if (reachable === false) return { reason: `Executive unreachable (${error || "no answer"}). Start it with npm run hive.` };
  if (!state) return { reason: "Connecting to the Executive…" };
  const m = state.mission;
  if (m && !TERMINAL_MISSION.has(m.status)) return { reason: "A mission is in progress. The Executive runs one mission at a time." };
  if (m) return { reason: "This run is finished. Start a new run to send another mission.", newRun: true };
  if (health) {
    const reasons = [];
    if (health.gateway && health.gateway.connected === false) {
      reasons.push(`Robot gateway (${health.gateway.label}) is not connected: ${health.gateway.detail}`);
    }
    for (const link of ["bridge", "rosbridge"]) {
      if (health[link]?.used && !health[link]?.connected) reasons.push(`${link} is down`);
    }
    const p = health.planners?.[planner];
    if (p && !p.ready) reasons.push(`Planner ${planner} is down: ${p.reason}`);
    if (reasons.length) return { reason: reasons.join("; ") };
  }
  return null;
}

export const normalizePlanner = (kind) => (kind === "qwen" ? "llm" : kind);

/* Robot ids named in a timeline message, for the Agent reports rail. */
export function robotsIn(message, robots = []) {
  return robots.filter((r) => new RegExp(`\\b${r.robot_id}\\b`).test(String(message)));
}
