// @vitest-environment jsdom
// The mission chat against a fake Executive: every task status, approval bound to
// (revision, proposal_hash), typed edits and their validator errors, the recovery diff, Stop
// current, a browser refresh replaying the same thread, and a thinking card built only from fields
// that exist.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { EXEC_API_URL } from "../executive/api.js";
import { useExecutive } from "../executive/useExecutive.js";
import awaiting from "./__fixtures__/awaiting.json";
import blocked from "./__fixtures__/blocked.json";
import canceling from "./__fixtures__/canceling.json";
import clarification from "./__fixtures__/clarification.json";
import complete from "./__fixtures__/complete.json";
import editInvalid from "./__fixtures__/edit-invalid.json";
import health from "./__fixtures__/health.json";
import recovery from "./__fixtures__/recovery.json";
import running from "./__fixtures__/running.json";
import ChatView from "./ChatView.jsx";
import { DEMO_COMMAND, revisionThinking } from "./chatModel.js";
import { EARLIER_KEY } from "./earlierMissions.js";
import PlanCard from "./PlanCard.jsx";
import ThinkingCard from "./ThinkingCard.jsx";

const reply = (status, data) => ({ ok: status < 400, status, json: async () => data });
const clone = (x) => JSON.parse(JSON.stringify(x));

let server;

function fakeFetch(url, init = {}) {
  const u = String(url);
  if (!u.startsWith(EXEC_API_URL)) return Promise.resolve(reply(404, { error: "not found" }));
  const path = u.slice(EXEC_API_URL.length);
  const method = init.method ?? "GET";
  const body = init.body ? JSON.parse(init.body) : null;
  server.calls.push({ path, method, body });
  if (method === "POST" || method === "DELETE") {
    const handler = (method === "POST" ? server.post : server.del)[path];
    if (!handler) return Promise.resolve(reply(404, { ok: false, error: `No route ${path}` }));
    const res = handler(body);
    // a successful POST returns the new state; later polls must serve it, as the Executive would
    return res.json().then((data) => {
      if (res.ok && data && "mission" in data) server.state = data;
      return reply(res.status, data);
    });
  }
  if (path === "/state") return Promise.resolve(reply(200, server.state));
  if (path === "/health") return Promise.resolve(reply(200, health));
  if (path === "/export") return Promise.resolve(reply(200, { schema_version: "hive.run-export.v1", ...server.state }));
  if (path === "/faults") return Promise.resolve(reply(200, { available: { route_blocked: "Pickup corridor reports blocked" }, active: [] }));
  if (path === "/debug/truth") {
    server.truthTimes.push(Date.now());
    return Promise.resolve(reply(200, server.truth));
  }
  return Promise.resolve(reply(503, { ok: false, error: "no map yet" }));
}

/* the shell's hook, as App wires it for the Chat tab */
function Harness() {
  const exec = useExecutive({ stateMs: 500, pauseHidden: true });
  return <ChatView exec={exec} robots={[]} showMap={false} />;
}

const posts = (path) => server.calls.filter((c) => c.method === "POST" && c.path === path);
const row = (task) => document.querySelector(`[data-revision] tr[data-task="${task}"]`);
const rowIn = (rev, task) => document.querySelector(`[data-revision="${rev}"] tr[data-task="${task}"]`);

beforeEach(() => {
  localStorage.clear();
  server = { state: clone(awaiting), calls: [], post: {}, del: {}, truthTimes: [] };
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("plan table", () => {
  it("renders every task status", () => {
    const base = clone(running);
    const rev = base.mission.revisions[0];
    const proto = rev.tasks[0];
    const cases = [
      ["t1", null, [], "queued"],
      ["t2", null, ["t1", "t3", "t4"], "blocked"],
      ["t3", { state: "dispatched" }, [], "dispatched"],
      ["t4", { state: "accepted" }, [], "accepted"],
      ["t5", { state: "running", progress: 0.4 }, [], "in_progress"],
      ["t6", { state: "succeeded", result: { found: true } }, [], "done"],
      ["t7", { state: "failed", error: { code: "motor_stall", message: "drive motor stalled" } }, [], "failed"],
      ["t8", { state: "canceling" }, [], "canceling"],
      ["t9", { state: "canceled", error: { code: "operator_stop", message: "Stopped by the operator" } }, [], "canceled"],
      ["t10", { state: "uncertain", error: { code: "place_unconfirmed", message: "placement not confirmed" } }, [], "uncertain"],
      ["t11", { state: "skipped" }, [], "skipped"],
      ["t12", { state: "rejected", error: { code: "busy", message: "robot busy" } }, [], "rejected"],
    ];
    rev.tasks = cases.map(([id, , deps]) => ({ ...proto, task_id: id, depends_on: deps }));
    rev.runtime = Object.fromEntries(
      cases.filter(([, rt]) => rt).map(([id, rt]) => [id, { task_id: id, revision: 1, robot_id: "camera_01", attempt_ids: [], progress: null, result: null, error: null, ...rt }]),
    );
    render(<PlanCard act={vi.fn()} latest mission={base.mission} rev={rev} state={base} />);

    for (const [id, , , key] of cases) {
      expect(within(row(id)).getByText((_, el) => el?.dataset?.status === key), `${id} is ${key}`).toBeTruthy();
    }
    expect(within(row("t2")).getByText("waits on T1, T3, T4")).toBeTruthy();
    expect(within(row("t5")).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40");
    expect(within(row("t7")).getByText("motor_stall")).toBeTruthy();
    // the latest report under the row: the error message, or the result summary
    expect(document.querySelector('[data-report="t7"]').textContent).toContain("drive motor stalled");
    expect(document.querySelector('[data-report="t6"]').textContent).not.toBe("");
    // running rows can be stopped; the failed row offers Replan, disabled while executing
    expect(within(row("t5")).getByRole("button", { name: "Stop current" })).toBeTruthy();
    expect(within(row("t7")).getByRole("button", { name: "Replan" }).disabled).toBe(true);
    // the command cell expands to the full envelope with a Copy button
    fireEvent.click(within(row("t1")).getByRole("button", { name: proto.agent_command.line }));
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(screen.getByText(/"schema_version": "hive.task-envelope.v1"/)).toBeTruthy();
  });
});

describe("approval", () => {
  it("Approve sends the revision and its proposal_hash", async () => {
    server.post["/revisions/1/approve"] = () => reply(200, running);
    render(<Harness />);
    const approve = await screen.findByRole("button", { name: "Approve & run" });
    expect(screen.getByText("Nothing moves until you approve.")).toBeTruthy();
    fireEvent.click(approve);
    await waitFor(() => expect(posts("/revisions/1/approve")).toHaveLength(1));
    expect(posts("/revisions/1/approve")[0].body).toEqual({ proposal_hash: awaiting.mission.revisions[0].proposal_hash });
    await waitFor(() => expect(within(row("t1")).getByText("in progress")).toBeTruthy());
  });

  it("shows a failed POST as a toast with the server's error text", async () => {
    server.post["/revisions/1/approve"] = () => reply(409, { ok: false, error: "Proposal hash does not match this revision (stale approval).", detail: null });
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve & run" }));
    const toast = await screen.findByRole("alert");
    expect(toast.textContent).toContain("Approve failed");
    expect(toast.textContent).toContain("Proposal hash does not match this revision (stale approval).");
  });
});

describe("edit plan", () => {
  it("sends typed ops and shows the validator error on the row", async () => {
    server.post["/revisions/1/edit"] = () => reply(200, editInvalid);
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit plan" }));
    // the skill kind is locked; robot and params are editable
    expect(within(row("t1")).getByTitle("The skill kind is locked").textContent).toContain("inspect_area");
    fireEvent.change(within(row("t1")).getByLabelText("Robot for T1"), { target: { value: "gripper_01" } });
    fireEvent.change(within(row("t1")).getByLabelText("object for T1"), { target: { value: "blue_block" } });
    fireEvent.click(within(row("t2")).getByRole("button", { name: "Remove T2" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit 3 edits" }));

    await waitFor(() => expect(posts("/revisions/1/edit")).toHaveLength(1));
    expect(posts("/revisions/1/edit")[0].body).toEqual({
      edits: [
        { op: "set_robot", binding_id: "observer", robot_id: "gripper_01" },
        { op: "remove_task", task_id: "t2" },
        { op: "set_param", task_id: "t1", field: "object_query", value: { label: "blue_block" } },
      ],
    });
    // rev 2 failed its checks: the finding sits on the row it belongs to and approval is off
    const finding = await waitFor(() => within(rowIn(2, "t1").nextElementSibling).getByRole("alert"));
    expect(finding.textContent).toContain("gripper_01 cannot perform binding 'observer': lacks skill inspect_area");
    expect(screen.getByRole("button", { name: "Approve rev 2" }).disabled).toBe(true);
    expect(screen.getByText(/deterministic checks failed 1\/2/)).toBeTruthy();
  });

  it("keeps the editor open with the server's error when the edit is refused", async () => {
    server.post["/revisions/1/edit"] = () =>
      reply(400, { ok: false, error: "Edited plan is not a valid proposal: 1 validation error for PlanProposal", detail: null });
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit plan" }));
    fireEvent.change(within(row("t1")).getByLabelText("area for T1"), { target: { value: "dropoff_zone" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit 1 edit" }));
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2));
    const texts = screen.getAllByRole("alert").map((a) => a.textContent);
    expect(texts.filter((t) => t.includes("Edited plan is not a valid proposal")).length).toBe(2); // inline + toast
    expect(posts("/revisions/1/edit")[0].body.edits).toEqual([{ op: "set_param", task_id: "t1", field: "area_id", value: "dropoff_zone" }]);
    expect(screen.getByRole("button", { name: "Submit 1 edit" })).toBeTruthy();
  });

  it("keeps an open-vocabulary object selected and marks labels without a profile", async () => {
    const st = clone(awaiting);
    st.mission.revisions[0].tasks[0].action.object_query = { label: "mystery_crate" };
    st.mission.revisions[0].proposal.mission_goals = [{ kind: "object_located", object_query: { label: "plush_toy" } }];
    st.world.objects = [{ object_id: "coffee_mug_1", label: "coffee_mug", location_id: "work_area" }];
    server.state = st;
    server.post["/revisions/1/edit"] = () => reply(200, editInvalid);
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit plan" }));
    const select = within(row("t1")).getByLabelText("object for T1");
    expect(select.value).toBe("mystery_crate");
    const options = [...select.options].map((o) => [o.value, o.textContent]);
    expect(options[0]).toEqual(["mystery_crate", "mystery crate (no profile - cannot carry)"]);
    expect(options.map((o) => o[0])).toEqual(["mystery_crate", "plush_toy", "red_block", "blue_block", "green_block", "coffee_mug"]);
    expect(options.find((o) => o[0] === "red_block")[1]).toBe("red block");
    expect(options.find((o) => o[0] === "coffee_mug")[1]).toBe("coffee mug (no profile - cannot carry)");

    fireEvent.change(select, { target: { value: "red_block" } });
    expect(select.value).toBe("red_block");
    expect(select.options[0].value).toBe("mystery_crate"); // order stays put while editing
    fireEvent.click(screen.getByRole("button", { name: "Submit 1 edit" }));
    await waitFor(() => expect(posts("/revisions/1/edit")).toHaveLength(1));
    expect(posts("/revisions/1/edit")[0].body).toEqual({
      edits: [{ op: "set_param", task_id: "t1", field: "object_query", value: { label: "red_block" } }],
    });
  });

  it("treats a label named like an Object prototype key as having no profile", async () => {
    const st = clone(awaiting);
    st.mission.revisions[0].tasks[0].action.object_query = { label: "constructor" };
    server.state = st;
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit plan" }));
    const select = within(row("t1")).getByLabelText("object for T1");
    expect(select.value).toBe("constructor");
    expect(select.options[0].textContent).toBe("constructor (no profile - cannot carry)");
  });

  it("marks profiled labels heavier than the gripper payload as too heavy", async () => {
    const st = clone(awaiting);
    st.catalog.object_profiles.rc_car = { label: "rc_car", mass_kg: 1.2, description: "remote-control toy car" };
    st.catalog.object_profiles.duck = { label: "duck", mass_kg: 0.05, description: "rubber duck" };
    st.catalog.object_profiles.cone = { label: "cone", mass_kg: 0.25, description: "exactly the payload" };
    st.mission.revisions[0].tasks[0].action.object_query = { label: "rc_car" };
    server.state = st;
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit plan" }));
    const select = within(row("t1")).getByLabelText("object for T1");
    const text = (v) => [...select.options].find((o) => o.value === v).textContent;
    expect(select.options[0].textContent).toBe("rc car (too heavy - cannot carry)");
    expect(text("duck")).toBe("duck");
    expect(text("cone")).toBe("cone"); // equal to the payload is allowed
    expect(text("red_block")).toBe("red block");
  });

  it("does not call anything too heavy while no robot reports a payload", async () => {
    const st = clone(awaiting);
    st.catalog.object_profiles.rc_car = { label: "rc_car", mass_kg: 1.2, description: "toy car" };
    st.catalog.robots = st.catalog.robots.map((r) => ({ ...r, payload_limit_kg: 0 }));
    st.robots = st.robots.map((r) => ({ ...r, payload_limit_kg: 0 }));
    st.mission.revisions[0].tasks[0].action.object_query = { label: "rc_car" };
    server.state = st;
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit plan" }));
    expect(within(row("t1")).getByLabelText("object for T1").options[0].textContent).toBe("rc car");
  });
});

describe("recovery", () => {
  it("shows the replan proposal with its trigger and the diff against the previous revision", async () => {
    server.state = clone(recovery);
    render(<Harness />);
    const card = await screen.findByRole("region", { name: "Plan revision 3" });
    expect(card.textContent).toContain("Replan proposal · rev 3");
    expect(within(card).getByText("T3 motor_stall")).toBeTruthy();
    const diff = within(card).getByRole("list", { name: "Changes from rev 2 to rev 3" });
    expect(diff.querySelector('[data-diff="done"]').textContent).toContain("T1, T2 done in rev 2");
    expect(diff.querySelector('[data-diff="kept"]').textContent).toContain("2 tasks unchanged (T3→T1, T4→T2)");
    expect(within(card).getByRole("button", { name: "Approve rev 3" }).disabled).toBe(false);
    expect(within(card).getByRole("button", { name: "Edit" })).toBeTruthy();
    // the failed row of rev 2 can ask for a re-plan while rev 3 waits
    expect(within(rowIn(2, "t3")).getByRole("button", { name: "Replan" }).disabled).toBe(false);
  });
});

describe("stop current", () => {
  it("calls the task cancel route for that row", async () => {
    server.state = clone(running);
    server.post["/tasks/t1/cancel"] = () => reply(200, canceling);
    render(<Harness />);
    await waitFor(() => expect(row("t1")).toBeTruthy());
    fireEvent.click(within(row("t1")).getByRole("button", { name: "Stop current" }));
    await waitFor(() => expect(posts("/tasks/t1/cancel")).toHaveLength(1));
    // task ids repeat across revisions: the stop names the card's revision
    expect(posts("/tasks/t1/cancel")[0].body).toEqual({ revision: running.mission.active_revision });
    expect(posts("/tasks/t2/cancel")).toHaveLength(0);
    await waitFor(() => expect(within(row("t1")).getByText("canceling")).toBeTruthy());
  });

  it("stops a replaced revision's still-running row by that card's revision, not the active one", async () => {
    const next = clone(running);
    const r1 = next.mission.revisions[0];
    r1.status = "superseded";
    r1.superseded_reason = "operator_message";
    r1.superseded_by = 2;
    r1.runtime.t2.state = "succeeded";
    const r2 = { ...clone(r1), revision: 2, kind: "feedback", status: "executing", superseded_reason: null, superseded_by: null, created_at: r1.created_at + 5 };
    r2.runtime = { t1: { ...clone(r1.runtime.t1), revision: 2 }, t2: { ...clone(r1.runtime.t2), state: "pending" } };
    next.mission.revisions = [r1, r2];
    next.mission.active_revision = 2;
    next.mission.operator_messages = [];
    server.state = next;
    server.post["/tasks/t1/cancel"] = () => reply(200, next);
    render(<Harness />);
    await waitFor(() => expect(rowIn(1, "t1")).toBeTruthy());
    expect(within(rowIn(2, "t1")).getByRole("button", { name: "Stop current" })).toBeTruthy();
    fireEvent.click(within(rowIn(1, "t1")).getByRole("button", { name: "Stop current" }));
    await waitFor(() => expect(posts("/tasks/t1/cancel")).toHaveLength(1));
    expect(posts("/tasks/t1/cancel")[0].body).toEqual({ revision: 1 });
  });
});

describe("refresh", () => {
  const snapshot = () =>
    [...document.querySelectorAll("[data-revision] tr[data-task]")].map((tr) => [
      tr.closest("[data-revision]").dataset.revision,
      tr.dataset.task,
      tr.querySelector("[data-status]").dataset.status,
    ]);

  it("replays the same thread from state after a browser refresh", async () => {
    server.state = clone(recovery);
    const first = render(<Harness />);
    await screen.findByRole("region", { name: "Plan revision 3" });
    const before = snapshot();
    const reports = [...document.querySelectorAll("[data-report]")].map((p) => p.textContent);
    first.unmount();

    render(<Harness />);
    await screen.findByRole("region", { name: "Plan revision 3" });
    expect(snapshot()).toEqual(before);
    expect([...document.querySelectorAll("[data-report]")].map((p) => p.textContent)).toEqual(reports);
    expect(before).toContainEqual(["2", "t3", "failed"]);
  });

  it("keeps a finished mission under Earlier missions across a refresh and a new run", async () => {
    server.state = clone(complete);
    const first = render(<Harness />);
    await screen.findByRole("region", { name: "Mission complete" });
    await waitFor(() => expect(JSON.parse(localStorage.getItem(EARLIER_KEY) || "[]")).toHaveLength(1));
    first.unmount();

    server.state = { ...clone(complete), run_id: "run-next", mission: null, timeline: [] };
    render(<Harness />);
    const earlier = await screen.findByRole("region", { name: "Earlier missions" });
    expect(earlier.textContent).toContain(complete.mission.command);
    expect(earlier.textContent).toContain("completed");
    expect(earlier.textContent).toContain("3 revisions");
    fireEvent.click(within(earlier).getByRole("button", { name: new RegExp(complete.mission.command.slice(0, 20)) }));
    expect(await screen.findByText("Viewing an earlier mission · read-only")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop current" })).toBeNull();
  });

  it("updates the kept record when a mission that hit a planner error is retried and finishes", async () => {
    const failed = clone(complete);
    failed.mission = { ...failed.mission, status: "planner_error", revisions: [], outcome: { status: "planner_error", reason: "Planner timed out." } };
    server.state = failed;
    render(<Harness />);
    await waitFor(() => expect(JSON.parse(localStorage.getItem(EARLIER_KEY) || "[]")[0]?.outcome).toBe("planner_error"));
    server.state = clone(complete);
    await waitFor(() => expect(JSON.parse(localStorage.getItem(EARLIER_KEY))[0].outcome).toBe("completed"), { timeout: 2000 });
    expect(JSON.parse(localStorage.getItem(EARLIER_KEY))).toHaveLength(1);
  });
});

describe("thinking card", () => {
  it("renders only the fields that exist", () => {
    const state = clone(awaiting);
    const rev = state.mission.revisions[0];
    const ctx = { mission: state.mission, timeline: state.timeline, robots: state.robots, planners: state.planners };
    const { unmount } = render(<ThinkingCard defaultOpen thinking={revisionThinking(rev, ctx)} />);
    const lines = [...document.querySelectorAll("[data-kind]")].map((li) => [li.dataset.kind, li.textContent.replace(/^\d+/, "")]);
    expect(lines).toEqual([
      ["summary", `Summary: ${rev.proposal.summary}`],
      ["log", "Initial proposal · plan · checks passed · 1 attempt"],
      ["allocation", "Allocation: observer → camera_01"],
      ["allocation", "Allocation: surveyor → lidar_01"],
    ]);
    expect(screen.getByText("Baseline (no LLM)")).toBeTruthy();
    expect(screen.getByText("1.7 ms")).toBeTruthy();
    unmount();

    // a record with no latency, no log entry, no issues and no allocations shows none of them
    const bare = { ...rev, planner: { ...rev.planner, latency_ms: null }, allocations: {}, proposal: { ...rev.proposal, issues: [] } };
    render(<ThinkingCard defaultOpen thinking={revisionThinking(bare, { ...ctx, mission: { ...state.mission, planning_log: [] } })} />);
    expect([...document.querySelectorAll("[data-kind]")].map((li) => li.dataset.kind)).toEqual(["summary"]);
    expect(screen.queryByText(/ ms$/)).toBeNull();
    expect(screen.queryByText(/Issue:/)).toBeNull();
  });

  it("is open with a shimmer while planning and collapsed once approved", async () => {
    server.state = clone(running);
    render(<Harness />);
    const toggle = await screen.findByRole("button", { name: /Executive thinking/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    cleanup();

    const planning = clone(awaiting);
    planning.mission = { ...planning.mission, status: "planning", revisions: [], planning_log: [] };
    server.state = planning;
    render(<Harness />);
    const live = await screen.findByText("Executive thinking…");
    expect(live.dataset.shimmer).toBe("true");
    expect(live.closest("button").getAttribute("aria-expanded")).toBe("true");
  });
});

describe("composer", () => {
  it("sends with Enter, keeps Shift+Enter for a new line, and uses the chosen planner", async () => {
    server.state = { ...clone(awaiting), mission: null, timeline: [] };
    server.post["/missions"] = () => reply(200, awaiting);
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Try the demo mission" }));
    const box = screen.getByPlaceholderText("Describe a mission");
    expect(box.value).toBe(DEMO_COMMAND);
    expect(document.activeElement).toBe(box);
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(posts("/missions")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Baseline" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "LLM (not bound)" }).disabled).toBe(true);
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(posts("/missions")).toHaveLength(1));
    expect(posts("/missions")[0].body).toEqual({ command: DEMO_COMMAND, planner: "baseline" });
    expect(await screen.findByRole("button", { name: "Approve & run" })).toBeTruthy();
  });

  it("is disabled with the reason while the Executive is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    render(<Harness />);
    await waitFor(() => expect(screen.getByPlaceholderText("Describe a mission").disabled).toBe(true));
    expect(document.querySelector("[data-composer-block]").textContent).toContain("Executive unreachable (Failed to fetch)");
  });

  it("offers a new run once the mission is finished", async () => {
    server.state = clone(complete);
    server.post["/reset"] = () => reply(200, { ...clone(complete), mission: null, timeline: [] });
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Start a new run" }));
    await waitFor(() => expect(posts("/reset")).toHaveLength(1));
    expect(posts("/reset")[0].body).toEqual({ scenario: complete.scenario, faults: [] });
  });
});

describe("mission outcomes", () => {
  it("offers Try again after a planner error, but not after one marked not retryable", async () => {
    const failed = clone(complete);
    failed.mission = { ...failed.mission, status: "planner_error", revisions: [], outcome: { status: "planner_error", reason: "Planner timed out." } };
    server.state = failed;
    const { unmount } = render(<Harness />);
    const card = await screen.findByText("Planner timed out.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    unmount();
    cleanup();
    const tooLong = clone(failed);
    const reason = "This request is too long for the planner. Start a new mission with a shorter command or fewer messages.";
    tooLong.mission.outcome = { status: "planner_error", reason, retryable: false };
    server.state = tooLong;
    render(<Harness />);
    expect(await screen.findByText(reason)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(card).toBeTruthy();
  });

  it("answers a clarification question", async () => {
    server.state = clone(clarification);
    server.post["/mission/clarify"] = () => reply(200, awaiting);
    render(<Harness />);
    const card = await screen.findByRole("region", { name: "Clarification" });
    expect(card.textContent).toContain(clarification.mission.clarification_question);
    fireEvent.change(within(card).getByPlaceholderText("Your answer"), { target: { value: "red_block_1" } });
    fireEvent.click(within(card).getByRole("button", { name: "Answer" }));
    await waitFor(() => expect(posts("/mission/clarify")).toHaveLength(1));
    expect(posts("/mission/clarify")[0].body).toEqual({ answer: "red_block_1" });
    expect(await screen.findByRole("button", { name: "Approve & run" })).toBeTruthy();
  });

  it("offers the planner's options as quick replies, keeps free text and earlier rounds", async () => {
    server.state = clone(clarification);
    const q = clarification.mission.clarification_question;
    server.state.mission.clarifications = [
      { question: "Which object should I move?", options: ["the red block", "the duck"], answer: "the red block", phase: "initial" },
      { question: q, options: ["red_block_1", "red_block_2"], answer: null, phase: "evaluation" },
    ];
    server.post["/mission/clarify"] = () => reply(200, awaiting);
    render(<Harness />);
    const card = await screen.findByRole("region", { name: "Clarification" });
    expect(card.textContent).toContain(q);
    const earlier = screen.getByRole("list", { name: "Earlier questions" });
    expect(earlier.textContent).toContain("Which object should I move?");
    expect(earlier.textContent).toContain("the red block");
    expect(within(earlier).queryByRole("button")).toBeNull();
    expect(within(card).getByPlaceholderText("Your answer")).toBeTruthy(); // free text stays
    fireEvent.click(within(card).getByRole("button", { name: "red_block_2" }));
    await waitFor(() => expect(posts("/mission/clarify")).toHaveLength(1));
    expect(posts("/mission/clarify")[0].body).toEqual({ answer: "red_block_2" });
  });

  it("renders an answered question as the Executive's reply, with no plan and a new run offered", async () => {
    server.state = clone(clarification);
    Object.assign(server.state.mission, {
      command: "What can the gripper car do?",
      status: "answered",
      revisions: [],
      active_revision: null,
      clarification_question: null,
      clarifications: [],
      canonical_goals: null,
      outcome: { status: "answered", reason: "gripper_01 picks and places objects up to 0.25 kg." },
    });
    render(<Harness />);
    const bubble = await screen.findByRole("article", { name: "Executive reply" });
    expect(bubble.textContent).toContain("gripper_01 picks and places objects up to 0.25 kg.");
    expect(screen.queryByRole("region", { name: "Clarification" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve & run" })).toBeNull();
    expect(await screen.findByRole("button", { name: "Start a new run" })).toBeTruthy();
  });

  it("states what a find-only mission found on its completion card", async () => {
    server.state = clone(complete);
    server.state.mission.canonical_goals = [{ kind: "object_located", object_query: { label: "duck" }, area_id: "work_area" }];
    server.state.mission.outcome = { status: "completed", reason: "Searched work_area: no duck found." };
    render(<Harness />);
    const done = await screen.findByRole("region", { name: "Mission complete" });
    expect(done.textContent).toContain("Searched work_area: no duck found.");
  });

  it("keeps the generic reason off a delivery's completion card", async () => {
    server.state = clone(complete);
    render(<Harness />);
    const done = await screen.findByRole("region", { name: "Mission complete" });
    expect(done.textContent).not.toContain("All mission goals satisfied");
  });

  it("shows a blocked mission's reason verbatim and the blocked completion variant", async () => {
    server.state = clone(blocked);
    render(<Harness />);
    const reason = await screen.findByRole("status", { name: "Why the mission is blocked" });
    expect(reason.textContent).toContain(blocked.mission.outcome.reason);
    const done = screen.getByRole("region", { name: "Mission blocked" });
    expect(done.dataset.outcome).toBe("blocked");
    expect(screen.queryByRole("button", { name: "Approve & run" })).toBeNull();
  });
});

describe("sim controls", () => {
  it("is collapsed, labeled simulation only, and resets with the picked faults or injects one now", async () => {
    server.state = clone(running);
    server.post["/reset"] = () => reply(200, { ...clone(awaiting), mission: null, timeline: [] });
    server.post["/faults"] = () => reply(200, { ...clone(running), faults: ["route_blocked"] });
    render(<Harness />);
    const drawer = await screen.findByRole("region", { name: "Simulation controls" });
    expect(drawer.textContent).toContain("simulation only");
    const toggle = within(drawer).getByRole("button", { name: /Sim controls/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(within(drawer).queryByRole("button", { name: "Reset simulation" })).toBeNull();

    fireEvent.click(toggle);
    fireEvent.click(await within(drawer).findByLabelText(/route_blocked/));
    expect(within(drawer).getByText("Resetting cancels the mission in progress.")).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: "inject now" }));
    await waitFor(() => expect(posts("/faults")).toHaveLength(1));
    expect(posts("/faults")[0].body).toEqual({ fault: "route_blocked" });
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "active" }).disabled).toBe(true));

    fireEvent.change(within(drawer).getByRole("combobox"), { target: { value: "known" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Reset simulation" }));
    await waitFor(() => expect(posts("/reset")).toHaveLength(1));
    expect(posts("/reset")[0].body).toEqual({ scenario: "known", faults: ["route_blocked"], world: "random" });
  });

  it("resets a random world with a replay seed or the fixed demo world, and shows the current seed", async () => {
    server.state = { ...clone(awaiting), sim: { world: "random", seed: 3141 } };
    server.post["/reset"] = () => reply(200, { ...clone(awaiting), mission: null, timeline: [], sim: { world: "random", seed: 42 } });
    render(<Harness />);
    const drawer = await screen.findByRole("region", { name: "Simulation controls" });
    fireEvent.click(within(drawer).getByRole("button", { name: /Sim controls/ }));
    expect(within(drawer).getByText(/seed 3141/)).toBeTruthy();
    expect(within(drawer).getByRole("radio", { name: /Random/ }).checked).toBe(true);

    fireEvent.change(within(drawer).getByRole("combobox"), { target: { value: "unknown" } });
    const seed = within(drawer).getByRole("spinbutton", { name: /Replay seed/ });
    fireEvent.change(seed, { target: { value: "-1" } });
    expect(within(drawer).getByRole("alert").textContent).toMatch(/whole number/);
    expect(within(drawer).getByRole("button", { name: "Reset simulation" }).disabled).toBe(true);
    fireEvent.change(seed, { target: { value: "42" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Reset simulation" }));
    await waitFor(() => expect(posts("/reset")).toHaveLength(1));
    expect(posts("/reset")[0].body).toEqual({ scenario: "unknown", faults: [], world: "random", seed: 42 });
    await waitFor(() => expect(within(drawer).getByText(/seed 42/)).toBeTruthy());

    fireEvent.click(within(drawer).getByRole("radio", { name: /Fixed demo/ }));
    expect(within(drawer).getByRole("spinbutton", { name: /Replay seed/ }).disabled).toBe(true);
    fireEvent.click(within(drawer).getByRole("button", { name: "Reset simulation" }));
    await waitFor(() => expect(posts("/reset")).toHaveLength(2));
    expect(posts("/reset")[1].body).toEqual({ scenario: "unknown", faults: [], world: "fixed" });
  });

  it("reveals the simulator's hidden truth only on request, labeled simulation only", async () => {
    server.truth = {
      warning: "SIMULATOR TRUTH - debug only",
      truth: {
        world: "random", seed: 7, faults: [], obstacles: ["pickup_corridor"],
        corridor: { pickup_corridor: "blocked" },
        objects: { mystery_crate_1: { label: "mystery_crate", location_id: "work_area", held_by: null } },
        robots: { gripper_01: { location_id: "dropoff", battery_pct: 61.5 } },
      },
    };
    render(<Harness />);
    const drawer = await screen.findByRole("region", { name: "Simulation controls" });
    fireEvent.click(within(drawer).getByRole("button", { name: /Sim controls/ }));
    expect(server.calls.some((c) => c.path === "/debug/truth")).toBe(false);
    const reveal = within(drawer).getByRole("button", { name: "Reveal world truth" });
    expect(reveal.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(reveal);
    const truth = await within(drawer).findByRole("region", { name: "World truth" });
    await within(truth).findByText(/mystery_crate_1/); // the fetch runs on a timer tick (throttle)
    expect(truth.textContent).toMatch(/simulation only/i);
    expect(truth.textContent).toContain("mystery_crate_1");
    expect(truth.textContent).toContain("work_area");
    expect(truth.textContent).toMatch(/pickup_corridor.*blocked/);
    expect(truth.textContent).toMatch(/gripper_01.*dropoff.*61\.5%/);
    fireEvent.click(within(drawer).getByRole("button", { name: "Hide world truth" }));
    expect(within(drawer).queryByRole("region", { name: "World truth" })).toBeNull();
  });

  it("refetches the revealed truth when mission or task state changes, at most once a second", async () => {
    const truthWith = (heldBy) => ({
      warning: "SIMULATOR TRUTH - debug only",
      truth: {
        world: "random", seed: 7, faults: [], obstacles: [], corridor: { pickup_corridor: "clear" },
        objects: { red_block_1: { label: "red_block", location_id: heldBy ? null : "pickup_zone", held_by: heldBy } },
        robots: { gripper_01: { location_id: "pickup_zone", battery_pct: 70 } },
      },
    });
    const tick = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    // fake timers drive the 500 ms state poll and the 1 s throttle (and Date), so the gaps are exact
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    try {
      server.state = clone(running);
      server.truth = truthWith(null);
      render(<Harness />);
      await tick(50);
      const drawer = screen.getByRole("region", { name: "Simulation controls" });
      fireEvent.click(within(drawer).getByRole("button", { name: /Sim controls/ }));
      fireEvent.click(within(drawer).getByRole("button", { name: "Reveal world truth" }));
      await tick(50);
      expect(within(drawer).getByText(/red_block_1 \(red_block\) at pickup_zone/)).toBeTruthy();
      expect(server.truthTimes).toHaveLength(1);

      // a task finishes and the world version moves on every poll: the truth follows, throttled
      server.truth = truthWith("gripper_01");
      let v = 0;
      for (let i = 0; i < 24; i += 1) {
        const next = clone(running);
        next.world.version = ++v;
        next.mission.revisions.at(-1).runtime.t1.state = v % 2 ? "succeeded" : "running";
        server.state = next;
        await tick(150);
      }
      await tick(1600);
      expect(within(drawer).getByText(/held by gripper_01/)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
    expect(server.truthTimes.length).toBeGreaterThanOrEqual(2);
    const gaps = server.truthTimes.slice(1).map((t, i) => t - server.truthTimes[i]);
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(950);
  });

  it("is hidden when the gateway says plainly it is not simulated", async () => {
    server.state = { ...clone(running), gateway: { ...running.gateway, simulated: false } };
    render(<Harness />);
    await waitFor(() => expect(row("t1")).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Simulation controls" })).toBeNull();
  });
});

describe("operator messages", () => {
  const message = (id, extra = {}) => ({
    id,
    text: `message ${id}`,
    mode: "now",
    status: "queued",
    created_at: awaiting.mission.created_at + 1,
    delivered_at: null,
    delivered_to_revision: null,
    delivered_as: null,
    ...extra,
  });
  const withMessages = (base, messages) => {
    const next = clone(base);
    next.mission.operator_messages = messages;
    return next;
  };
  /* rev 1 replaced by the operator's message; rev 2 is the feedback re-plan waiting for approval */
  function superseded() {
    const next = clone(awaiting);
    const r1 = next.mission.revisions[0];
    r1.status = "superseded";
    r1.superseded_reason = "operator_message";
    r1.superseded_by = 2;
    const r2 = { ...clone(r1), revision: 2, kind: "feedback", status: "awaiting_approval", superseded_reason: null, superseded_by: null, created_at: r1.created_at + 5 };
    next.mission.revisions = [r1, r2];
    next.mission.operator_messages = [
      message("msg-1", { text: "use the camera only", status: "delivered", delivered_at: r1.created_at + 2, delivered_to_revision: 2, delivered_as: "feedback" }),
    ];
    return next;
  }
  const box = () => document.getElementById("mission-input");

  it("keeps the composer open during a mission: Enter sends now, Shift+Enter is a new line", async () => {
    server.post["/mission/messages"] = (body) => reply(200, { ...withMessages(awaiting, [message("msg-1", { text: body.text, status: "delivered" })]), message: {} });
    render(<Harness />);
    await screen.findByRole("button", { name: "Approve & run" });
    expect(box().disabled).toBe(false);
    expect(screen.getByRole("button", { name: "Send now" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Queue" })).toBeTruthy();
    expect(screen.getByText("Enter sends now · Shift+Enter new line")).toBeTruthy();
    expect(document.querySelector("[data-composer-block]")).toBeNull();
    fireEvent.change(box(), { target: { value: "use the camera only" } });
    fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
    expect(posts("/mission/messages")).toHaveLength(0);
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts("/mission/messages")).toHaveLength(1));
    expect(posts("/mission/messages")[0].body).toEqual({ text: "use the camera only", mode: "now" });
    expect(posts("/missions")).toHaveLength(0);
    await waitFor(() => expect(box().value).toBe(""));
  });

  it("queues a message as a chip that can be removed or sent now", async () => {
    server.post["/mission/messages"] = (body) => reply(200, { ...withMessages(awaiting, [message("msg-1", { text: body.text, mode: "queue" })]), message: {} });
    server.post["/mission/messages/msg-1/send-now"] = () => reply(200, withMessages(awaiting, [message("msg-1", { status: "delivered" })]));
    server.del["/mission/messages/msg-1"] = () => reply(200, withMessages(awaiting, [message("msg-1", { status: "canceled" })]));
    render(<Harness />);
    await screen.findByRole("button", { name: "Approve & run" });
    fireEvent.change(box(), { target: { value: "then check the corridor" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));
    await waitFor(() => expect(posts("/mission/messages")).toHaveLength(1));
    expect(posts("/mission/messages")[0].body).toEqual({ text: "then check the corridor", mode: "queue" });

    const chips = await screen.findByRole("list", { name: "Queued messages" });
    expect(within(chips).getByText("then check the corridor")).toBeTruthy();
    expect(within(chips).getByText("Queued - delivered at next planning step")).toBeTruthy();
    fireEvent.click(within(chips).getByRole("button", { name: "Send now: then check the corridor" }));
    await waitFor(() => expect(posts("/mission/messages/msg-1/send-now")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Queued messages" })).toBeNull());

    server.state = withMessages(awaiting, [message("msg-1", { text: "then check the corridor" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Remove queued message: then check the corridor" }));
    await waitFor(() => expect(server.calls.filter((c) => c.method === "DELETE" && c.path === "/mission/messages/msg-1")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Queued messages" })).toBeNull());
  });

  it("folds a revision superseded by a message and tags the message with the revision it reached", async () => {
    server.state = superseded();
    render(<Harness />);
    const fold = await screen.findByRole("button", { name: /Superseded by your message/ });
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector('[data-revision="1"]')).toBeNull();
    const bubble = document.querySelector('[data-message="msg-1"]');
    expect(bubble.textContent).toContain("use the camera only");
    expect(bubble.textContent).toContain("delivered to revision 2");
    // the message sits between the folded rev 1 and the feedback rev 2 it produced
    const order = [...document.querySelectorAll("[data-message], [data-revision], [data-fold]")].map(
      (el) => el.dataset.message || el.dataset.fold || el.dataset.revision,
    );
    expect(order).toEqual(["1", "msg-1", "2"]);
    expect(screen.getByRole("button", { name: "Approve rev 2" })).toBeTruthy();
    expect(screen.getByText("your message")).toBeTruthy();
    fireEvent.click(fold);
    const old = document.querySelector('[data-revision="1"]');
    expect(old).toBeTruthy();
    expect(within(old).queryByRole("button", { name: /Approve/ })).toBeNull();
  });

  it("shows a held message with its tag while the planner works", async () => {
    const next = clone(awaiting);
    next.mission.status = "planning";
    next.mission.operator_messages = [message("msg-1", { status: "held", text: "skip the corridor" })];
    server.state = next;
    render(<Harness />);
    await waitFor(() => expect(document.querySelector('[data-message="msg-1"]')).toBeTruthy());
    expect(document.querySelector('[data-message="msg-1"]').textContent).toContain("held");
    expect(screen.queryByRole("list", { name: "Queued messages" })).toBeNull();
  });

  it("offers approve and reject for a live re-plan while the running revision keeps going", async () => {
    const next = clone(running);
    const r1 = next.mission.revisions[0];
    const r2 = { ...clone(r1), revision: 2, kind: "feedback", status: "awaiting_approval", runtime: {}, created_at: r1.created_at + 5 };
    next.mission.revisions = [r1, r2];
    next.mission.operator_messages = [];
    server.state = next;
    server.post["/revisions/2/approve"] = () => reply(200, next);
    render(<Harness />);
    const approve = await screen.findByRole("button", { name: "Approve rev 2" });
    const card = document.querySelector('[data-revision="2"]');
    expect(within(card).queryByRole("button", { name: "Edit" })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Reject" }));
    expect(within(card).getByText(/rev 1 keeps running/)).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "Back" }));
    fireEvent.click(within(card).getByRole("button", { name: "Approve rev 2" }));
    await waitFor(() => expect(posts("/revisions/2/approve")).toHaveLength(1));
    expect(posts("/revisions/2/approve")[0].body).toEqual({ proposal_hash: r2.proposal_hash });
    expect(approve).toBeTruthy();
  });

  it("says Reject ends the mission and points to messages instead", async () => {
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(screen.getByText("Reject ends this mission. To change the plan, send a message instead.")).toBeTruthy();
  });

  it("keeps the text and shows the server's reason when a message is refused", async () => {
    server.state = withMessages(awaiting, [message("msg-1", { text: "wait" })]);
    server.post["/mission/messages"] = () => reply(409, { error: "Mission is completed; start a new mission instead." });
    render(<Harness />);
    expect((await screen.findByRole("button", { name: "Remove queued message: wait" })).tagName).toBe("BUTTON");
    fireEvent.change(box(), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send now" }));
    expect(await screen.findByText("Mission is completed; start a new mission instead.")).toBeTruthy();
    expect(box().value).toBe("hello");
  });

  it("shows a canceled message in the thread with its note", async () => {
    const next = clone(complete);
    const note = "Mission ended before your message was applied; send it again as a new mission.";
    next.mission.operator_messages = [
      message("msg-1", { status: "canceled", text: "also check the corridor", created_at: next.mission.created_at + 1, note }),
    ];
    server.state = next;
    render(<Harness />);
    await waitFor(() => expect(document.querySelector('[data-message="msg-1"]')).toBeTruthy());
    const bubble = document.querySelector('[data-message="msg-1"]');
    expect(bubble.textContent).toContain("also check the corridor");
    expect(bubble.textContent).toContain("canceled");
    expect(bubble.textContent).toContain(note);
  });

  it("says why a live re-plan was dropped when its stage ended first", async () => {
    const next = clone(complete);
    const last = next.mission.revisions.at(-1);
    next.mission.revisions.push({
      ...clone(last),
      revision: last.revision + 1,
      kind: "feedback",
      status: "superseded",
      superseded_reason: "stage_ended",
      runtime: {},
      created_at: last.created_at + 1,
    });
    server.state = next;
    render(<Harness />);
    expect(await screen.findByText("Superseded: the running stage ended before this re-plan was approved.")).toBeTruthy();
  });

  it("keeps the new-mission composer when no mission is active", async () => {
    server.state = { ...clone(awaiting), mission: null, timeline: [] };
    render(<Harness />);
    await screen.findByRole("button", { name: "Try the demo mission" });
    expect(screen.getByPlaceholderText("Describe a mission")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send mission" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Queue" })).toBeNull();
    expect(screen.getByText("Enter sends · Shift+Enter new line")).toBeTruthy();
  });
});
