// @vitest-environment jsdom
// "Why this plan": the planner's rationale on a plan card. One item is the first line; several fold
// behind an aria-expanded button; no rationale shows nothing; every revision that has one shows it.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import awaiting from "./__fixtures__/awaiting.json";
import PlanCard from "./PlanCard.jsx";

afterEach(cleanup);

const clone = (x) => JSON.parse(JSON.stringify(x));
const ONE = ["camera_01 is the only car with a camera, so it does the looking."];
const MANY = [
  "gripper_01 is the only car that picks and places.",
  "It has no camera or LiDAR and drives by dead reckoning (simulation).",
  "Assumes the corridor stays clear.",
];

function card(rationale, { readOnly = false, latest = true } = {}) {
  const st = clone(awaiting);
  const rev = st.mission.revisions[0];
  if (rationale !== undefined) rev.proposal.rationale = rationale;
  return render(<PlanCard act={vi.fn()} latest={latest} mission={st.mission} readOnly={readOnly} rev={rev} state={st} />);
}

describe("Why this plan", () => {
  it("shows nothing for a revision without a rationale (old output, or an empty list)", () => {
    card(undefined);
    expect(screen.queryByText(/Why this plan/)).toBeNull();
    cleanup();
    card([]);
    expect(screen.queryByText(/Why this plan/)).toBeNull();
    cleanup();
    card(["", "   "]);
    expect(screen.queryByText(/Why this plan/)).toBeNull();
  });

  it("shows a single item as an always-visible first line, with no toggle", () => {
    card(ONE);
    const why = screen.getByRole("region", { name: "Why this plan" });
    expect(why.textContent).toContain(ONE[0]);
    expect(within(why).queryByRole("button")).toBeNull();
    // above the task table
    const table = screen.getByRole("table");
    expect(why.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("folds several items behind an aria-expanded button, collapsed by default", () => {
    card(MANY);
    const toggle = screen.getByRole("button", { name: /Why this plan/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toMatch(/3/);
    expect(screen.queryByText(MANY[1])).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const list = screen.getByRole("list", { name: "Why this plan" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(MANY);
    expect(toggle.getAttribute("aria-controls")).toBe(list.closest("[id]").id);

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(MANY[1])).toBeNull();
  });

  it("shows for a read-only or superseded revision too", () => {
    card(ONE, { readOnly: true, latest: false });
    expect(screen.getByRole("region", { name: "Why this plan" }).textContent).toContain(ONE[0]);
    cleanup();
    card(MANY, { readOnly: true, latest: false });
    expect(screen.getByRole("button", { name: /Why this plan/ })).toBeTruthy();
  });

  it("renders the text as text, not markup", () => {
    card(["<img src=x onerror=alert(1)> **bold**"]);
    const why = screen.getByRole("region", { name: "Why this plan" });
    expect(why.querySelector("img")).toBeNull();
    expect(why.textContent).toContain("<img src=x onerror=alert(1)> **bold**");
  });
});
