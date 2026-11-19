// "Executive thinking" (R1). Every line comes from state: the proposal's summary and issues, the
// planning_log entry, planner errors, the repair notes in the timeline, and the allocation. Nothing
// here writes reasoning of its own. Open while planning (with a shimmer); collapsed once approved.
import React, { useState } from "react";
import { Caret, latencyText } from "./ui.jsx";

const LINE_TONE = {
  error: "text-danger",
  issue: "text-warn",
  repair: "text-warn",
  summary: "text-ink",
};

export default function ThinkingCard({ thinking, defaultOpen = false }) {
  const [openState, setOpen] = useState(null);
  if (!thinking) return null;
  const open = thinking.live || (openState ?? defaultOpen);
  const latency = latencyText(thinking.latencyMs);
  const bodyId = `thinking-${thinking.label}-${thinking.lines.length}`.replace(/\W+/g, "-");

  return (
    <section aria-label="Executive thinking" className="rounded-2xl border border-line bg-sidebar/60">
      <button
        aria-controls={bodyId}
        aria-expanded={open}
        className="flex w-full items-start gap-2.5 rounded-2xl px-4 py-3 text-left hover:bg-hover/50"
        disabled={thinking.live}
        onClick={() => setOpen(!open)}
        type="button"
      >
        <span className="mt-1 text-faint">
          <Caret open={open} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className={`text-[14px] font-semibold ${thinking.live ? "shimmer-text" : ""}`} data-shimmer={thinking.live || undefined}>
              {thinking.live ? "Executive thinking…" : "Executive thinking"}
            </span>
            <span className="text-[13px] text-muted">{thinking.label}</span>
            {latency && <span className="font-mono text-[12px] text-faint">{latency}</span>}
          </span>
          {thinking.reads.length > 0 && (
            <span className="mt-0.5 block text-[13px] text-faint">read {thinking.reads.join(" · ")}</span>
          )}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-3.5" id={bodyId}>
          {thinking.lines.length ? (
            <ol className="ml-6 space-y-1 border-l border-line pl-4">
              {thinking.lines.map((line, i) => (
                <li className={`relative text-[14px] leading-relaxed ${LINE_TONE[line.kind] || "text-muted"}`} data-kind={line.kind} key={i}>
                  <span aria-hidden="true" className="absolute -left-[42px] w-6 text-right font-mono text-[12px] leading-[22px] text-faint">
                    {i + 1}
                  </span>
                  {line.text}
                </li>
              ))}
            </ol>
          ) : (
            <p className="ml-6 text-[14px] text-faint">{thinking.live ? "Waiting for the planner's proposal." : "The planner logged nothing for this step."}</p>
          )}
        </div>
      )}
    </section>
  );
}
