// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { act, renderHook, cleanup } from "@testing-library/react";
import { setEventSourceFactory, useLiveStream } from "./useLiveStream.js";

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

const robot = {
  id: "r1",
  host: "10.0.0.1",
  domainId: 42,
  status: "connected",
  discovery: { topics: [{ name: "/scan", type: "sensor_msgs/msg/LaserScan", kind: "scan" }, { name: "/cam", type: "x", kind: "image" }] },
};

afterEach(() => {
  cleanup();
  FakeSource.instances = [];
  setEventSourceFactory(null);
});

describe("useLiveStream", () => {
  it("stores data payloads, stale status and link, and closes on unmount", () => {
    setEventSourceFactory((url) => new FakeSource(url));
    const { result, unmount } = renderHook(() => useLiveStream({ base: "http://h/api", robot, topics: ["/scan"], enabled: true }));
    expect(FakeSource.instances).toHaveLength(1);
    const src = FakeSource.instances[0];
    expect(src.url).toContain("topics=%2Fscan");
    act(() => src.emit("data", { topic: "/scan", hz: 7.5, data: { a: 1 } }));
    expect(result.current.payloads["/scan"].hz).toBe(7.5);
    act(() => src.emit("status", { topic: "/scan", stale: true }));
    expect(result.current.stale["/scan"]).toBe(true);
    act(() => src.emit("link", { stop: "confirmed" }));
    expect(result.current.link.stop).toBe("confirmed");
    unmount();
    expect(src.closed).toBe(true);
  });

  it("does not open when disabled or not connected or no topics", () => {
    setEventSourceFactory((url) => new FakeSource(url));
    renderHook(() => useLiveStream({ base: "b", robot, topics: ["/scan"], enabled: false }));
    renderHook(() => useLiveStream({ base: "b", robot: { ...robot, status: "offline" }, topics: ["/scan"], enabled: true }));
    renderHook(() => useLiveStream({ base: "b", robot, topics: [], enabled: true }));
    expect(FakeSource.instances).toHaveLength(0);
  });
});

describe("useLiveStream fallback", () => {
  it("splits a refused multi-topic stream into one stream per topic", () => {
    setEventSourceFactory((url) => new FakeSource(url));
    renderHook(() => useLiveStream({ base: "b", robot, topics: ["/a", "/b"], enabled: true }));
    const first = FakeSource.instances[0];
    first.readyState = 2;
    act(() => first.onerror());
    expect(FakeSource.instances).toHaveLength(3);
    expect(FakeSource.instances[1].url).toContain("topics=%2Fa");
    expect(FakeSource.instances[2].url).toContain("topics=%2Fb");
  });
});
