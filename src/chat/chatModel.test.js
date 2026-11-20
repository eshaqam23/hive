import { describe, expect, it } from "vitest";
import awaiting from "./__fixtures__/awaiting.json";
import blocked from "./__fixtures__/blocked.json";
import canceled from "./__fixtures__/canceled.json";
import complete from "./__fixtures__/complete.json";
import continuation from "./__fixtures__/continuation.json";
import editInvalid from "./__fixtures__/edit-invalid.json";
import health from "./__fixtures__/health.json";
import recovery from "./__fixtures__/recovery.json";
import uncertain from "./__fixtures__/uncertain.json";
import {
  checkSummary,
  clarificationRounds,
  completionSummary,
  composerBlock,
  describeWhat,
  durationText,
  formatResult,
  formatTaskList,
  isLiveRevision,
  messageTag,
  queuedMessages,
  threadMessages,
  likelyRobots,
  livePlannerLabel,
  matchPlanningLog,
  previousRevision,
  pendingThinking,
  planSource,
  plannerLabel,
  previewDiffRows,
  replanTrigger,
  repairNotes,
  reportLine,
  revisionDiff,
  revisionThinking,
  robotsIn,
  rowFindings,
  taskLabel,
  taskStatus,
} from "./chatModel.js";

const rev = (state, n) => state.mission.revisions.find((r) => r.revision === n);
const ctx = (state) => ({ mission: state.mission, timeline: state.timeline, robots: state.robots, planners: state.planners });

describe("task labels", () => {
  it("upper-cases ids and collapses runs of three or more", () => {
    expect(taskLabel("t1")).toBe("T1");
    expect(formatTaskList(["t3", "t1", "t2"])).toBe("T1–T3");
    expect(formatTaskList(["t1", "t2"])).toBe("T1, T2");
    expect(formatTaskList(["t1", "t2", "t3", "t5", "t7", "t8"])).toBe("T1–T3, T5, T7, T8");
    expect(formatTaskList([])).toBe("");
  });
});

describe("taskStatus", () => {
  const task = { task_id: "t2", depends_on: ["t1"] };
  const withRuntime = (state, extra = {}) => ({
    status: "executing",
    runtime: { t2: { state, progress: 0.4, error: null, ...extra } },
  });

  it("maps every runtime state", () => {
    const cases = {
      dispatched: "dispatched",
      accepted: "accepted",
      running: "in progress",
      canceling: "canceling",
      succeeded: "done",
      failed: "failed",
      rejected: "rejected",
      canceled: "canceled",
      uncertain: "uncertain",
      skipped: "skipped",
    };
    for (const [state, label] of Object.entries(cases)) {
      expect(taskStatus(task, withRuntime(state)).label).toBe(label);
    }
  });

  it("carries progress for running and the error code for failures", () => {
    expect(taskStatus(task, withRuntime("running")).progress).toBe(0.4);
    expect(taskStatus(task, withRuntime("running", { progress: 7 })).progress).toBe(1);
    expect(taskStatus(task, withRuntime("failed", { error: { code: "motor_stall", message: "x" } })).code).toBe("motor_stall");
  });

  it("says queued or blocked with the unmet dependencies before dispatch", () => {
    const r = rev(continuation, 2);
    expect(r.tasks.map((t) => taskStatus(t, r).key)).toEqual(["queued", "blocked", "blocked", "blocked"]);
    expect(taskStatus(r.tasks[1], r).waitsOn).toEqual(["t1"]);
    const deep = { task_id: "t4", depends_on: ["t1", "t2", "t3"] };
    expect(formatTaskList(taskStatus(deep, { status: "awaiting_approval" }).waitsOn)).toBe("T1–T3");
  });

  it("says not run for a superseded revision and passes an unknown state through", () => {
    const r = rev(editInvalid, 1);
    expect(taskStatus(r.tasks[0], r).key).toBe("not_run");
    const odd = taskStatus(task, withRuntime("teleporting"));
    expect([odd.key, odd.label]).toEqual(["unknown", "teleporting"]);
    expect(taskStatus(task, withRuntime("pending")).key).toBe("blocked");
  });
});

describe("plan header", () => {
  it("counts passed checks per task, with binding findings on their rows", () => {
    expect(checkSummary(rev(awaiting, 1))).toMatchObject({ passed: 2, total: 2, ok: true });
    const bad = rev(editInvalid, 2);
    expect(checkSummary(bad)).toMatchObject({ passed: 1, total: 2, ok: false, planErrors: [] });
    expect(rowFindings(bad, bad.tasks[0]).map((f) => f.code)).toEqual(["unsupported_assignment"]);
    expect(rowFindings(bad, bad.tasks[1])).toEqual([]);
  });

  it("names the plan source", () => {
    expect(planSource(rev(awaiting, 1))).toBe("Baseline");
    expect(planSource(rev(editInvalid, 2))).toBe("operator edit");
    expect(planSource(rev(recovery, 3))).toBe("recovery");
    expect(planSource({ kind: "initial", planner: { kind: "llm" } })).toBe("LLM proposed");
  });

  it("counts plan-level errors apart", () => {
    const r = {
      tasks: [{ task_id: "t1", binding_id: "a" }],
      validation: { ok: false, findings: [{ code: "cycle", severity: "error", message: "m" }] },
    };
    expect(checkSummary(r)).toMatchObject({ passed: 1, total: 1, planErrors: [{ code: "cycle" }] });
  });
});

describe("rows", () => {
  it("describes actions in catalog place names", () => {
    const tasks = rev(continuation, 2).tasks.map((t) => describeWhat(t.action, continuation.catalog));
    expect(tasks).toEqual([
      "Drive to Pickup zone via pickup corridor",
      "Pick up red_block_1",
      "Drive to Drop-off zone via pickup corridor",
      "Place red_block_1 at Drop-off zone",
    ]);
    expect(describeWhat({ kind: "inspect_area", area_id: "work_area", object_query: { label: "red_block" } }, awaiting.catalog)).toBe(
      "Look for red block in Work area",
    );
    expect(describeWhat({ kind: "capture_image", area_id: "pickup_zone" }, awaiting.catalog)).toBe("Photograph Pickup zone");
    expect(describeWhat({ kind: "scan_route", route_id: "pickup_corridor" }, null)).toBe("Scan pickup corridor");
    expect(describeWhat({ kind: "pick_object", object_query: { label: "blue_block" } }, null)).toBe("Pick up blue block");
    expect(describeWhat({ kind: "wave" }, null)).toBe("wave");
    expect(describeWhat(null, null)).toBe("");
  });

  it("reports the result, or the error with what was observed", () => {
    const r = rev(recovery, 2);
    expect(reportLine(r.runtime.t2, recovery.world).text).toBe("object_id: red_block_1 · held · location_id: pickup_zone");
    expect(reportLine(r.runtime.t3, recovery.world)).toEqual({ tone: "danger", text: "drive motor stalled; robot stopped in place" });
    expect(reportLine(rev(uncertain, 2).runtime.t4, uncertain.world).tone).toBe("warn");
    expect(reportLine(rev(canceled, 1).runtime.t1, canceled.world)).toEqual({
      tone: "muted",
      text: "Operator stopped t1; robot confirmed.",
    });
    const world = { reports: [{ attempt_id: "a1", kind: "route_blocked", entity_id: "pickup_corridor" }] };
    expect(reportLine({ state: "failed", attempt_ids: ["a1"], error: { code: "no_path", message: "blocked" } }, world).text).toBe(
      "blocked · observed route_blocked pickup_corridor",
    );
    expect(reportLine(undefined, world)).toBeNull();
    expect(reportLine({ state: "running", result: null }, world)).toBeNull();
    expect(formatResult({ found: [], ok: false, x: null, o: { a: 1 } })).toBe('found: none · not ok · o: {"a":1}');
  });
});

describe("thinking card", () => {
  it("builds lines only from summary, log, and allocations when nothing else exists", () => {
    const t = revisionThinking(rev(awaiting, 1), ctx(awaiting));
    expect(t.label).toBe("Baseline (no LLM)");
    expect(t.latencyMs).toBe(1.7);
    expect(t.reads).toEqual(["skill catalog", "3 robot states", "observed world v0"]);
    expect(t.lines.map((l) => l.text)).toEqual([
      "Summary: Gather missing evidence before transport.",
      "Initial proposal · plan · checks passed · 1 attempt",
      "Allocation: observer → camera_01",
      "Allocation: surveyor → lidar_01",
    ]);
    expect(t.lines.some((l) => l.kind === "issue" || l.kind === "repair" || l.kind === "error")).toBe(false);
  });

  it("adds issues, repair notes, errors, failovers and binding findings when they exist", () => {
    const planner = {
      kind: "llm",
      label: "LLM via Groq (llama)",
      provider_id: "groq",
      model: "llama-3.3",
      latency_ms: 4200,
      attempts: 2,
      repaired: true,
      first_pass_ok: false,
      input_tokens_est: 3100,
      errors: ["first answer was not JSON"],
      fallback_used: true,
      failovers: ["gemini"],
    };
    const r = {
      revision: 1,
      kind: "initial",
      planner,
      proposal: { summary: "Do it.", issues: ["Object position is a guess."] },
      allocations: { carrier: "gripper_01" },
      operator_constraints: {},
      world_version: 4,
      evidence_ids: ["e1"],
      validation: {
        findings: [{ code: "preference_overridden", severity: "info", message: "preferred camera_01 lacks pick_object", binding_id: "carrier" }],
      },
    };
    const mission = { revisions: [r], planning_log: [{ phase: "initial", planner, disposition: "plan", ok: true }] };
    const timeline = [
      { kind: "planning", message: "Requesting initial proposal from llm." },
      { kind: "planning", message: "Proposal rejected by schema; repair attempt. (bad json)" },
      { kind: "revision", message: "Revision 1 (initial, full) proposed by x; validation passed." },
    ];
    const planners = { llm: { provider: "groq", provider_label: "Groq", model: "llama-3.3" } };
    const t = revisionThinking(r, { mission, timeline, robots: [1, 2], planners });
    expect(t.label).toBe("Groq · llama-3.3");
    expect(t.reads).toEqual(["skill catalog", "2 robot states", "observed world v4 · 1 evidence", "~3100 tokens"]);
    expect(t.lines.map((l) => l.text)).toEqual([
      "Summary: Do it.",
      "Issue: Object position is a guess.",
      "Initial proposal · plan · checks passed · 2 attempts · repaired once · first pass invalid",
      "Planner error: first answer was not JSON",
      "Fallback provider answered after gemini",
      "Proposal rejected by schema; repair attempt. (bad json)",
      "Allocation: carrier → gripper_01 · preferred camera_01 lacks pick_object",
    ]);
  });

  it("pairs log entries with revisions and skips edits", () => {
    const { byRevision, trailing } = matchPlanningLog(recovery.mission);
    expect([...byRevision.keys()]).toEqual([1, 2, 3]);
    expect(trailing).toEqual([]);
    expect(matchPlanningLog(editInvalid.mission).byRevision.has(2)).toBe(false);
  });

  it("shows a live card while recovering and the planner error after one", () => {
    const live = pendingThinking(ctx(canceled));
    expect(live).toMatchObject({ live: true, label: "Baseline (no LLM)", lines: [] });
    expect(pendingThinking(ctx(awaiting))).toBeNull();
    expect(pendingThinking({ mission: null })).toBeNull();
    const mission = {
      status: "planner_error",
      planner_kind: "llm",
      revisions: [],
      planning_log: [{ phase: "error", planner: null, error: "provider timed out", attempts: 2 }],
      last_proposal: null,
    };
    const err = pendingThinking({ mission, timeline: [], robots: [], planners: { llm: { kind: "llm", configured: false } } });
    expect(err.live).toBe(false);
    expect(err.label).toBe("LLM (not bound)");
    expect(err.lines).toEqual([{ kind: "error", text: "Planner error: provider timed out · 2 attempts" }]);
  });

  it("shows a reject disposition's summary from last_proposal", () => {
    const planner = { kind: "baseline", label: "Baseline (no LLM)" };
    const mission = {
      status: "rejected",
      revisions: [],
      planning_log: [{ phase: "initial", planner, disposition: "reject", ok: null }],
      last_proposal: { disposition: "reject", summary: "No robot can fly.", issues: ["needs a drone"] },
    };
    const t = pendingThinking({ mission, timeline: [], robots: [], planners: {} });
    expect(t.lines.map((l) => l.text)).toEqual(["Summary: No robot can fly.", "Issue: needs a drone", "Initial proposal · reject"]);
  });

  it("finds repair notes for the latest request", () => {
    const tl = [
      { kind: "planning", message: "Requesting initial proposal from llm." },
      { kind: "planning", message: "Proposal failed deterministic validation; one repair attempt.", data: { findings: ["a", "b"] } },
    ];
    expect(repairNotes(tl).map((e) => e.message)).toEqual(["Proposal failed deterministic validation; one repair attempt."]);
    expect(repairNotes(tl, 5)).toEqual([]);
    expect(repairNotes([{ kind: "planning", message: "x" }])).toEqual([]);
  });

  it("labels planners from records and from live state", () => {
    expect(plannerLabel(null)).toBe("Planner");
    expect(plannerLabel({ kind: "llm", provider_id: "groq", model: "m" }, {})).toBe("groq · m");
    expect(livePlannerLabel("baseline", awaiting.planners)).toBe("Baseline (no LLM)");
    expect(livePlannerLabel("qwen", { llm: { kind: "llm", configured: true, provider: "g", model: "m" } })).toBe("g · m");
    expect(livePlannerLabel("other", {})).toBe("other");
  });
});

describe("replan diff", () => {
  it("pairs a recovery's renumbered tasks with the ones they continue", () => {
    const d = revisionDiff(rev(recovery, 2), rev(recovery, 3));
    expect(d.done.map((x) => x.task.task_id)).toEqual(["t1", "t2"]);
    expect(d.removed).toEqual([]);
    expect(d.added).toEqual([]);
    expect(d.kept.map((x) => `${x.prev.task_id}>${x.task.task_id}`)).toEqual(["t3>t1", "t4>t2"]);
  });

  it("finds a reassignment from an operator edit", () => {
    const d = revisionDiff(rev(editInvalid, 1), rev(editInvalid, 2));
    expect(d.reassigned.map((x) => [x.task.task_id, x.from, x.to])).toEqual([["t1", "camera_01", "gripper_01"]]);
    expect(d.kept.map((x) => x.task.task_id)).toEqual(["t2"]);
  });

  it("finds added, removed and changed tasks", () => {
    const prev = {
      tasks: [
        { task_id: "t1", skill: "navigate_to", robot_id: "g", depends_on: [], action: { kind: "navigate_to", location_id: "pickup_zone" } },
        { task_id: "t2", skill: "scan_route", robot_id: "l", depends_on: [], action: { kind: "scan_route", route_id: "pickup_corridor" } },
      ],
      runtime: {},
    };
    const cur = {
      tasks: [
        { task_id: "t1", skill: "navigate_to", robot_id: "g", depends_on: ["t2"], action: { kind: "navigate_to", location_id: "dropoff_zone" } },
        { task_id: "t2", skill: "capture_image", robot_id: "c", depends_on: [], action: { kind: "capture_image", area_id: "work_area" } },
      ],
    };
    const d = revisionDiff(prev, cur);
    expect(d.changed[0].fields).toEqual([
      { field: "location_id", from: "pickup_zone", to: "dropoff_zone" },
      { field: "depends_on", from: [], to: ["t2"] },
    ]);
    expect(d.added.map((x) => x.task.skill)).toEqual(["capture_image"]);
    expect(d.removed).toEqual([{ task: prev.tasks[1], state: null }]);
  });

  it("names what triggered the revision", () => {
    expect(replanTrigger(recovery.mission, rev(recovery, 3))).toBe("T3 motor_stall");
    expect(replanTrigger(editInvalid.mission, rev(editInvalid, 2))).toBe("operator edit of rev 1");
    expect(replanTrigger(continuation.mission, rev(continuation, 2))).toBe("rev 1 finished");
    expect(replanTrigger(awaiting.mission, rev(awaiting, 1))).toBeNull();
    const m = { revisions: [{ revision: 1, status: "superseded", tasks: [] }, { revision: 2, kind: "initial" }] };
    expect(replanTrigger(m, m.revisions[1])).toBe("re-plan request");
    const c = { revisions: [{ revision: 1, status: "finished", tasks: [] }, { revision: 2, kind: "clarification" }] };
    expect(replanTrigger(c, c.revisions[1])).toBe("your clarification answer");
  });
});

describe("completion", () => {
  it("summarises a completed mission with its recovered failure", () => {
    const c = completionSummary(complete.mission, complete.timeline);
    expect(c).toMatchObject({ title: "Mission complete", steps: 7, succeeded: 6, stepsText: "6 of 7 step(s) succeeded" });
    expect(c.failing.task.task_id).toBe("t3");
    expect(c.failing.revision).toBe(2);
    expect(c.duration).toBeGreaterThan(0);
  });

  it("highlights the uncertain step of a blocked mission", () => {
    const c = completionSummary(uncertain.mission, uncertain.timeline);
    expect(c.title).toBe("Mission blocked");
    expect(c.failing.task.task_id).toBe("t4");
    expect(c.reason).toMatch(/uncertain outcome/);
  });

  it("says all steps succeeded when they did", () => {
    expect(completionSummary(blocked.mission, blocked.timeline).stepsText).toBe("all 2 step(s) succeeded");
    expect(completionSummary(awaiting.mission, awaiting.timeline)).toBeNull();
  });

  it("titles an answered mission and keeps the answer as its reason", () => {
    const m = { ...awaiting.mission, status: "answered", revisions: [], outcome: { status: "answered", reason: "gripper_01 picks and places." } };
    expect(completionSummary(m, [])).toMatchObject({ title: "Answered", status: "answered", steps: 0, reason: "gripper_01 picks and places." });
    expect(composerBlock({ reachable: true, state: { mission: m } }).newRun).toBe(true);
  });

  it("shows the completion reason for a find-only mission or a cancel, not for a delivery", () => {
    const goal = (kind) => [{ kind, object_query: { label: "duck" }, area_id: "work_area", location_id: "dropoff_zone" }];
    const mk = (status, kind) => ({ ...complete.mission, status, canonical_goals: kind ? goal(kind) : null });
    expect(completionSummary(mk("completed", "object_located"), complete.timeline).showReason).toBe(true);
    expect(completionSummary(mk("completed", "object_at"), complete.timeline).showReason).toBe(false);
    expect(completionSummary(mk("completed", null), complete.timeline).showReason).toBe(false);
    expect(completionSummary(mk("canceled", "object_at"), complete.timeline).showReason).toBe(true);
  });

  it("splits answered clarification rounds into before and after the plan", () => {
    const r = (phase, answer) => ({ question: `${phase}?`, options: [], answer, phase });
    const m = { clarifications: [r("initial", "the red block"), r("clarification_answer", "the drop-off zone"), r("evaluation", "red_block_2"), r("evaluation", null)] };
    const { before, after } = clarificationRounds(m);
    expect(before.map((x) => x.answer)).toEqual(["the red block", "the drop-off zone"]);
    expect(after.map((x) => x.answer)).toEqual(["red_block_2"]);
    expect(clarificationRounds({})).toEqual({ before: [], after: [] });
    expect(clarificationRounds(null)).toEqual({ before: [], after: [] });
  });

  it("formats durations", () => {
    expect(durationText(301)).toBe("5 min 01 s");
    expect(durationText(4.4)).toBe("4 s");
    expect(durationText(3725)).toBe("1 h 02 min");
    expect(durationText(null)).toBe("—");
  });
});

describe("composer", () => {
  it("explains why a mission cannot be sent", () => {
    expect(composerBlock({ reachable: false, error: "timeout" }).reason).toMatch(/unreachable \(timeout\)/);
    expect(composerBlock({ reachable: true, state: null }).reason).toMatch(/Connecting/);
    // an active mission keeps the composer open for operator messages; only a stopping one blocks it
    expect(composerBlock({ reachable: true, state: awaiting })).toBeNull();
    expect(composerBlock({ reachable: true, state: { ...awaiting, mission: { ...awaiting.mission, status: "canceling" } } }).reason).toMatch(/Stopping/);
    expect(composerBlock({ reachable: true, state: complete })).toMatchObject({ newRun: true });
    const idle = { ...awaiting, mission: null };
    expect(composerBlock({ reachable: true, state: idle, health, planner: "baseline" })).toBeNull();
    expect(composerBlock({ reachable: true, state: idle, health, planner: "llm" }).reason).toMatch(/^Planner llm is down: executive role/);
    const down = { ...health, gateway: { ...health.gateway, connected: false }, bridge: { used: true, connected: false } };
    expect(composerBlock({ reachable: true, state: idle, health: down, planner: "baseline" }).reason).toMatch(
      /Robot gateway .* not connected.*; bridge is down/,
    );
  });

  it("finds robot ids in a timeline message", () => {
    expect(robotsIn("gripper_01 accepted t3.", awaiting.robots).map((r) => r.robot_id)).toEqual(["gripper_01"]);
    expect(robotsIn("Mission created.", awaiting.robots)).toEqual([]);
  });
});

describe("next-stage preview helpers", () => {
  const catalog = {
    robots: [
      { robot_id: "camera_01", skills: ["inspect_area", "capture_image", "navigate_to"], reachable_locations: ["camera_post"], observable_areas: ["work_area", "pickup_zone"], observable_routes: [] },
      { robot_id: "lidar_01", skills: ["scan_route", "navigate_to"], reachable_locations: ["lidar_post"], observable_areas: [], observable_routes: ["pickup_corridor"] },
      { robot_id: "gripper_01", skills: ["pick_object", "place_object", "navigate_to"], reachable_locations: ["dropoff_zone", "pickup_zone"], observable_areas: [], observable_routes: [] },
    ],
  };
  it("likelyRobots matches skill and reach, prefers the named robot when it fits, and hints nothing without a catalog", () => {
    expect(likelyRobots({ kind: "navigate_to", location_id: "pickup_zone" }, catalog)).toEqual(["gripper_01"]);
    expect(likelyRobots({ kind: "inspect_area", area_id: "work_area" }, catalog)).toEqual(["camera_01"]);
    expect(likelyRobots({ kind: "scan_route", route_id: "pickup_corridor" }, catalog)).toEqual(["lidar_01"]);
    expect(likelyRobots({ kind: "inspect_area", area_id: "dropoff_zone" }, catalog)).toEqual([]);
    expect(likelyRobots({ kind: "pick_object", object_query: { label: "x" } }, catalog, "camera_01")).toEqual(["gripper_01"]);
    expect(likelyRobots({ kind: "pick_object", object_query: { label: "x" } }, catalog, "gripper_01")).toEqual(["gripper_01"]);
    expect(likelyRobots({ kind: "pick_object" }, null, "gripper_01")).toEqual(["gripper_01"]);
    expect(likelyRobots({ kind: "pick_object" }, {}, null)).toEqual([]);
    expect(likelyRobots(null, catalog)).toEqual([]);
  });
  it("previewDiffRows lists changed, added and removed in that order and nothing for a missing or matching diff", () => {
    expect(previewDiffRows(null)).toEqual([]);
    expect(previewDiffRows({ matches_preview: true, kept: [{}], changed: [], added: [], removed: [] })).toEqual([]);
    const rows = previewDiffRows({
      changed: [{ preview_task: "t3", task: "t3", what: "w", differences: ["a: 1 -> 2", "b: 3 -> 4"] }, { preview_task: "t1", task: "t2", what: "nav()", differences: [] }],
      added: [{ task: "t5", what: "scan()" }],
      removed: [{ preview_task: "t4", what: "place()" }],
    });
    expect(rows.map((r) => r.kind)).toEqual(["changed", "changed", "added", "removed"]);
    expect(rows[0].text).toBe("T3 changed: a: 1 -> 2; b: 3 -> 4");
    expect(rows[1].text).toBe("T1 changed (now T2): nav()");
    expect(rows[2].text).toBe("Added T5: scan()");
    expect(rows[3].text).toBe("Dropped T4: place()");
  });
});

describe("operator messages", () => {
  const msg = (id, extra) => ({ id, text: `text ${id}`, mode: "now", status: "delivered", created_at: 10, delivered_at: 11, delivered_to_revision: null, delivered_as: "feedback", ...extra });

  it("lists queued messages oldest first and tags the rest", () => {
    const m = { operator_messages: [msg("msg-2", { status: "queued", created_at: 20 }), msg("msg-1", { status: "queued", created_at: 5 }), msg("msg-3")] };
    expect(queuedMessages(m).map((x) => x.id)).toEqual(["msg-1", "msg-2"]);
    expect(queuedMessages(null)).toEqual([]);
    expect(messageTag(msg("a", { status: "held" }))).toBe("held");
    expect(messageTag(msg("a", { delivered_to_revision: 3 }))).toBe("delivered to revision 3");
    expect(messageTag(msg("a"))).toBe("delivered - awaiting approval");
    expect(messageTag(msg("a", { note: "The planner answered." }))).toBe("delivered");
    expect(messageTag(msg("a", { status: "canceled" }))).toBe("canceled");
  });

  it("places held and delivered messages before the revision that consumed them", () => {
    const m = {
      revisions: [{ revision: 1, created_at: 1 }, { revision: 2, created_at: 30 }, { revision: 3, created_at: 60 }],
      operator_messages: [
        msg("msg-1", { delivered_to_revision: 2 }),
        msg("msg-2", { created_at: 40, delivered_at: 41 }), // delivered, no revision of its own: before the next one
        msg("msg-3", { status: "queued", delivered_at: null }), // a chip, not a bubble
        msg("msg-4", { status: "canceled" }),
        msg("msg-5", { delivered_as: "answer" }), // shown as the clarification answer already
        msg("msg-6", { status: "held", created_at: 70, delivered_at: null }),
      ],
    };
    const { before, tail } = threadMessages(m);
    expect(before.get(2).map((x) => x.id)).toEqual(["msg-1"]);
    expect(before.get(3).map((x) => x.id)).toEqual(["msg-2"]);
    expect(before.has(1)).toBe(false);
    expect(tail.map((x) => x.id)).toEqual(["msg-4", "msg-6"]); // a canceled one shows with its note
    const dropped = threadMessages({ ...m, operator_messages: [msg("msg-7", { status: "canceled", delivered_to_revision: 3 })] });
    expect(dropped.before.get(3).map((x) => x.id)).toEqual(["msg-7"]);
  });

  it("names the source and trigger of a feedback revision and spots a live one", () => {
    expect(planSource({ kind: "feedback", planner: { kind: "llm" } })).toBe("LLM proposed · from your message");
    expect(planSource({ kind: "feedback", planner: { kind: "baseline" } })).toBe("Baseline · from your message");
    const m = { status: "executing", active_revision: 1, revisions: [{ revision: 1, status: "executing", tasks: [] }, { revision: 2, kind: "feedback", status: "awaiting_approval", tasks: [] }] };
    expect(replanTrigger(m, m.revisions[1])).toBe("your message");
    expect(isLiveRevision(m, m.revisions[1])).toBe(true);
    expect(isLiveRevision(m, m.revisions[0])).toBe(false);
    expect(isLiveRevision({ ...m, status: "awaiting_approval" }, m.revisions[1])).toBe(false);
  });

  it("skips a live re-plan dropped at the stage end when naming what a revision follows", () => {
    const m = {
      revisions: [
        { revision: 1, status: "finished", tasks: [] },
        { revision: 2, kind: "feedback", status: "superseded", superseded_reason: "stage_ended", tasks: [] },
        { revision: 3, kind: "continuation", status: "awaiting_approval", tasks: [] },
        { revision: 4, kind: "feedback", status: "awaiting_approval", tasks: [] },
      ],
    };
    expect(previousRevision(m, m.revisions[2]).revision).toBe(1);
    expect(replanTrigger(m, m.revisions[2])).toBe("rev 1 finished");
    expect(previousRevision(m, m.revisions[3]).revision).toBe(3);
    expect(previousRevision(m, m.revisions[0])).toBeNull();
  });

  it("shows a never-dispatched task of a replaced revision as superseded", () => {
    const r = { status: "superseded", runtime: { t1: { state: "superseded" } } };
    expect(taskStatus({ task_id: "t1" }, r)).toMatchObject({ key: "superseded", label: "superseded", tone: "faint" });
  });
});
