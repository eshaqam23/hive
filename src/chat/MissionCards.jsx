// The thread's other cards: the operator's command, the Executive's reply to a question, the replan
// header with its diff, the clarification question and earlier rounds, the amber reason card, the
// completion card and the earlier missions list.
import React, { useState } from "react";
import { describeWhat, durationText, formatTaskList, revisionDiff, taskLabel } from "./chatModel.js";
import { clock } from "./ui.jsx";

export function UserBubble({ command, at }) {
  return (
    <div className="flex flex-col items-end gap-1">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-card px-4 py-2.5 text-[15px] leading-relaxed text-ink">{command}</p>
      {at && <time className="font-mono text-[12px] text-faint">{clock(at)}</time>}
    </div>
  );
}

/* An answered fleet question: the planner's reply, on the assistant's side of the thread. */
export function AssistantBubble({ text, at }) {
  return (
    <article aria-label="Executive reply" className="flex flex-col items-start gap-1">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl border border-line bg-raised px-4 py-2.5 text-[15px] leading-relaxed text-ink">{text}</p>
      {at && <time className="font-mono text-[12px] text-faint">{clock(at)}</time>}
    </article>
  );
}

/* Questions the planner already asked this mission and what the operator answered, oldest first. */
export function ClarificationRounds({ rounds, label = "Earlier questions" }) {
  if (!rounds?.length) return null;
  return (
    <ul aria-label={label} className="space-y-2">
      {rounds.map((r, i) => (
        <li className="space-y-1" key={i}>
          <p className="max-w-[85%] rounded-2xl border border-line px-4 py-2 text-[14px] text-muted">
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-warn">The planner asked </span>
            {r.question}
          </p>
          <p className="ml-auto w-fit max-w-[85%] rounded-2xl bg-card px-4 py-2 text-[14px] text-ink">{r.answer}</p>
        </li>
      ))}
    </ul>
  );
}

const show = (v) => (v && typeof v === "object" ? (Array.isArray(v) ? formatTaskList(v) || "none" : v.label ?? JSON.stringify(v)) : (v ?? "none"));

/* "Replan proposal · rev N · triggered by …" with the diff against rev N-1 */
export function ReplanHeader({ prev, rev, trigger, catalog }) {
  const diff = revisionDiff(prev, rev);
  const rows = [
    ...diff.added.map(({ task }) => ({ sign: "+", tone: "text-live", text: `${taskLabel(task.task_id)} ${describeWhat(task.action, catalog)} → ${task.robot_id ?? "no robot"}`, kind: "added" })),
    ...diff.removed.map(({ task, state }) => ({
      sign: "−",
      tone: "text-danger",
      text: `${taskLabel(task.task_id)} ${describeWhat(task.action, catalog)}${state ? ` (was ${state})` : ""}`,
      kind: "removed",
    })),
    ...diff.reassigned.map(({ task, from, to }) => ({ sign: "↻", tone: "text-warn", text: `${taskLabel(task.task_id)} reassigned ${from ?? "no robot"} → ${to ?? "no robot"}`, kind: "reassigned" })),
    ...diff.changed.flatMap(({ task, fields }) =>
      fields.map((f) => ({ sign: "~", tone: "text-warn", text: `${taskLabel(task.task_id)} ${f.field}: ${show(f.from)} → ${show(f.to)}`, kind: "changed" })),
    ),
  ];
  return (
    <div className="border-b border-line bg-coral/8 px-4 py-3">
      <p className="text-[14px] font-semibold text-ink">
        Replan proposal · rev {rev.revision}
        {trigger && <span className="font-normal text-muted"> · triggered by </span>}
        {trigger && <span className="font-mono text-[13px] text-coral">{trigger}</span>}
      </p>
      <p className="mt-0.5 text-[12px] text-faint">Changes against rev {prev.revision}</p>
      <ul aria-label={`Changes from rev ${prev.revision} to rev ${rev.revision}`} className="mt-2 space-y-0.5">
        {diff.done.length > 0 && (
          <li className="text-[13px] text-muted" data-diff="done">
            <span className="inline-block w-4 font-mono text-live">✓</span>
            {formatTaskList(diff.done.map((d) => d.task.task_id))} done in rev {prev.revision}, not repeated
          </li>
        )}
        {rows.map((r, i) => (
          <li className="text-[13px] text-muted" data-diff={r.kind} key={i}>
            <span className={`inline-block w-4 font-mono ${r.tone}`}>{r.sign}</span>
            {r.text}
          </li>
        ))}
        {diff.kept.length > 0 && (
          <li className="text-[13px] text-faint" data-diff="kept">
            <span className="inline-block w-4 font-mono">=</span>
            {diff.kept.length} task{diff.kept.length === 1 ? "" : "s"} unchanged
            {diff.kept.some((k) => k.prev.task_id !== k.task.task_id)
              ? ` (${diff.kept.map((k) => (k.prev.task_id === k.task.task_id ? taskLabel(k.task.task_id) : `${taskLabel(k.prev.task_id)}→${taskLabel(k.task.task_id)}`)).join(", ")})`
              : ""}
          </li>
        )}
        {!rows.length && !diff.done.length && !diff.kept.length && <li className="text-[13px] text-faint">No task changes.</li>}
      </ul>
    </div>
  );
}

export function ClarificationCard({ question, options = [], busy, act }) {
  const [answer, setAnswer] = useState("");
  const reply = (text) => act("/mission/clarify", { answer: text }, "Answer sent to the planner.");
  const send = async (e) => {
    e.preventDefault();
    const res = await reply(answer.trim());
    if (res.ok) setAnswer("");
  };
  return (
    <section aria-label="Clarification" className="rounded-2xl border border-warn/50 bg-card px-4 py-3.5">
      <p className="font-mono text-[12px] uppercase tracking-[0.08em] text-warn">The planner asks</p>
      <p className="mt-1 text-[15px] leading-relaxed text-ink">{question}</p>
      {options.length > 0 && (
        <div aria-label="Suggested answers" className="mt-3 flex flex-wrap gap-2" role="group">
          {options.map((opt, i) => (
            <button className="btn btn-secondary px-3 py-1 text-[14px]" disabled={busy} key={`${i}:${opt}`} onClick={() => reply(opt)} type="button">
              {opt}
            </button>
          ))}
        </div>
      )}
      <form className="mt-3 flex gap-2" onSubmit={send}>
        <label className="min-w-0 flex-1">
          <span className="sr-only">Your answer</span>
          <input className="field min-h-8 w-full py-1.5 text-[14px]" maxLength={500} onChange={(e) => setAnswer(e.target.value)} placeholder="Your answer" value={answer} />
        </label>
        <button className="btn btn-primary px-4 py-1.5 text-[14px]" disabled={!answer.trim() || busy} type="submit">
          Answer
        </button>
      </form>
    </section>
  );
}

/* planner_error / blocked / rejected: the reason, verbatim */
export function ReasonCard({ title, reason, children }) {
  return (
    <section aria-label={title} className="rounded-2xl border border-warn/50 bg-warn/8 px-4 py-3.5" role="status">
      <p className="text-[14px] font-semibold text-warn">{title}</p>
      {reason && <p className="mt-1 whitespace-pre-wrap text-[14px] leading-relaxed text-ink">{reason}</p>}
      {children && <div className="mt-3 flex flex-wrap gap-2">{children}</div>}
    </section>
  );
}

const EDGE = { completed: "border-l-live", blocked: "border-l-warn", canceled: "border-l-faint", rejected: "border-l-danger", planner_error: "border-l-danger", answered: "border-l-live" };

/* R3: "Mission complete · duration · all N step(s) succeeded", or the failed variant */
export function CompletionCard({ summary, catalog, onExport, exportLabel = "Export run", showReason = false }) {
  const failing = summary.failing;
  return (
    <section aria-label={summary.title} className={`rounded-2xl border border-l-4 border-line bg-card px-4 py-3.5 ${EDGE[summary.status] || ""}`} data-outcome={summary.status}>
      <p className="text-[15px] font-semibold text-ink">
        {summary.title} <span className="font-normal text-muted">· {summary.durationText} · {summary.stepsText}</span>
      </p>
      {showReason && summary.reason && <p className="mt-1 text-[14px] text-muted">{summary.reason}</p>}
      {failing && (
        <p className={`mt-2 rounded-lg px-3 py-2 text-[13px] ${summary.status === "completed" ? "bg-raised text-muted" : "bg-danger/10 text-danger"}`} data-failing={failing.task.task_id}>
          <span className="font-mono font-semibold">
            rev {failing.revision} · {taskLabel(failing.task.task_id)}
          </span>{" "}
          {describeWhat(failing.task.action, catalog)} — {failing.rt.state}
          {failing.rt.error?.code ? ` ${failing.rt.error.code}` : ""}
          {failing.rt.error?.message ? `: ${failing.rt.error.message}` : ""}
          {summary.status === "completed" ? " (recovered by a later revision)" : ""}
        </p>
      )}
      {onExport && (
        <button className="mt-3 text-[13px] font-medium text-coral underline-offset-4 hover:underline" onClick={onExport} type="button">
          {exportLabel}
        </button>
      )}
    </section>
  );
}

const OUTCOME_TONE = { completed: "text-live", blocked: "text-warn", canceled: "text-faint", rejected: "text-danger", planner_error: "text-danger", answered: "text-live" };

const EARLIER_SHOWN = 3;

export function EarlierMissions({ list, viewing, onView, onExport }) {
  const [all, setAll] = useState(false);
  if (!list.length) return null;
  const shown = all ? list : list.slice(0, EARLIER_SHOWN);
  return (
    <section aria-label="Earlier missions" className="rounded-2xl border border-line">
      <header className="flex items-center justify-between px-4 py-2.5">
        <h2 className="text-[14px] font-semibold text-ink">Earlier missions</h2>
        <span className="flex items-center gap-3 text-[13px] text-faint">
          {list.length} kept in this browser
          {list.length > EARLIER_SHOWN && (
            <button aria-expanded={all} className="font-medium text-muted hover:text-ink" onClick={() => setAll(!all)} type="button">
              {all ? "show fewer" : `show all ${list.length}`}
            </button>
          )}
        </span>
      </header>
      <ul className="divide-y divide-line border-t border-line">
        {shown.map((r) => (
          <li className={`flex items-center gap-3 px-4 py-2 ${viewing === r.id ? "bg-raised" : ""}`} key={r.id}>
            <button
              aria-current={viewing === r.id || undefined}
              className="min-w-0 flex-1 text-left disabled:cursor-default"
              disabled={!r.export}
              onClick={() => onView(viewing === r.id ? null : r.id)}
              title={r.export ? "View read-only" : "No export was captured for this mission"}
              type="button"
            >
              <span className="block truncate text-[14px] text-ink">{r.command}</span>
              <span className="block text-[12px] text-faint">
                <span className={OUTCOME_TONE[r.outcome] || ""}>{r.outcome}</span> · {durationText(r.duration_s)} · {r.revisions} revision{r.revisions === 1 ? "" : "s"}
                {r.finished_at ? ` · ${clock(r.finished_at)}` : ""}
              </span>
            </button>
            {r.export ? (
              <button className="flex-none text-[13px] font-medium text-coral hover:underline" onClick={() => onExport(r)} type="button">
                Export
              </button>
            ) : (
              <span className="flex-none text-[12px] text-faint">no export</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
