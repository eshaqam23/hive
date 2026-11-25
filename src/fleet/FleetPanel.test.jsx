// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("../robotModels.js", () => ({
  PRESET_MODELS: { cameraRcCar: { label: "Camera car" }, lidarRcCar: { label: "LiDAR car" }, gripperRcCar: { label: "Gripper car" } },
  snapshot: () => new Promise(() => {}),
}));

import { FleetPanel } from "./FleetView.jsx";

afterEach(cleanup);

const rover = (over = {}) => ({ id: "r1", name: "Rover", preset: "lidarRcCar", host: "10.0.0.5", domainId: 43, status: "offline", ...over });
const lidarEntry = {
  robot_id: "lidar_01",
  display_name: "LiDAR RC Car",
  preset: "lidarRcCar",
  state: { location_id: "lidar_post", battery_pct: 64, health: "ok", available: true },
};
const execState = (over = {}) => ({
  reachable: true,
  state: {
    gateway: { kind: "fake", label: "Fake fleet (in-process simulation)" },
    catalog: { locations: [{ location_id: "lidar_post", label: "LiDAR post" }] },
    robots: [lidarEntry],
    mission: null,
    ...over,
  },
});
const handlers = () => ({ onOpen: vi.fn(), onConnect: vi.fn(), onDisconnect: vi.fn(), onRemove: vi.fn(), onEdit: vi.fn() });
const panel = (props) => {
  const h = handlers();
  render(<FleetPanel exec={execState()} link={null} {...h} {...props} />);
  return { h, card: screen.getByRole("button", { name: "Open Rover" }).closest("article") };
};

describe("FleetPanel", () => {
  it.each([
    [{ status: "connected" }, "online", "Live"],
    [{ status: "offline" }, "offline", "Offline"],
    [{ status: "connecting" }, "connecting", "Connecting…"],
    [{ status: "connecting", reconnecting: true }, "reconnecting", "Reconnecting…"],
    [{ status: "failed" }, "lost", "Failed"],
  ])("shows link state %o as %s", (over, state, badge) => {
    const { card } = panel({ robot: rover(over) });
    expect(within(card).getByTestId("link-state").textContent).toBe(state);
    expect(within(card).getByText(badge)).toBeTruthy();
  });

  it("shows name, preset, address, domain, last discovery and sensors", () => {
    const discovery = { nodes: ["/base_driver"], topics: [{ name: "/scan" }, { name: "/cmd_vel" }], cmdVelSubscribers: 1, at: 1 };
    const { card } = panel({ robot: rover({ discovery }) });
    expect(within(card).getByText("Rover")).toBeTruthy();
    expect(within(card).getByText("LiDAR RC Car")).toBeTruthy();
    expect(within(card).getByText("10.0.0.5")).toBeTruthy();
    expect(within(card).getByText("domain 43")).toBeTruthy();
    expect(within(card).getByText("1 node · 2 topics")).toBeTruthy();
    expect(within(card).getByText("LiDAR")).toBeTruthy();
  });

  it("says when it has never been connected", () => {
    const { card } = panel({ robot: rover() });
    expect(within(card).getByText("not connected yet")).toBeTruthy();
  });

  it("connect and disconnect call live-data's handlers; the panel opens the robot page", () => {
    const { h, card } = panel({ robot: rover() });
    fireEvent.click(within(card).getByRole("button", { name: "Connect Rover" }));
    expect(h.onConnect).toHaveBeenCalledWith(expect.objectContaining({ id: "r1" }));
    expect(h.onOpen).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Open Rover" }));
    expect(h.onOpen).toHaveBeenCalledTimes(1);
    cleanup();
    const live = panel({ robot: rover({ status: "connected" }) });
    fireEvent.click(within(live.card).getByRole("button", { name: "Disconnect Rover" }));
    expect(live.h.onDisconnect).toHaveBeenCalledTimes(1);
  });

  it("shows Stop not confirmed in red while the car has not confirmed a stop", () => {
    const { card } = panel({ robot: rover({ status: "connected" }), link: { stop: "unconfirmed", seq: 3 } });
    const badge = within(card).getByText("Stop not confirmed");
    expect(badge.getAttribute("role")).toBe("status");
    expect(badge.className).toContain("bg-danger");
    expect(card.className).toContain("border-danger");
  });

  it("tags the Executive's status SIM on the fake fleet, with battery and location", () => {
    const { card } = panel({ robot: rover() });
    const block = within(card).getByRole("group", { name: "Executive status for lidar_01" });
    expect(within(block).getByText("SIM").getAttribute("title")).toMatch(/Simulated/);
    expect(within(block).getByText("idle")).toBeTruthy();
    expect(within(block).getByText("LiDAR post")).toBeTruthy();
    expect(within(block).getByRole("meter", { name: "Battery (simulated)" }).getAttribute("aria-valuenow")).toBe("64");
  });

  it("tags an emulated agent fleet EMULATED and says battery unknown when the agent sends none", () => {
    const fleet = { schema: "hive.fleet-report.v1", kind: "hive_agent", profile: "laptop", evidence: "emulated", agent_commit: "fc1fe24" };
    const entry = { ...lidarEntry, state: { location_id: "lidar_post", health: "ok", available: true } };
    const exec = execState({ gateway: { kind: "synapse", label: "AgenticROS / Corebrum Synapse", simulated: true, fleet }, robots: [entry] });
    render(<FleetPanel exec={exec} link={null} robot={rover()} {...handlers()} />);
    const block = screen.getByRole("group", { name: "Executive status for lidar_01" });
    expect(within(block).getByText("EMULATED").getAttribute("title")).toMatch(/Emulated/);
    expect(within(block).queryByText("SIM")).toBeNull();
    expect(within(block).getByText("battery unknown")).toBeTruthy();
    expect(within(block).getByRole("meter", { name: "Battery (emulated)" }).getAttribute("aria-valuenow")).toBeNull();
  });

  it("shows no tag for a real agent fleet", () => {
    const fleet = { schema: "hive.fleet-report.v1", kind: "hive_agent", profile: "desktop", evidence: "real", agent_commit: "abc1234" };
    render(<FleetPanel exec={execState({ gateway: { kind: "synapse", simulated: false, fleet } })} link={null} robot={rover()} {...handlers()} />);
    const block = screen.getByRole("group", { name: "Executive status for lidar_01" });
    expect(within(block).queryByText("SIM")).toBeNull();
    expect(within(block).queryByText("EMULATED")).toBeNull();
    expect(within(block).getByRole("meter", { name: "Battery" }).getAttribute("aria-valuenow")).toBe("64");
  });

  it("names the task and skill while executing, with the command line", () => {
    const mission = {
      mission_id: "m1",
      status: "executing",
      active_revision: 1,
      revisions: [
        {
          revision: 1,
          tasks: [{ task_id: "T1", robot_id: "lidar_01", skill: "navigate_to", agent_command: { line: 'navigate_to(location="work_area")' } }],
          runtime: { T1: { state: "running" } },
        },
      ],
    };
    render(<FleetPanel exec={execState({ mission })} link={null} robot={rover()} {...handlers()} />);
    const block = screen.getByRole("group", { name: "Executive status for lidar_01" });
    expect(within(block).getByText("executing T1 navigate_to")).toBeTruthy();
    expect(within(block).getByText('navigate_to(location="work_area")')).toBeTruthy();
  });

  it("says Not in Executive catalog without a mapping, and unavailable while the Executive is offline", () => {
    const { card } = panel({ robot: rover({ preset: "gripperRcCar" }) });
    expect(within(card).getByText("Not in Executive catalog")).toBeTruthy();
    expect(within(card).queryByText("SIM")).toBeNull();
    cleanup();
    render(<FleetPanel exec={{ reachable: false, state: null }} link={null} robot={rover()} {...handlers()} />);
    expect(screen.getByText("unavailable")).toBeTruthy();
    expect(screen.getByText("Executive offline")).toBeTruthy();
  });
});
