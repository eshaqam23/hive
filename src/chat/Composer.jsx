// The input bar. With no active mission it says "Describe a mission": Enter sends, Shift+Enter adds a
// line, and the planner chip shows the bound executive provider/model with a toggle to Baseline. While
// a mission is active the same box talks to it: "Send now" (Enter) re-plans with the message at once,
// "Queue" keeps it for the next planning step, and queued messages sit above the box as chips that can
// be sent now or removed. When the Executive or the planner is down the input is disabled and says why.
import React from "react";
import { SendIcon, Spinner } from "../shell/icons.jsx";
import { livePlannerLabel } from "./chatModel.js";

export function PlannerChip({ planner, planners, healthPlanners, onChange, disabled }) {
  const llm = planners?.llm;
  const llmHealth = healthPlanners?.llm;
  const llmReady = llmHealth ? llmHealth.ready : Boolean(llm?.configured);
  const llmWhy = llmHealth?.reason || llm?.problems?.[0] || "The LLM planner is not bound.";
  const options = [
    { kind: "llm", label: livePlannerLabel("llm", planners), ok: llmReady, why: llmWhy },
    { kind: "baseline", label: "Baseline", ok: true, why: null },
  ];
  return (
    <div aria-label="Planner" className="inline-flex items-center rounded-full border border-line-strong p-0.5" role="group">
      {options.map((o) => (
        <button
          aria-pressed={planner === o.kind}
          className={`max-w-56 truncate rounded-full px-3 py-1 text-[13px] font-medium ${
            planner === o.kind ? "bg-raised text-ink" : "text-faint hover:text-muted"
          } disabled:cursor-not-allowed disabled:opacity-50`}
          disabled={disabled || (!o.ok && planner !== o.kind)}
          key={o.kind}
          onClick={() => onChange(o.kind)}
          title={o.ok ? `Plan with ${o.label}` : o.why}
          type="button"
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

const short = (text) => (text.length > 60 ? `${text.slice(0, 57)}…` : text);

/* Messages waiting for the next planning step, each with Send now and a remove button. */
export function QueuedChips({ messages, busy, onSendNow, onRemove }) {
  if (!messages.length) return null;
  return (
    <ul aria-label="Queued messages" className="mb-2 flex flex-col gap-1.5">
      {messages.map((m) => (
        <li className="flex min-w-0 items-center gap-2 rounded-xl border border-line bg-raised px-3 py-1.5" data-queued={m.id} key={m.id}>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[14px] text-ink" title={m.text}>
              {m.text}
            </p>
            <p className="text-[12px] text-faint">Queued - delivered at next planning step</p>
          </div>
          <button
            aria-label={`Send now: ${short(m.text)}`}
            className="flex-none rounded-lg px-2 py-1 text-[13px] font-medium text-coral hover:bg-coral/10 disabled:opacity-40"
            disabled={busy}
            onClick={() => onSendNow(m.id)}
            type="button"
          >
            Send now
          </button>
          <button
            aria-label={`Remove queued message: ${short(m.text)}`}
            className="grid size-7 flex-none place-items-center rounded-lg text-[16px] leading-none text-faint hover:bg-card hover:text-ink disabled:opacity-40"
            disabled={busy}
            onClick={() => onRemove(m.id)}
            type="button"
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function Composer({
  text,
  setText,
  block,
  sending,
  planner,
  planners,
  healthPlanners,
  onPlanner,
  onSend,
  onNewRun,
  busy,
  messaging = false,
  queued = [],
  onSendQueued,
  onRemoveQueued,
}) {
  const blocked = Boolean(block);
  const canSend = !blocked && !sending && text.trim().length > 0;

  const submit = async (e, mode = "now") => {
    e?.preventDefault();
    if (!canSend) return;
    const ok = await onSend(text.trim(), mode);
    if (ok) setText("");
  };

  return (
    <form className="sticky bottom-0 mx-auto w-full max-w-3xl flex-none bg-panel px-4 pb-6 pt-3 sm:px-6 lg:static" onSubmit={submit}>
      {block && (
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1" role="status">
          <p className="text-[13px] text-warn" data-composer-block>
            {block.reason}
          </p>
          {block.newRun && (
            <button className="btn btn-secondary px-3 py-1 text-[13px]" disabled={busy} onClick={onNewRun} type="button">
              Start a new run
            </button>
          )}
        </div>
      )}
      {messaging && !blocked && <QueuedChips busy={busy} messages={queued} onRemove={onRemoveQueued} onSendNow={onSendQueued} />}
      <div className="rounded-2xl border border-line-strong bg-card p-3 shadow-float focus-within:ring-2 focus-within:ring-coral/50">
        <label className="sr-only" htmlFor="mission-input">
          {messaging ? "Message the running mission" : "Describe a mission"}
        </label>
        <textarea
          className="block max-h-48 min-h-[52px] w-full resize-none bg-transparent px-1 text-[15px] leading-relaxed text-ink placeholder:text-faint focus:outline-none disabled:cursor-not-allowed"
          disabled={blocked}
          id="mission-input"
          maxLength={messaging ? 500 : undefined}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={messaging ? "Message the planner: change the plan, add a step…" : "Describe a mission"}
          rows={2}
          value={text}
        />
        {messaging ? (
          <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
            <span className="mr-auto text-[12px] text-faint">Enter sends now · Shift+Enter new line</span>
            <button
              className="btn btn-secondary px-3 py-1.5 text-[14px]"
              disabled={!canSend}
              onClick={(e) => submit(e, "queue")}
              title="Keep it for the next planning step"
              type="button"
            >
              Queue
            </button>
            <button className="btn btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-[14px]" disabled={!canSend} title="Re-plan with this message now" type="submit">
              {sending ? <Spinner size={16} /> : <SendIcon size={16} />}
              Send now
            </button>
          </div>
        ) : (
          <div className="mt-2 flex items-center justify-between gap-3">
            <PlannerChip disabled={sending} healthPlanners={healthPlanners} onChange={onPlanner} planner={planner} planners={planners} />
            <div className="flex items-center gap-3">
              <span className="hidden text-[12px] text-faint sm:inline">Enter sends · Shift+Enter new line</span>
              <button aria-label="Send mission" className="grid size-9 place-items-center rounded-lg bg-coral text-coral-ink hover:bg-coral-hover disabled:opacity-40" disabled={!canSend} type="submit">
                {sending ? <Spinner size={18} /> : <SendIcon size={18} />}
              </button>
            </div>
          </div>
        )}
      </div>
    </form>
  );
}
