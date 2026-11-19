// @vitest-environment jsdom
// Renders the whole App against a fake backend and a fake EventSource, so the car page's wiring is tested:
// what one SSE stream subscribes to (R26, R27), which link wins (R28), what the mode says after a stop
// (final audit #5), the drive pad's car line (#6), the Home badge, and a reconnect that replaced a worker (R29).
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("./RobotViewer.jsx", () => ({ default: () => <div data-testid="model" /> }));
vi.mock("./robotModels.js", () => ({
  PRESET_MODELS: { cameraRcCar: { label: "Camera car" }, lidarRcCar: { label: "LiDAR car" }, gripperRcCar: { label: "Gripper car" } },
  snapshot: () => new Promise(() => {}),
}));

import App from "./App.jsx";
import { setEventSourceFactory } from "./live/useLiveStream.js";

const API = "http://127.0.0.1:8787/api";
const TOPICS = [
  { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", kind: "value" },
  { name: "/scan", type: "sensor_msgs/msg/LaserScan", kind: "scan" },
  { name: "/ros_data", type: "std_msgs/msg/String", kind: "value" },
  { name: "/esp_data", type: "std_msgs/msg/String", kind: "value" },
  { name: "/tf", type: "tf2_msgs/msg/TFMessage", kind: "generic" },
  { name: "/odom", type: "nav_msgs/msg/Odometry", kind: "value" },
];

class FakeSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeSource.instances.push(this);
  }
  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }
  close() {
    this.closed = true;
  }
  emit(type, data) {
    this.listeners[type]?.({ data: JSON.stringify(data) });
  }
}
FakeSource.instances = [];
const liveSources = () => FakeSource.instances.filter((s) => !s.closed);
const streamed = (source) => decodeURIComponent(new URL(source.url).searchParams.get("topics") || "").split(",").filter(Boolean);

let server;
const reply = (status, data) => ({ ok: status < 400, status, json: async () => data });

function fakeFetch(url, init) {
  const path = String(url).replace(API, "");
  const body = init?.body ? JSON.parse(init.body) : {};
  server.calls.push({ path, body });
  switch (path) {
    case "/health":
      return reply(200, { ok: true, ros: { found: true, setup: "/opt/ros/jazzy/setup.bash" } });
    case "/robots/connect": {
      const replaced = server.replaceNext;
      server.replaceNext = false;
      return reply(200, { ok: true, nodes: ["/base_driver"], topics: TOPICS, cmd_vel_subscribers: 1, warnings: [], camera: null, worker_replaced: replaced });
    }
    case "/robots/drive":
      if (body.action === "stop") return reply(200, { ok: true, action: "stop", streaming: false, link: server.stopLink });
      return reply(200, { ok: true, action: body.action, streaming: true, link: { stop: "idle", reporting: true, seq: 2 } });
    case "/robots/drive/keepalive":
      return server.keepaliveFails ? reply(502, { ok: false, error: "The backend is not driving this robot any more.", code: "worker_gone" }) : reply(200, { ok: true, streaming: true, link: { stop: "idle", reporting: true, seq: 3 } });
    case "/robots/link":
      return reply(200, { ok: true, link: server.link });
    case "/robots/status":
      return reply(200, { ok: true, nodes: ["/base_driver"], link: server.link, worker_replaced: false });
    default:
      return reply(200, { ok: true });
  }
}

beforeEach(() => {
  server = { calls: [], link: { stop: "idle", reporting: true, seq: 1 }, stopLink: { stop: "pending", reporting: true, seq: 20 }, keepaliveFails: false, replaceNext: false };
  localStorage.setItem("hive:fleet:manual-rc-v1", JSON.stringify([{ id: "r1", name: "Rover", preset: "lidarRcCar", host: "10.0.0.5", domainId: 43 }]));
  Element.prototype.scrollTo = () => {};
  Element.prototype.scrollIntoView = () => {};
  HTMLCanvasElement.prototype.getContext = () => null; // the LiDAR panel draws only when it gets one
  vi.stubGlobal("fetch", vi.fn(async (url, init) => fakeFetch(url, init)));
  setEventSourceFactory((url) => new FakeSource(url));
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  setEventSourceFactory(null);
  FakeSource.instances = [];
});

async function openAndConnect() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Open Rover" }));
  fireEvent.click(screen.getByRole("button", { name: "Connect Rover" }));
  await screen.findByText(/Found 1 node and 6 topics/);
}

const mode = () => screen.getByTestId("robot-mode").textContent;
const emitLink = (link) => act(() => liveSources()[0].emit("link", { reporting: true, other_publishers: 0, ...link }));

describe("App car page", () => {
  it("streams only the stage topic and the link topics on one EventSource; a watch toggle reopens it (R26)", async () => {
    await openAndConnect();
    expect(liveSources()).toHaveLength(1);
    expect(streamed(liveSources()[0])).toEqual(["/scan", "/ros_data", "/esp_data"]);
    const tfRow = screen.getByRole("button", { name: "Watch /tf" });
    expect(tfRow.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(tfRow);
    expect(liveSources()).toHaveLength(1);
    expect(streamed(liveSources()[0])).toEqual(["/scan", "/ros_data", "/esp_data", "/tf"]);
    fireEvent.click(screen.getByRole("button", { name: "Watch /tf" }));
    expect(streamed(liveSources()[0])).toEqual(["/scan", "/ros_data", "/esp_data"]);
    fireEvent.click(screen.getByRole("tab", { name: "3D model" }));
    expect(liveSources()).toHaveLength(1);
    expect(streamed(liveSources()[0])).toEqual(["/ros_data", "/esp_data"]); // nothing on stage: link topics only
    expect(FakeSource.instances.length).toBeGreaterThan(1);
  });

  it("shows the car's confirmed line while driving and the not-reporting warning whenever reporting is false (#6)", async () => {
    await openAndConnect();
    emitLink({ stop: "idle", last_line: "127,1,127,1\n", last_line_age_ms: 40, seq: 10 });
    expect(screen.getByText("Car confirmed 127,1,127,1 · 40 ms ago")).toBeTruthy();
    emitLink({ stop: "idle", reporting: false, seq: 11 });
    expect(screen.getByText("Car is not reporting motor commands: is motor_commands running?")).toBeTruthy();
  });

  it("never says Stopped before the car confirms, and a stale link never overrides a newer one (#5, R28)", async () => {
    await openAndConnect();
    emitLink({ stop: "idle", last_line: "127,1,127,1", last_line_age_ms: 30, seq: 10 });
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(server.calls.some((c) => c.path === "/robots/drive" && c.body.action === "stop")).toBe(true));
    await waitFor(() => expect(mode()).toContain("Stopping…"));
    emitLink({ stop: "confirmed", last_line: "0,1,0,1", seq: 15 }); // older than the stop reply (seq 20)
    expect(mode()).toContain("Stopping…");
    expect(screen.queryByText(/Car confirmed 0,1,0,1/)).toBeNull();
    emitLink({ stop: "unconfirmed", seq: 25 });
    expect(mode()).toContain("Stop not confirmed: car may still be moving");
    expect(screen.getByRole("alert").textContent).toBe("Stop not confirmed: car may still be moving");
    emitLink({ stop: "confirmed", last_line: "0,1,0,1", last_line_age_ms: 3, seq: 30 });
    expect(mode()).toContain("Stopped");
    expect(screen.getByText("Car confirmed 0,1,0,1 · 3 ms ago")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a reconnect that replaced a crashed worker says a stop was sent (R29)", async () => {
    await openAndConnect();
    server.keepaliveFails = true;
    server.replaceNext = true;
    fireEvent.keyDown(window, { key: "w" });
    await waitFor(() => expect(mode()).toContain("Reconnected — stop sent"), { timeout: 3000 });
    expect(screen.getByText(/sent the car a stop/)).toBeTruthy();
    expect(liveSources()).toHaveLength(1);
    fireEvent.keyUp(window, { key: "w" });
  });
});

describe("App Home", () => {
  it("a car whose stop is not confirmed gets a danger badge on its Home card, from the link poll (#5)", async () => {
    render(<App />);
    const card = screen.getByRole("button", { name: "Open Rover" }).closest("article");
    fireEvent.click(within(card).getByRole("button", { name: "Connect Rover" }));
    await waitFor(() => expect(within(card).getByText("Live")).toBeTruthy());
    expect(liveSources()).toHaveLength(0); // no car page open, no stream
    server.link = { stop: "unconfirmed", reporting: true, seq: 25 };
    const dock = screen.getByRole("region", { name: "Driving Rover" });
    fireEvent.click(within(dock).getByRole("button", { name: "Stop" }));
    expect(await within(card).findByText("Stop not confirmed", {}, { timeout: 2000 })).toBeTruthy();
    expect(within(dock).getByText("Stop not confirmed: car may still be moving")).toBeTruthy();
    server.link = { stop: "confirmed", reporting: true, seq: 30 };
  });
});

describe("App change address", () => {
  const dialog = () => screen.getByRole("dialog", { name: "Change address" });
  const fill = (host, domain) => {
    fireEvent.change(within(dialog()).getByLabelText("Robot IP address"), { target: { value: host } });
    fireEvent.change(within(dialog()).getByLabelText("ROS domain ID"), { target: { value: String(domain) } });
  };
  const save = () => fireEvent.click(within(dialog()).getByRole("button", { name: "Save address" }));
  const saved = () => JSON.parse(localStorage.getItem("hive:fleet:manual-rc-v1"));
  const homeCard = (name) => screen.getByRole("button", { name: `Open ${name}` }).closest("article");

  it("changes an offline car's IP and domain from its Home card, keeps it, and Connect uses the new address", async () => {
    render(<App />);
    fireEvent.click(within(homeCard("Rover")).getByRole("button", { name: "Change address of Rover" }));
    expect(within(dialog()).getByLabelText("Robot IP address").value).toBe("10.0.0.5");
    expect(within(dialog()).getByLabelText("ROS domain ID").value).toBe("43");
    fill(" 10.0.0.9 ", 45);
    save();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(homeCard("Rover")).getByText("10.0.0.9")).toBeTruthy();
    expect(within(homeCard("Rover")).getByText("domain 45")).toBeTruthy();
    expect(saved()[0]).toMatchObject({ host: "10.0.0.9", domainId: 45 });
    expect(server.calls.some((c) => c.path === "/robots/disconnect")).toBe(false); // it was not connected
    fireEvent.click(within(homeCard("Rover")).getByRole("button", { name: "Connect Rover" }));
    await waitFor(() => expect(within(homeCard("Rover")).getByText("Live")).toBeTruthy());
    expect(server.calls.find((c) => c.path === "/robots/connect").body).toEqual({ ip: "10.0.0.9", domain_id: 45 });
  });

  it("refuses a domain another car uses, a bad address and a bad domain, and Cancel changes nothing", () => {
    localStorage.setItem(
      "hive:fleet:manual-rc-v1",
      JSON.stringify([
        { id: "r1", name: "Rover", preset: "lidarRcCar", host: "10.0.0.5", domainId: 43 },
        { id: "r2", name: "Grip", preset: "gripperRcCar", host: "10.0.0.6", domainId: 44 },
      ]),
    );
    render(<App />);
    fireEvent.click(within(homeCard("Rover")).getByRole("button", { name: "Change address of Rover" }));
    fill("10.0.0.5", 44);
    save();
    expect(within(dialog()).getByRole("alert").textContent).toBe("Domain 44 is already used by Grip. Pick another, such as 42.");
    fill("not an ip!", 43);
    save();
    expect(within(dialog()).getByRole("alert").textContent).toBe("Enter an IPv4 address or hostname, such as 192.168.1.20.");
    fill("", 43);
    save();
    expect(within(dialog()).getByRole("alert").textContent).toBe("Enter an IPv4 address or hostname, such as 192.168.1.20.");
    for (const bad of ["300", "", "4.5", "-1"]) {
      fill("10.0.0.7", bad);
      save();
      expect(within(dialog()).getByRole("alert").textContent).toBe("The ROS domain ID is a whole number from 0 to 232.");
    }
    fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(homeCard("Rover")).getByText("10.0.0.5")).toBeTruthy();
    expect(saved()[0]).toMatchObject({ host: "10.0.0.5", domainId: 43 });
  });

  it("a connected car is stopped and disconnected at its old address before the new one is used", async () => {
    await openAndConnect();
    fireEvent.click(screen.getByRole("button", { name: "Change address of Rover" }));
    expect(within(dialog()).getByText(/stops and disconnects Rover/)).toBeTruthy();
    fill("10.0.0.9", 43);
    save();
    await waitFor(() => expect(server.calls.some((c) => c.path === "/robots/disconnect")).toBe(true));
    expect(server.calls.filter((c) => c.path === "/robots/disconnect").map((c) => c.body)).toEqual([{ ip: "10.0.0.5", domain_id: 43 }]);
    expect(screen.getByText("Not connected")).toBeTruthy();
    expect(screen.getByText("10.0.0.9")).toBeTruthy();
    expect(screen.getByText(/Address changed from 10\.0\.0\.5 \(domain 43\) to 10\.0\.0\.9 \(domain 43\)/)).toBeTruthy();
    expect(liveSources()).toHaveLength(0);
    expect(saved()[0]).toMatchObject({ host: "10.0.0.9", domainId: 43, status: "offline" });
  });

  it("saving the same address leaves a connected car alone, drive keys do nothing while it is open, and Escape closes it", async () => {
    await openAndConnect();
    fireEvent.click(screen.getByRole("button", { name: "Change address of Rover" }));
    save();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Change address of Rover" }));
    fireEvent.keyDown(window, { key: "w" });
    fireEvent.keyUp(window, { key: "w" });
    expect(server.calls.some((c) => c.path === "/robots/drive")).toBe(false);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(server.calls.some((c) => c.path === "/robots/disconnect")).toBe(false);
    expect(screen.getByText(/Found 1 node and 6 topics/)).toBeTruthy();
  });
});
