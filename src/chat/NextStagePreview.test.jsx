// @vitest-environment jsdom
// The next-stage preview on a plan card: under an observation stage's tasks, a muted "Likely next stage -
// not approved, may change" block (robot + described action per task, what it waits for, no controls), and on
// the continuation revision a line saying whether it matches the preview or what changed.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import awaiting from "./__fixtures__/awaiting.json";
import continuation from "./__fixtures__/continuation.json";
import PlanCard from "./PlanCard.jsx";
import { describeAction } from "../executive/api.js";

afterEach(cleanup);

const clone = (x) => JSON.parse(JSON.stringify(x));
const ref = { object_query: { label: "red_block" } };
const PREVIEW = {
  summary: "Carry the red block to the drop-off zone.",
  tasks: [
    { task_id: "t1", binding_id: "carrier", action: { kind: "navigate_to", location_id: "pickup_zone", route_id: "pickup_corridor" }, depends_on: [], preferred_robot_id: null },
    { task_id: "t2", binding_id: "carrier", action: { kind: "pick_object", ...ref }, depends_on: ["t1"], preferred_robot_id: "gripper_01" },
    { task_id: "t3", binding_id: "carrier", action: { kind: "navigate_to", location_id: "dropoff_zone", route_id: "pickup_corridor" }, depends_on: ["t2"], preferred_robot_id: null },
    { task_id: "t4", binding_id: "carrier", action: { kind: "place_object", ...ref, location_id: "dropoff_zone" }, depends_on: ["t3"], preferred_robot_id: null },
  ],
  waits_for: ["camera_01 sighting of red_block", "lidar_01 corridor scan"],
};

function stage(preview, { readOnly = false, latest = true } = {}) {
  const st = clone(awaiting);
  const rev = st.mission.revisions[0];
  if (preview !== undefined) rev.proposal.next_stage_preview = preview;
  return render(<PlanCard act={vi.fn()} latest={latest} mission={st.mission} readOnly={readOnly} rev={rev} state={st} />);
}

describe("Likely next stage block", () => {
  it("shows nothing when the revision has no preview (old output, or a full plan)", () => {
    stage(undefined);
    expect(screen.queryByText(/Likely next stage/)).toBeNull();
    cleanup();
    stage(null);
    expect(screen.queryByText(/Likely next stage/)).toBeNull();
  });

  it("is labelled as not approved, lists each task with a robot and the described action, and what it waits for", () => {
    stage(clone(PREVIEW));
    const block = screen.getByRole("region", { name: "Likely next stage" });
    expect(block.textContent).toContain("Likely next stage - not approved, may change");
    expect(block.textContent).toContain("Carry the red block to the drop-off zone.");
    const rows = within(within(block).getByRole("list", { name: "Previewed tasks" })).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    PREVIEW.tasks.forEach((t, i) => {
      expect(rows[i].textContent).toContain(describeAction(t.action));
      expect(rows[i].textContent).toContain(`T${i + 1}`);
    });
    expect(rows[1].textContent).toContain("gripper_01"); // the preview names it
    expect(rows[3].textContent).toContain("gripper_01"); // only the gripper car can place an object
    expect(rows[0].textContent).toContain("gripper_01"); // the only car that reaches the pickup zone
    const waits = within(block).getByRole("list", { name: "Waits for" });
    expect(within(waits).getAllByRole("listitem").map((li) => li.textContent)).toEqual(PREVIEW.waits_for);
  });

  it("has no approve, edit, reject or stop controls inside it, and no table cells to act on", () => {
    stage(clone(PREVIEW));
    const block = screen.getByRole("region", { name: "Likely next stage" });
    expect(within(block).queryAllByRole("button")).toHaveLength(0);
    expect(within(block).queryByRole("table")).toBeNull();
    expect(block.querySelector("input, select, textarea, button")).toBeNull();
    // the real controls of the card are still there for the revision itself
    expect(screen.getByRole("button", { name: /Approve/ })).toBeTruthy();
  });

  it("sits under the task table and is visually muted", () => {
    stage(clone(PREVIEW));
    const block = screen.getByRole("region", { name: "Likely next stage" });
    expect(screen.getByRole("table").compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(block.className).toMatch(/border-dashed/);
    expect(block.className).toMatch(/text-muted|text-faint/);
  });

  it("does not make the revision's own task table longer", () => {
    stage(clone(PREVIEW));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(1 + awaiting.mission.revisions[0].tasks.length);
  });

  it("shows for a read-only (earlier mission) or superseded revision too", () => {
    stage(clone(PREVIEW), { readOnly: true, latest: false });
    expect(screen.getByRole("region", { name: "Likely next stage" })).toBeTruthy();
  });

  it("says so when no robot in the catalog could do a previewed task", () => {
    const odd = clone(PREVIEW);
    odd.tasks = [{ task_id: "t1", binding_id: "x", action: { kind: "inspect_area", area_id: "dropoff_zone", object_query: { label: "x" } }, depends_on: [] }];
    stage(odd);
    expect(screen.getByRole("region", { name: "Likely next stage" }).textContent).toMatch(/no robot/i);
  });

  it("renders the text as text, not markup, and survives a missing waits_for", () => {
    const p = clone(PREVIEW);
    p.summary = "<img src=x onerror=alert(1)> **bold**";
    delete p.waits_for;
    stage(p);
    const block = screen.getByRole("region", { name: "Likely next stage" });
    expect(block.querySelector("img")).toBeNull();
    expect(block.textContent).toContain("<img src=x onerror=alert(1)> **bold**");
    expect(within(block).queryByRole("list", { name: "Waits for" })).toBeNull();
  });

  it("fits a 390 px column: no fixed minimum width, text wraps", () => {
    stage(clone(PREVIEW));
    const block = screen.getByRole("region", { name: "Likely next stage" });
    expect(block.innerHTML).not.toMatch(/min-w-\[/);
    expect(block.innerHTML).toMatch(/break-words|min-w-0/);
  });

  it("lists the notes the backend attached to the preview as muted notes, not errors", () => {
    const st = clone(awaiting);
    const rev = st.mission.revisions[0];
    rev.proposal.next_stage_preview = clone(PREVIEW);
    rev.validation.preview_findings = [{ code: "route_blocked", severity: "warning", message: "Latest observation reports pickup_corridor blocked.", task_id: "t1" }];
    render(<PlanCard act={vi.fn()} latest mission={st.mission} rev={rev} state={st} />);
    const block = screen.getByRole("region", { name: "Likely next stage" });
    expect(within(block).getByRole("list", { name: "Notes on the preview" }).textContent).toContain("pickup_corridor blocked");
    expect(screen.getByRole("button", { name: /Approve/ }).disabled).toBe(false); // notes never block approval
  });
});

const DIFF = {
  previous_revision: 1,
  matches_preview: true,
  kept: [{ preview_task: "t1", task: "t1", what: 'navigate_to(location_id="pickup_zone")', was: null, differences: [] }],
  changed: [],
  added: [],
  removed: [],
};

function cont(diff, changes) {
  const st = clone(continuation);
  const rev = st.mission.revisions[1];
  if (diff !== undefined) rev.preview_diff = diff;
  if (changes !== undefined) rev.proposal.preview_changes = changes;
  return render(<PlanCard act={vi.fn()} latest mission={st.mission} rev={rev} state={st} />);
}

describe("Compared with the preview line", () => {
  it("shows nothing for a revision without a diff", () => {
    cont(undefined);
    expect(screen.queryByText(/preview/i)).toBeNull();
    cleanup();
    cont(null);
    expect(screen.queryByText(/Matches the preview|Changed from preview/)).toBeNull();
  });

  it("says 'Matches the preview' when the backend diff matches, and adds the planner's words", () => {
    cont(clone(DIFF), ["Evidence as previewed."]);
    const line = screen.getByRole("region", { name: "Compared with the preview" });
    expect(line.textContent).toContain("Matches the preview");
    expect(line.textContent).toContain("Evidence as previewed.");
    expect(line.textContent).not.toContain("Changed from preview");
  });

  it("lists what changed, was added and was removed from the backend diff, with the planner's explanation", () => {
    const diff = {
      previous_revision: 1,
      matches_preview: false,
      kept: [{ preview_task: "t1", task: "t1", what: 'navigate_to(location_id="pickup_zone")', was: null, differences: [] }],
      changed: [{ preview_task: "t3", task: "t2", what: 'navigate_to(location_id="work_area")', was: 'navigate_to(location_id="dropoff_zone")', differences: ["location_id: dropoff_zone -> work_area"] }],
      added: [{ preview_task: null, task: "t5", what: 'scan_route(route_id="pickup_corridor")', was: null, differences: [] }],
      removed: [{ preview_task: "t4", task: null, what: 'place_object(object_id="red_block_1")', was: null, differences: [] }],
    };
    cont(diff, ["The block is at work_area, not pickup_zone."]);
    const line = screen.getByRole("region", { name: "Compared with the preview" });
    expect(line.textContent).toContain("Changed from preview:");
    expect(line.textContent).not.toContain("Matches the preview");
    const items = within(within(line).getByRole("list", { name: "Changes from the preview" })).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(3);
    expect(items[0]).toContain("T3");
    expect(items[0]).toContain("location_id: dropoff_zone -> work_area");
    expect(items[1]).toMatch(/added/i);
    expect(items[1]).toContain("T5");
    expect(items[2]).toMatch(/dropped|removed|no longer/i);
    expect(items[2]).toContain("T4");
    expect(within(line).getByRole("list", { name: "Planner's explanation" }).textContent).toContain("The block is at work_area");
    expect(line.querySelector("button")).toBeNull();
  });

  it("trusts the backend diff, not the planner's words, for matches/changed", () => {
    cont(clone(DIFF), ["Everything changed!"]);
    expect(screen.getByRole("region", { name: "Compared with the preview" }).textContent).toContain("Matches the preview");
  });

  it("sits above the task table and renders text as text", () => {
    cont(clone(DIFF), ["<b>bold</b>"]);
    const line = screen.getByRole("region", { name: "Compared with the preview" });
    expect(line.compareDocumentPosition(screen.getByRole("table")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(line.querySelector("b")).toBeNull();
  });
});

describe("in the mission thread", () => {
  it("shows the preview under stage 1 and the comparison on the continuation, read-only too", async () => {
    const { Thread } = await import("./ChatView.jsx");
    const st = clone(continuation);
    st.mission.revisions[0].proposal.next_stage_preview = clone(PREVIEW);
    st.mission.revisions[1].preview_diff = clone(DIFF);
    st.mission.revisions[1].proposal.preview_changes = ["Evidence as previewed."];
    for (const readOnly of [false, true]) {
      cleanup();
      render(<Thread act={vi.fn()} readOnly={readOnly} state={st} />);
      expect(screen.getAllByRole("region", { name: "Likely next stage" })).toHaveLength(1);
      expect(screen.getAllByRole("region", { name: "Compared with the preview" })).toHaveLength(1);
    }
  });
});
