// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../RobotViewer.jsx", () => ({ default: () => <div data-testid="model" /> }));
vi.mock("../viewers/panels.js", () => ({
  PANELS: {
    CameraPanel: ({ stats, stale }) => <div data-testid="camera">{stats ? `cam ${stats.hz}` : "cam"}{stale ? " stale" : ""}</div>,
    LidarPanel: () => <div data-testid="lidar" />,
    ValuePanel: () => <div data-testid="value" />,
    GenericPanel: () => <div data-testid="generic" />,
  },
}));

import LiveView from "./LiveView.jsx";

afterEach(cleanup);

const topics = [
  { name: "/cam/image_raw", type: "sensor_msgs/msg/Image", kind: "image" },
  { name: "/scan", type: "sensor_msgs/msg/LaserScan", kind: "scan" },
  { name: "/ros_data", type: "std_msgs/msg/String", kind: "value" },
];
const robot = (status = "connected") => ({ id: "r", preset: "cameraRcCar", status, host: "1.2.3.4", domainId: 42, discovery: { topics } });

describe("LiveView", () => {
  it("shows the CAD model and no tabs while not connected", () => {
    render(<LiveView robot={robot("offline")} />);
    expect(screen.getByTestId("model")).toBeTruthy();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("offers Camera, LiDAR and 3D model tabs and switches between them", () => {
    render(<LiveView imageStats={{ "/cam/image_raw": { hz: 14.5 } }} robot={robot()} />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Camera", "LiDAR", "3D model"]);
    expect(screen.getByTestId("camera").textContent).toBe("cam 14.5");
    expect(screen.getByText("● 14.5 Hz")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "LiDAR" }));
    expect(screen.getByTestId("lidar")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "3D model" }));
    expect(screen.getByTestId("model")).toBeTruthy();
  });

  it("opens a clicked topic row in an extra tab and shows hz and MB/s for data topics", () => {
    const payloads = { "/ros_data": { hz: 5, bytes_per_s: 2500000 } };
    render(<LiveView payloads={payloads} robot={robot()} selectedTopic={{ name: "/ros_data" }} />);
    expect(screen.getByTestId("value")).toBeTruthy();
    expect(screen.getByText("● 5.0 Hz")).toBeTruthy();
    expect(screen.getByText("2.50 MB/s from car")).toBeTruthy();
  });

  it("lets the page own the active tab, so the page knows what is on stage (R26)", () => {
    const onActive = vi.fn();
    const { rerender } = render(<LiveView active="/scan" onActive={onActive} robot={robot()} />);
    expect(screen.getByTestId("lidar")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Camera" }));
    expect(onActive).toHaveBeenCalledWith("/cam/image_raw");
    expect(screen.getByTestId("lidar")).toBeTruthy(); // nothing changes until the page says so
    rerender(<LiveView active="3D model" onActive={onActive} robot={robot()} />);
    expect(screen.getByTestId("model")).toBeTruthy();
  });

  it("passes the stale flag and shows the interrupted-stream state", () => {
    render(<LiveView error="Live stream interrupted, reconnecting…" robot={robot()} stale={{ "/cam/image_raw": true }} />);
    expect(screen.getByTestId("camera").textContent).toContain("stale");
    expect(screen.getByRole("status").textContent).toBe("Live stream interrupted, reconnecting…");
  });
});
