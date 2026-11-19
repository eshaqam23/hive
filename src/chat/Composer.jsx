// The input bar: "Describe a mission". Enter sends, Shift+Enter adds a line. The planner chip shows
// the bound executive provider/model with a toggle to Baseline. When the Executive or the planner is
// down the input is disabled and says why.
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

export default function Composer({ text, setText, block, sending, planner, planners, healthPlanners, onPlanner, onSend, onNewRun, busy }) {
  const blocked = Boolean(block);
  const canSend = !blocked && !sending && text.trim().length > 0;

  const submit = async (e) => {
    e?.preventDefault();
    if (!canSend) return;
    const ok = await onSend(text.trim());
    if (ok) setText("");
  };

  return (
    <form className="sticky bottom-0 mx-auto w-full max-w-3xl flex-none bg-panel px-6 pb-6 pt-3 lg:static" onSubmit={submit}>
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
      <div className="rounded-2xl border border-line-strong bg-card p-3 shadow-float focus-within:ring-2 focus-within:ring-coral/50">
        <label className="sr-only" htmlFor="mission-input">
          Describe a mission
        </label>
        <textarea
          className="block max-h-48 min-h-[52px] w-full resize-none bg-transparent px-1 text-[15px] leading-relaxed text-ink placeholder:text-faint focus:outline-none disabled:cursor-not-allowed"
          disabled={blocked}
          id="mission-input"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Describe a mission"
          rows={2}
          value={text}
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <PlannerChip disabled={sending} healthPlanners={healthPlanners} onChange={onPlanner} planner={planner} planners={planners} />
          <div className="flex items-center gap-3">
            <span className="hidden text-[12px] text-faint sm:inline">Enter sends · Shift+Enter new line</span>
            <button aria-label="Send mission" className="grid size-9 place-items-center rounded-lg bg-coral text-coral-ink hover:bg-coral-hover disabled:opacity-40" disabled={!canSend} type="submit">
              {sending ? <Spinner size={18} /> : <SendIcon size={18} />}
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}
