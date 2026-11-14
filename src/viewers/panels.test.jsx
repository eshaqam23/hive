// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import CameraPanel, { statsLine } from "./CameraPanel.jsx";
import LidarPanel from "./LidarPanel.jsx";
import ValuePanel, { compactValue } from "./ValuePanel.jsx";
import GenericPanel from "./GenericPanel.jsx";
import { PANELS } from "./panels.js";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const robot = { host: "10.0.0.5", domainId: 42 };
const topic = { name: "/cam", kind: "image" };
const res = (status, body) => ({ ok: status === 200, status, json: async () => body ?? {} });

describe("CameraPanel", () => {
  it("probes, then shows the img with fps, MB/s, size and the Stale overlay", async () => {
    const fetchFn = vi.fn(async () => res(200));
    vi.stubGlobal("fetch", fetchFn);
    const stats = { hz: 14.52, bytes_per_s: 123456, width: 640, height: 480 };
    const { rerender } = render(<CameraPanel base="http://h/api" robot={robot} stats={stats} topic={topic} />);
    const img = await screen.findByRole("img");
    expect(img.getAttribute("src")).toContain("/robots/camera.mjpg?ip=10.0.0.5&domain_id=42&topic=%2Fcam");
    expect(screen.getByText("14.5 fps · 0.12 MB/s · 640×480")).toBeTruthy();
    expect(screen.queryByText("Stale")).toBeNull();
    rerender(<CameraPanel base="http://h/api" robot={robot} stale stats={stats} topic={topic} />);
    expect(screen.getByText("Stale")).toBeTruthy();
  });

  it("409 shows the reason and the force-full button, which re-probes with force_full", async () => {
    const fetchFn = vi.fn(async (url) => (url.includes("force_full=1") ? res(200) : res(409, { code: "camera_untuned", reason: "could not tune" })));
    vi.stubGlobal("fetch", fetchFn);
    const onForceFull = vi.fn();
    render(<CameraPanel base="http://h/api" onForceFull={onForceFull} robot={robot} topic={topic} />);
    expect(await screen.findByText("could not tune")).toBeTruthy();
    fireEvent.click(screen.getByText("Stream full size anyway (~27 MB/s, may disrupt driving)"));
    await waitFor(() => expect(screen.getByRole("img").getAttribute("src")).toContain("force_full=1"));
    expect(onForceFull).toHaveBeenCalled();
    expect(fetchFn.mock.calls[1][0]).toContain("force_full=1");
  });

  it("an img error shows a retry state that probes again", async () => {
    const fetchFn = vi.fn(async () => res(200));
    vi.stubGlobal("fetch", fetchFn);
    render(<CameraPanel base="b" robot={robot} topic={topic} />);
    fireEvent.error(await screen.findByRole("img"));
    expect(await screen.findByText("The camera stream stopped.")).toBeTruthy();
    fireEvent.click(screen.getByText("Retry"));
    await screen.findByRole("img");
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("aborts the probe on unmount", async () => {
    let signal;
    vi.stubGlobal("fetch", (url, init) => {
      signal = init.signal;
      return new Promise(() => {});
    });
    const { unmount } = render(<CameraPanel base="b" robot={robot} topic={topic} />);
    expect(screen.getByText("Starting camera…")).toBeTruthy();
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it("statsLine tolerates missing fields", () => {
    expect(statsLine(null)).toBe("");
    expect(statsLine({ hz: 5 })).toBe("5.0 fps");
  });
});

describe("LidarPanel", () => {
  it("draws on the canvas and reads out the nearest obstacle", () => {
    const calls = { fillRect: 0, arc: 0 };
    const ctx = new Proxy({}, { get: (_, name) => (name === "fillRect" ? () => (calls.fillRect += 1) : name === "arc" ? () => (calls.arc += 1) : () => {}), set: () => true });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const scan = { angle_min: 0, angle_increment: 0.1, ranges_mm: [3000, 1500, 0] };
    render(<LidarPanel payload={{ data: scan }} stale={false} />);
    expect(screen.getByText("Nearest 1.50 m at 6°")).toBeTruthy();
    expect(calls.fillRect).toBe(2);
    expect(calls.arc).toBe(6);
  });

  it("says it is waiting without a scan", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    render(<LidarPanel />);
    expect(screen.getByText("Waiting for a scan…")).toBeTruthy();
  });

  it("reports an empty scan", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    render(<LidarPanel payload={{ data: { ranges_mm: [0, 0] } }} />);
    expect(screen.getByText("No obstacle in range")).toBeTruthy();
  });
});

describe("ValuePanel and GenericPanel", () => {
  it("compacts single-field values and waits for data", () => {
    expect(compactValue({ data: "0,1,0,1" })).toBe("0,1,0,1");
    expect(compactValue({ a: 1, b: 2 })).toBe('{"a":1,"b":2}');
    expect(compactValue(null)).toBe("");
    expect(compactValue(7)).toBe("7");
    render(<ValuePanel payload={{ data: { data: "OK" } }} />);
    expect(screen.getByText("OK")).toBeTruthy();
    cleanup();
    render(<ValuePanel />);
    expect(screen.getByText("Waiting for data…")).toBeTruthy();
  });

  it("shows generic JSON and its note", () => {
    render(<GenericPanel payload={{ data: { a: 1 }, note: "truncated" }} topic={{ name: "/tf" }} />);
    expect(screen.getByText("truncated")).toBeTruthy();
    expect(screen.getByText(/"a": 1/)).toBeTruthy();
    cleanup();
    render(<GenericPanel topic={{ name: "/tf" }} />);
    expect(screen.getByText("Waiting for /tf…")).toBeTruthy();
  });

  it("maps every panel name", () => {
    expect(Object.keys(PANELS).sort()).toEqual(["CameraPanel", "GenericPanel", "LidarPanel", "ValuePanel"]);
  });
});
