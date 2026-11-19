// @vitest-environment jsdom
// STOP ALL through the whole App: one click sends live-data's stop to every connected car and the
// Executive's cancel at once, and the cars' stops land even when the Executive never answers.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("../RobotViewer.jsx", () => ({ default: () => <div data-testid="model" /> }));
vi.mock("../robotModels.js", () => ({
  PRESET_MODELS: { cameraRcCar: { label: "Camera car" }, lidarRcCar: { label: "LiDAR car" }, gripperRcCar: { label: "Gripper car" } },
  snapshot: () => new Promise(() => {}),
}));

import App from "../App.jsx";
import { EXEC_API_URL } from "../executive/api.js";
import { setEventSourceFactory } from "../live/useLiveStream.js";

const API = "http://127.0.0.1:8787/api";
const never = () => new Promise(() => {});
const reply = (status, data) => ({ ok: status < 400, status, json: async () => data });

const mission = () => ({
  mission_id: "m1",
  status: "executing",
  created_at: Date.now() / 1000 - 102,
  active_revision: 1,
  revisions: [{ revision: 1, tasks: [{ task_id: "T1", robot_id: "lidar_01", skill: "navigate_to", agent_command: { line: "lidar_01 navigate_to dock" } }], runtime: { T1: { state: "running" } } }],
});

let server;

function fakeFetch(url, init) {
  const u = String(url);
  const body = init?.body ? JSON.parse(init.body) : {};
  if (u.startsWith(EXEC_API_URL)) {
    const path = u.slice(EXEC_API_URL.length);
    server.calls.push({ exec: true, path, method: init?.method ?? "GET" });
    if (server.execDown) return Promise.reject(new TypeError("Failed to fetch"));
    if (path === "/health") return reply(200, { ok: true, ready: true, missing: [], gateway: { kind: "fake", label: "fake fleet", connected: true } });
    if (path === "/state") {
      return reply(200, {
        gateway: { kind: "fake", label: "fake fleet", connected: true },
        default_planner: "rules",
        planners: { llm: { provider_label: "Groq", model: "llama" } },
        catalog: { locations: [{ location_id: "dock", label: "Dock" }] },
        robots: [{ robot_id: "lidar_01", display_name: "LiDAR car", preset: "lidarRcCar", state: { location_id: "dock", battery_pct: 80, health: "ok", available: true } }],
        mission: server.mission,
      });
    }
    if (path === "/cancel") return server.cancel();
    return reply(200, {});
  }
  const path = u.replace(API, "");
  server.calls.push({ path, body });
  switch (path) {
    case "/health":
      return reply(200, { ok: true, ros: { found: true, setup: "/opt/ros/jazzy/setup.bash" } });
    case "/robots/connect":
      return reply(200, { ok: true, nodes: ["/base_driver"], topics: [], cmd_vel_subscribers: 1, warnings: [], camera: null, worker_replaced: false });
    case "/robots/drive":
      return reply(200, { ok: true, action: body.action, streaming: false, link: { stop: "pending", reporting: true, seq: 20 } });
    case "/robots/link":
    case "/robots/status":
      return reply(200, { ok: true, nodes: ["/base_driver"], link: { stop: "idle", reporting: true, seq: 1 } });
    default:
      return reply(200, { ok: true });
  }
}

beforeEach(() => {
  server = { calls: [], mission: mission(), execDown: false, cancel: never };
  localStorage.setItem("hive:fleet:manual-rc-v1", JSON.stringify([{ id: "r1", name: "Rover", preset: "lidarRcCar", host: "10.0.0.5", domainId: 43 }]));
  Element.prototype.scrollTo = () => {};
  Element.prototype.scrollIntoView = () => {};
  HTMLCanvasElement.prototype.getContext = () => null;
  vi.stubGlobal("fetch", vi.fn(async (url, init) => fakeFetch(url, init)));
  setEventSourceFactory(() => ({ addEventListener() {}, close() {} }));
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  setEventSourceFactory(null);
});

// The app opens on the Mission tab; these tests connect the car from its Fleet card first.
function renderOnFleet() {
  render(<App />);
  fireEvent.click(within(screen.getByRole("navigation", { name: "Views" })).getByRole("button", { name: "Fleet" }));
}

const card = () => screen.getByRole("button", { name: "Open Rover" }).closest("article");
const stopCalls = () => server.calls.filter((c) => c.path === "/robots/drive" && c.body.action === "stop");
const cancelCalls = () => server.calls.filter((c) => c.exec && c.path === "/cancel");

async function connectRover() {
  fireEvent.click(within(card()).getByRole("button", { name: "Connect Rover" }));
  await waitFor(() => expect(within(card()).getByText("Live")).toBeTruthy());
}

describe("STOP ALL", () => {
  it("sends the car stop and the mission cancel in the same click, and the car's stop lands while the Executive hangs", async () => {
    renderOnFleet();
    await connectRover();
    expect((await screen.findByTestId("mission-pill")).textContent).toMatch(/^mission running · rev 1 · 01:4\d$/);

    const stop = screen.getByRole("button", { name: "STOP ALL" });
    expect(stop.getAttribute("title")).toBe("software stop, not a hardware e-stop");
    fireEvent.click(stop);

    // both requests are already out when the click returns: nothing waited on the other
    expect(stopCalls()).toHaveLength(1);
    expect(cancelCalls()).toHaveLength(1);
    const order = server.calls.indexOf(stopCalls()[0]) < server.calls.indexOf(cancelCalls()[0]);
    expect(order).toBe(true);

    const results = screen.getByRole("status", { name: "STOP ALL results" });
    expect(within(results).getByTestId("stop-line-mission").textContent).toBe("cancel requested…");
    // the car's line moves on to its live stop state although /cancel never answers
    await waitFor(() => expect(within(results).getByTestId("stop-line-r1").textContent).not.toBe("sending stop…"));
    expect(within(results).getByTestId("stop-line-r1").textContent).toMatch(/Stopping|Stopped|not confirmed/);
    expect(within(results).getByTestId("stop-line-mission").textContent).toBe("cancel requested…");
  });

  it("still stops the cars when the Executive is offline, and says the cancel failed", async () => {
    server.execDown = true;
    renderOnFleet();
    await connectRover();
    await waitFor(() => expect(screen.getByRole("status", { name: /^LIVE:/ }).getAttribute("title")).toMatch(/Executive offline/));

    fireEvent.click(screen.getByRole("button", { name: "STOP ALL" }));
    expect(stopCalls()).toHaveLength(1);
    expect(cancelCalls()).toHaveLength(1); // the Executive cannot say there is no mission, so it is asked to cancel
    const results = screen.getByRole("status", { name: "STOP ALL results" });
    await waitFor(() => expect(within(results).getByTestId("stop-line-mission").textContent).toBe("cancel failed: Failed to fetch"));
    expect(within(results).getByTestId("stop-line-r1").textContent).not.toBe("sending stop…");
  });

  it("does not call the Executive when it reports no mission, and says when no car is connected", async () => {
    server.mission = null;
    renderOnFleet();
    await waitFor(() => expect(server.calls.some((c) => c.exec && c.path === "/state")).toBe(true));
    await waitFor(() => expect(screen.getByRole("status", { name: /^LIVE:/ }).getAttribute("title")).toBe("Car backend and Executive healthy"));

    fireEvent.click(screen.getByRole("button", { name: "STOP ALL" }));
    const results = screen.getByRole("status", { name: "STOP ALL results" });
    expect(within(results).getByText("No connected cars")).toBeTruthy();
    expect(within(results).getByTestId("stop-line-mission").textContent).toBe("no mission active");
    expect(cancelCalls()).toHaveLength(0);
    expect(stopCalls()).toHaveLength(0);

    fireEvent.click(within(results).getByRole("button", { name: "Close STOP ALL results" }));
    expect(screen.queryByRole("status", { name: "STOP ALL results" })).toBeNull();
  });

  it("treats the Executive's 'No active mission.' as nothing to cancel", async () => {
    server.cancel = () => reply(404, { ok: false, error: "No active mission.", detail: null });
    renderOnFleet();
    await screen.findByTestId("mission-pill");
    fireEvent.click(screen.getByRole("button", { name: "STOP ALL" }));
    const results = screen.getByRole("status", { name: "STOP ALL results" });
    await waitFor(() => expect(within(results).getByTestId("stop-line-mission").textContent).toBe("no mission active"));
  });
});
