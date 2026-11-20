// @vitest-environment jsdom
// The clarification card's quick replies: repeated option text still renders one button each,
// with no duplicate React keys, and a click sends that option's text.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ClarificationCard } from "./MissionCards.jsx";

afterEach(cleanup);

describe("ClarificationCard", () => {
  it("renders repeated options without duplicate keys and sends the clicked text", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const act = vi.fn(async () => ({ ok: true }));
    render(<ClarificationCard act={act} busy={false} options={["the red block", "the red block"]} question="Which one?" />);
    const buttons = screen.getAllByRole("button", { name: "the red block" });
    expect(buttons).toHaveLength(2);
    expect(errors.mock.calls.some((c) => String(c[0]).includes("same key"))).toBe(false);
    fireEvent.click(buttons[1]);
    expect(act).toHaveBeenCalledWith("/mission/clarify", { answer: "the red block" }, "Answer sent to the planner.");
    errors.mockRestore();
  });
});
