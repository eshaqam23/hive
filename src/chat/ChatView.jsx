// The mission chat: one Claude-like column (earlier missions, the operator's command, the
// Executive's thinking, the plan, the outcome) and a right rail (fleet, mini map, agent reports).
// Everything on screen is painted from GET /api/exec/state (polled by the shell every 500 ms while
// this tab is visible) or from the state a POST returned, so a browser refresh repaints exactly the
// same thread. Every failed POST lands in a toast with the server's own error text.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EXEC_API_URL, errorText, execGet, execPost } from "../executive/api.js";
import { TERMINAL_MISSION, isSimulated } from "../fleet/execStatus.js";
import { HiveMark } from "../shell/icons.jsx";
import {
  DEMO_COMMAND,
  clarificationRounds,
  completionSummary,
  composerBlock,
  finishedAt,
  livePlannerLabel,
  messageTag,
  messagesOpen,
  normalizePlanner,
  pendingThinking,
  previousRevision,
  queuedMessages,
  replanTrigger,
  revisionThinking,
  threadMessages,
} from "./chatModel.js";
import Composer from "./Composer.jsx";
import { loadEarlier, missionRecord, saveEarlier, withMission } from "./earlierMissions.js";
import {
  AssistantBubble,
  ClarificationCard,
  ClarificationRounds,
  CompletionCard,
  EarlierMissions,
  ReasonCard,
  ReplanHeader,
  UserBubble,
} from "./MissionCards.jsx";
import PlanCard from "./PlanCard.jsx";
import RightRail from "./RightRail.jsx";
import SimDrawer from "./SimDrawer.jsx";
import ThinkingCard from "./ThinkingCard.jsx";
import { ToastRegion, useToasts } from "./Toasts.jsx";

const ACTION_NAME = [
  [/\/approve$/, "Approve"],
  [/\/reject$/, "Reject"],
  [/\/edit$/, "Edit"],
  [/^\/tasks\/[^/]+\/cancel$/, "Stop current"],
  [/^\/mission\/replan$/, "Replan"],
  [/^\/mission\/clarify$/, "Answer"],
  [/^\/missions$/, "Send mission"],
  [/^\/mission\/messages$/, "Send message"],
  [/^\/mission\/messages\/[^/]+\/send-now$/, "Send now"],
  [/^\/mission\/messages\/[^/]+$/, "Remove message"],
  [/^\/reset$/, "Reset"],
  [/^\/faults$/, "Fault injection"],
];
const actionName = (path) => ACTION_NAME.find(([re]) => re.test(path))?.[1] ?? path;

/* DELETE /mission/messages/{id}: execPost's twin for the one call that is not a POST */
async function execDelete(path) {
  const response = await fetch(`${EXEC_API_URL}${path}`, { method: "DELETE" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(data, response.status));
  return data;
}

/* revisions that never ran and were replaced fold into one line */
const FOLDED = new Set(["superseded", "rejected", "invalid"]);
const ASKING = new Set(["awaiting_approval", "invalid"]);

function download(data, runId) {
  try {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `hive-run-${runId}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
  } catch {
    return false;
  }
}

function Hero({ onDemo }) {
  return (
    <div className="flex flex-col items-center px-6 pb-10 pt-[10vh] text-center">
      <HiveMark className="text-coral" size={40} />
      <h1 className="mt-5 font-display text-[clamp(30px,3.2vw,42px)] leading-tight tracking-display">What should the fleet do?</h1>
      <p className="mt-2 max-w-[52ch] text-[15px] leading-relaxed text-muted">
        Describe a mission. The Executive plans it, deterministic checks validate it, and nothing moves until you approve.
      </p>
      <button className="chip mt-6" onClick={onDemo} type="button">
        Try the demo mission
      </button>
    </div>
  );
}

/* An operator message in the thread: the user's bubble with where it went (held / delivered to rev N). */
function MessageBubble({ msg }) {
  return (
    <div className="flex flex-col items-end gap-1" data-message={msg.id}>
      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-card px-4 py-2.5 text-[15px] leading-relaxed text-ink">{msg.text}</p>
      <p className="flex flex-wrap justify-end gap-x-2 text-[12px] text-faint">
        <span className={`rounded-full border px-2 font-mono ${msg.status === "held" ? "border-warn/50 text-warn" : "border-line"}`} data-message-tag>
          {messageTag(msg)}
        </span>
        {msg.note && <span>{msg.note}</span>}
      </p>
    </div>
  );
}

const byMessage = (rev) => rev.status === "superseded" && rev.superseded_reason === "operator_message";

/* why a revision that stays open in the thread was superseded */
function supersededNote(rev) {
  if (rev.superseded_reason === "operator_message") {
    return `Superseded by your message${rev.superseded_by ? ` · rev ${rev.superseded_by} took over; its tasks that never started are superseded` : ""}`;
  }
  if (rev.superseded_reason === "stage_ended") return "Superseded: the running stage ended before this re-plan was approved.";
  return null;
}

function FoldedRevision({ rev, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div data-fold={rev.revision}>
      <button
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-xl border border-dashed border-line px-4 py-2 text-left text-[13px] text-faint hover:text-muted"
        onClick={() => setOpen(!open)}
        type="button"
      >
        <span className="flex-none whitespace-nowrap font-mono">rev {rev.revision}</span>
        <span className="min-w-0">
          · {byMessage(rev) ? "Superseded by your message" : rev.status} · {rev.tasks.length} task{rev.tasks.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto flex-none">{open ? "hide" : "show"}</span>
      </button>
      {open && <div className="mt-3 space-y-3">{children}</div>}
    </div>
  );
}

/* The thread of one mission. readOnly renders an earlier mission's export. */
export function Thread({ state, readOnly = false, busy = false, act, pending = null, sending = false, plannerLabel = null, onExport }) {
  const mission = state?.mission ?? null;
  const ctx = { mission, timeline: state?.timeline || [], robots: state?.robots || [], planners: state?.planners };
  const revisions = mission?.revisions || [];
  const latestNo = revisions.at(-1)?.revision;
  const live = readOnly ? null : pendingThinking(ctx);
  const summary = completionSummary(mission, ctx.timeline);
  const status = mission?.status;
  const rounds = clarificationRounds(mission);
  const asking = mission?.clarifications?.at(-1);
  const options = asking && asking.answer == null ? asking.options || [] : [];
  const messages = threadMessages(mission);

  if (!mission && !pending) return null;
  return (
    <div className="space-y-4" data-mission={mission?.mission_id}>
      <UserBubble at={mission?.created_at ?? pending?.at} command={mission?.command ?? pending?.command} />
      {mission && (
        <p className="text-center font-mono text-[12px] text-faint">
          {mission.mission_id} · {state.run_id} · {status}
          {readOnly ? " · read-only" : ""}
        </p>
      )}
      <ClarificationRounds rounds={rounds.before} />
      {!mission && sending && (
        <ThinkingCard thinking={{ label: plannerLabel || "Planner", latencyMs: null, reads: [], lines: [], live: true }} />
      )}

      {revisions.map((rev) => {
        const latest = rev.revision === latestNo;
        const ran = rev.runtime && Object.keys(rev.runtime).length > 0;
        const prev = previousRevision(mission, rev);
        const replan = latest && prev && ASKING.has(rev.status) && !readOnly;
        const body = (
          <>
            {rev.kind !== "edit" && (
              <ThinkingCard defaultOpen={latest && ASKING.has(rev.status)} thinking={revisionThinking(rev, ctx)} />
            )}
            <PlanCard
              act={act}
              busy={busy}
              header={replan ? <ReplanHeader catalog={state.catalog} prev={prev} rev={rev} trigger={replanTrigger(mission, rev)} /> : null}
              latest={latest}
              mission={mission}
              readOnly={readOnly}
              rev={rev}
              state={state}
            />
          </>
        );
        const said = (messages.before.get(rev.revision) || []).map((msg) => <MessageBubble key={msg.id} msg={msg} />);
        // a revision the operator's message replaced folds even while the re-plan is still coming
        if (!ran && ((!latest && FOLDED.has(rev.status)) || byMessage(rev))) {
          return (
            <React.Fragment key={rev.revision}>
              {said}
              <FoldedRevision rev={rev}>{body}</FoldedRevision>
            </React.Fragment>
          );
        }
        return (
          <React.Fragment key={rev.revision}>
            {said}
            <div className="space-y-3">
              {rev.status === "superseded" && supersededNote(rev) && <p className="text-[13px] text-faint">{supersededNote(rev)}</p>}
              {body}
            </div>
          </React.Fragment>
        );
      })}
      {messages.tail.map((msg) => (
        <MessageBubble key={msg.id} msg={msg} />
      ))}

      <ClarificationRounds label="Questions during the mission" rounds={rounds.after} />
      {live && <ThinkingCard thinking={live} />}

      {status === "clarification" && !readOnly && (
        <ClarificationCard act={act} busy={busy} options={options} question={mission.clarification_question} />
      )}
      {status === "clarification" && readOnly && <ReasonCard reason={mission.clarification_question} title="The planner asked" />}
      {status === "canceling" && <ReasonCard reason="Stopping the robots' current tasks; waiting for them to confirm." title="Canceling" />}
      {(status === "planner_error" || status === "blocked" || status === "rejected") && (
        <ReasonCard reason={mission.outcome?.reason} title={status === "planner_error" ? "Planner error" : status === "blocked" ? "Why the mission is blocked" : "Plan rejected"}>
          {status === "planner_error" && !readOnly && (
            <button className="btn btn-secondary px-4 py-1.5 text-[14px]" disabled={busy} onClick={() => act("/mission/replan", {}, "Asked the planner again.")} type="button">
              Try again
            </button>
          )}
        </ReasonCard>
      )}
      {status === "answered" && <AssistantBubble at={finishedAt(mission, ctx.timeline)} text={mission.outcome?.reason} />}
      {summary && (summary.steps > 0 || !["rejected", "planner_error", "answered"].includes(status)) && (
        <CompletionCard catalog={state.catalog} onExport={onExport} showReason={summary.showReason} summary={summary} />
      )}
    </div>
  );
}

const captureKey = (id, outcome, revisions) => `${id}|${outcome}|${revisions}`;

export default function ChatView({ exec, robots = [], showMap = true }) {
  const { state, health, reachable, error, accept } = exec;
  const { toasts, push, dismiss } = useToasts();
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState(null);
  const [text, setText] = useState("");
  const [plannerChoice, setPlannerChoice] = useState(null);
  const [earlier, setEarlier] = useState(loadEarlier);
  const [viewing, setViewing] = useState(null);
  const earlierRef = useRef(earlier);
  earlierRef.current = earlier;
  // one capture per (mission, outcome, revision count): "Try again" after a planner error can finish it later
  const captured = useRef(new Set(earlier.map((r) => captureKey(r.id, r.outcome, r.revisions))));
  const endRef = useRef(null);

  const planner = plannerChoice ?? normalizePlanner(state?.default_planner) ?? "baseline";
  const mission = state?.mission ?? null;
  const currentId = mission && state?.run_id ? `${state.run_id}:${mission.mission_id}` : null;

  const act = useCallback(
    async (path, body, okText, method = "POST") => {
      setBusy(true);
      try {
        const next = method === "DELETE" ? await execDelete(path) : await execPost(path, body);
        accept(next);
        if (okText) push({ text: okText });
        return { ok: true, state: next };
      } catch (err) {
        push({ tone: "error", title: `${actionName(path)} failed`, text: err.message });
        return { ok: false, error: err.message };
      } finally {
        setBusy(false);
      }
    },
    [accept, push],
  );

  /* a finished mission is kept in this browser with the run export captured now */
  useEffect(() => {
    if (!currentId || !TERMINAL_MISSION.has(mission.status)) return;
    const key = captureKey(currentId, mission.status, (mission.revisions || []).length);
    if (captured.current.has(key)) return;
    captured.current.add(key);
    const snapshot = state;
    execGet("/export")
      .catch(() => null)
      .then((exported) => {
        const next = withMission(earlierRef.current, missionRecord(snapshot, exported));
        const stored = saveEarlier(next);
        setEarlier(stored.length ? stored : next);
      });
  }, [currentId, mission, state]);

  /* follow the thread as it grows */
  const growth = `${currentId}:${mission?.status}:${mission?.revisions?.length ?? 0}:${mission?.operator_messages?.length ?? 0}:${pending ? 1 : 0}`;
  useEffect(() => {
    if (!viewing) endRef.current?.scrollIntoView?.({ block: "end" });
  }, [growth, viewing]);

  const messaging = messagesOpen(mission);

  const send = async (command, mode = "now") => {
    if (messaging) {
      setSending(true);
      const res = await act("/mission/messages", { text: command, mode }, mode === "queue" ? "Message queued for the next planning step." : null);
      setSending(false);
      return res.ok;
    }
    setSending(true);
    setPending({ command, at: Date.now() / 1000 });
    const res = await act("/missions", { command, planner }, null);
    setSending(false);
    setPending(null);
    return res.ok;
  };

  /* the demo chip fills the composer and hands it focus, so Enter sends it */
  const fillDemo = () => {
    setText(DEMO_COMMAND);
    document.getElementById("mission-input")?.focus();
  };

  const newRun = () => act("/reset", { scenario: state?.scenario || "unknown", faults: [] }, "New run started.");

  const exportCurrent = async () => {
    try {
      const data = await execGet("/export");
      if (!download(data, data.run_id ?? state?.run_id)) push({ tone: "error", title: "Export failed", text: "This browser could not save the file." });
    } catch (err) {
      push({ tone: "error", title: "Export failed", text: err.message });
    }
  };
  const exportEarlier = (record) => {
    if (!download(record.export, record.run_id)) push({ tone: "error", title: "Export failed", text: "This browser could not save the file." });
  };

  const block = composerBlock({ reachable, error, state, health, planner });
  const shownEarlier = useMemo(() => earlier.filter((r) => r.id !== currentId), [earlier, currentId]);
  const viewed = viewing ? earlier.find((r) => r.id === viewing && r.export) : null;
  const sim = state && isSimulated(state.gateway);

  return (
    <div className="lg:grid lg:h-full lg:grid-cols-[minmax(0,1fr)_minmax(300px,340px)]">
      <div className="flex min-h-full flex-col lg:h-full lg:min-h-0">
        {/* the thread scrolls; the composer stays below it and never covers the last card */}
        <div className="relative flex-1 lg:min-h-0 lg:overflow-y-auto">
          <div className="mx-auto w-full max-w-[860px] space-y-5 px-6 pb-6 pt-8">
            <EarlierMissions list={shownEarlier} onExport={exportEarlier} onView={setViewing} viewing={viewing} />

            {viewed ? (
              <>
                <div className="flex items-center justify-between rounded-xl bg-raised px-4 py-2 text-[13px]">
                  <span className="text-muted">Viewing an earlier mission · read-only</span>
                  <button className="font-medium text-coral hover:underline" onClick={() => setViewing(null)} type="button">
                    Back to the current run
                  </button>
                </div>
                <Thread act={act} onExport={() => exportEarlier(viewed)} readOnly state={viewed.export} />
              </>
            ) : (
              <>
                {reachable === false && !state && (
                  <ReasonCard reason={`${error || "No answer"}. Start it with npm run hive.`} title="Executive unreachable" />
                )}
                {!mission && !pending && state && <Hero onDemo={fillDemo} />}
                <Thread
                  act={act}
                  busy={busy}
                  onExport={exportCurrent}
                  pending={pending}
                  plannerLabel={livePlannerLabel(planner, state?.planners)}
                  sending={sending}
                  state={state}
                />
              </>
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer
          block={viewed ? { reason: "Viewing an earlier mission (read-only)." } : block}
          busy={busy}
          healthPlanners={health?.planners}
          messaging={messaging && !viewed}
          onNewRun={newRun}
          onRemoveQueued={(id) => act(`/mission/messages/${encodeURIComponent(id)}`, null, "Queued message removed.", "DELETE")}
          onSendQueued={(id) => act(`/mission/messages/${encodeURIComponent(id)}/send-now`, {}, null)}
          onPlanner={setPlannerChoice}
          onSend={send}
          planner={planner}
          planners={state?.planners}
          queued={queuedMessages(mission)}
          sending={sending}
          setText={setText}
          text={text}
        />
      </div>
      <RightRail robots={robots} showMap={showMap} state={state}>
        {sim && <SimDrawer act={act} busy={busy} state={state} />}
      </RightRail>
      <ToastRegion onDismiss={dismiss} toasts={toasts} />
    </div>
  );
}
