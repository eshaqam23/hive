// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import SharedMap from "./SharedMap.jsx";
import { SCHEMA } from "./mapContract.js";

beforeEach(() => {
  HTMLCanvasElement.prototype.getContext = () => null; // jsdom has no 2D canvas; the occupancy layer is skipped
});
afterEach(cleanup);

const notBuilt = { available: false, reason: "shared perception not built yet", schema: SCHEMA };
const fleet = [
  { id: "r1", name: "Rover", preset: "lidarRcCar", host: "10.0.0.5", domainId: 43, status: "connected" },
  { id: "r2", name: "Cam", preset: "cameraRcCar", host: "10.0.0.6", domainId: 45, status: "offline" },
];
const execState = {
  robots: [
    { robot_id: "lidar_01", preset: "lidarRcCar" },
    { robot_id: "camera_01", preset: "cameraRcCar" },
  ],
};
const builtMap = {
  available: true,
  schema: SCHEMA,
  occupancy: { resolution: 0.5, origin: { x: 0, y: 0, theta: 0 }, width: 2, height: 2, data: [0, 100, -1, 0], revision: 7, epoch: "e1" },
  robots: [{ robot_id: "lidar_01", pose: { x: 1.25, y: 0.5, theta: 0 }, source: "odom", age_s: 0.4 }],
  objects: [],
  regions: [],
};
const page = (props = {}) => render(<SharedMap execState={execState} fleet={fleet} map={notBuilt} reachable {...props} />);

describe("SharedMap", () => {
  it("shows the designed empty state while the map is not built, with the full layout around it", () => {
    page();
    expect(screen.getByText("Shared perception map not built yet")).toBeTruthy();
    expect(screen.getByText(/LiDAR \/scan, camera detections and robot poses/)).toBeTruthy();
    for (const layer of ["Occupancy", "LiDAR scans", "Robots", "Objects", "Regions"]) {
      expect(screen.getByRole("button", { name: layer }).getAttribute("aria-pressed")).toBe("true");
    }
    const legend = screen.getByRole("list", { name: "Legend" });
    for (const key of ["free", "occupied", "unknown", "frontier"]) expect(within(legend).getByText(key)).toBeTruthy();
    expect(screen.getByTestId("known").textContent).toBe("—");
    expect(screen.getByTestId("revision").textContent).toBe("rev — · epoch —");
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeTruthy();
    expect(screen.getByText(/\d+ (m|cm)$/, { selector: "text" })).toBeTruthy(); // the scale bar
  });

  it("lists every car with pose unknown, and what each would feed the map", () => {
    page();
    const robots = screen.getByRole("list", { name: "Robots on the map" });
    const rover = within(robots).getByText("Rover").closest("li");
    expect(within(rover).getByText("lidar_01")).toBeTruthy();
    expect(within(rover).getByText("pose: unknown")).toBeTruthy();
    expect(within(rover).getByText("no source")).toBeTruthy();
    expect(within(rover).getByText("age —")).toBeTruthy();
    const sources = screen.getByRole("list", { name: "Sources" });
    const roverSource = within(sources).getByText("Rover").closest("li");
    expect(within(roverSource).getByText(/LiDAR \/scan/)).toBeTruthy();
    expect(within(roverSource).getByText("online")).toBeTruthy();
    const camSource = within(sources).getByText("Cam").closest("li");
    expect(within(camSource).getByText(/camera detections/)).toBeTruthy();
    expect(within(camSource).getByText("offline")).toBeTruthy();
  });

  it("says when the Executive cannot be asked", () => {
    page({ map: null, reachable: false });
    expect(screen.getByText("Shared perception map not built yet")).toBeTruthy();
    expect(screen.getByText(/Executive offline/)).toBeTruthy();
  });

  it("has a demo toggle, off by default, that draws the fixture labeled DEMO", () => {
    page({ demoAllowed: true });
    const toggle = screen.getByRole("button", { name: /Demo data/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByText("DEMO")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getAllByText("DEMO").length).toBeGreaterThan(0);
    expect(screen.getByTestId("demo-watermark").textContent).toBe("DEMO");
    expect(screen.queryByText("Shared perception map not built yet")).toBeNull();
    expect(screen.getByTestId("known").textContent).toMatch(/^\d+%$/);
    expect(screen.getByTestId("revision").textContent).toBe("rev 42 · epoch demo-1");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("demo-watermark")).toBeNull();
  });

  it("has no demo toggle outside development", () => {
    page({ demoAllowed: false });
    expect(screen.queryByRole("button", { name: /Demo data/ })).toBeNull();
  });

  it("draws a real map's robots with their pose, source and age, and layers toggle", () => {
    page({ map: builtMap });
    expect(screen.queryByText("Shared perception map not built yet")).toBeNull();
    expect(screen.getByTestId("known").textContent).toBe("75%");
    expect(screen.getByTestId("revision").textContent).toBe("rev 7 · epoch e1");
    const rover = within(screen.getByRole("list", { name: "Robots on the map" })).getByText("Rover").closest("li");
    expect(within(rover).getByText("pose: 1.25, 0.50 m · 0°")).toBeTruthy();
    expect(within(rover).getByText("odom")).toBeTruthy();
    expect(within(rover).getByText("0.4 s")).toBeTruthy();
    expect(screen.getByTestId("robot-marker-lidar_01")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Robots" }));
    expect(screen.getByRole("button", { name: "Robots" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByTestId("robot-marker-lidar_01")).toBeNull();
  });

  it("compact mode is the canvas alone, for Chat's mini map", () => {
    page({ compact: true, demoAllowed: true });
    expect(screen.getByText("Shared map not built yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Occupancy" })).toBeNull();
    expect(screen.queryByRole("list", { name: "Sources" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Demo data/ })).toBeNull();
  });
});
