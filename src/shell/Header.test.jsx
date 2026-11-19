// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import Header from "./Header.jsx";
import { setTheme } from "./theme.js";

afterEach(() => {
  cleanup();
  setTheme("dark");
});

const healthy = { ok: true, down: [], text: "Car backend and Executive healthy" };
const degraded = { ok: false, down: ["car backend offline (npm run backend)", "Executive offline"], text: "car backend offline (npm run backend) · Executive offline" };
const header = (props = {}) =>
  render(<Header live={healthy} mission={null} models={null} onCloseStop={vi.fn()} onNavigate={vi.fn()} onStopAll={vi.fn()} sim stop={null} view="fleet" {...props} />);

describe("Header", () => {
  it("has the Fleet | Map | Chat tabs, with Fleet lit on the robot page", () => {
    const onNavigate = vi.fn();
    header({ view: "robot", onNavigate });
    const nav = screen.getByRole("navigation", { name: "Views" });
    expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(["Fleet", "Map", "Chat"]);
    expect(within(nav).getByRole("button", { name: "Fleet" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(within(nav).getByRole("button", { name: "Map" }));
    expect(onNavigate).toHaveBeenCalledWith("map");
  });

  it("shows the LIVE dot green when both backends are healthy, amber naming what is down otherwise", () => {
    header();
    expect(screen.getByTestId("live-dot").className).toContain("bg-live");
    cleanup();
    header({ live: degraded });
    const live = screen.getByRole("status", { name: /^LIVE:/ });
    expect(screen.getByTestId("live-dot").className).toContain("bg-warn");
    expect(live.getAttribute("title")).toBe(degraded.text);
    expect(screen.getByText("Executive offline")).toBeTruthy(); // the hover / focus tooltip
    expect(screen.getByText("car backend offline (npm run backend)")).toBeTruthy();
  });

  it("shows a mission pill with revision and elapsed time, tagged SIM, that opens Chat", () => {
    const onNavigate = vi.fn();
    header({ mission: { status: "executing", active_revision: 2, created_at: Date.now() / 1000 - 65 }, onNavigate });
    expect(screen.getByTestId("mission-pill").textContent).toMatch(/^mission running · rev 2 · 01:0[56]$/);
    expect(screen.getByText("SIM")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Open the mission in Chat"));
    expect(onNavigate).toHaveBeenCalledWith("chat");
  });

  it("has no pill without an active mission", () => {
    header({ mission: { status: "completed", active_revision: 1, created_at: 0 } });
    expect(screen.queryByTestId("mission-pill")).toBeNull();
  });

  it("toggles the theme", () => {
    header();
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeTruthy();
  });

  it("opens the Models placeholder", () => {
    header({ models: { defaultPlanner: "rules", llm: { provider_label: "Groq", model: "llama-3.3" } } });
    fireEvent.click(screen.getByRole("button", { name: "Models" }));
    const menu = screen.getByRole("group", { name: "Models" });
    expect(within(menu).getByText("rules")).toBeTruthy();
    expect(within(menu).getByText("Groq · llama-3.3")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Models" })).toBeNull();
  });

  it("fires STOP ALL in one click and shows each result", () => {
    const onStopAll = vi.fn();
    const stop = {
      at: "10:00:00",
      lines: [
        { key: "r1", label: "Rover", text: "Stopped", level: "live" },
        { key: "mission", label: "Executive mission", text: "cancel requested…", level: "warn" },
      ],
    };
    header({ onStopAll, stop });
    fireEvent.click(screen.getByRole("button", { name: "STOP ALL" }));
    expect(onStopAll).toHaveBeenCalledTimes(1);
    const results = screen.getByRole("status", { name: "STOP ALL results" });
    expect(within(results).getByTestId("stop-line-r1").textContent).toBe("Stopped");
    expect(within(results).getByTestId("stop-line-mission").textContent).toBe("cancel requested…");
  });
});
