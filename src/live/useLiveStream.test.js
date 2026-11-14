// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
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

describe("useLiveStream resilience", () => {
  it("keeps exactly one EventSource and reconnects with backoff after errors", () => {
    vi.useFakeTimers();
    try {
      setEventSourceFactory((url) => new FakeSource(url));
      const { result } = renderHook(() => useLiveStream({ base: "b", robot, topics: ["/a", "/b", "/c", "/d", "/e", "/f", "/g"], enabled: true }));
      const live = () => FakeSource.instances.filter((s) => !s.closed);
      expect(live()).toHaveLength(1);
      act(() => FakeSource.instances[0].onerror());
      expect(result.current.error).toMatch(/reconnecting/);
      expect(live()).toHaveLength(0);
      act(() => vi.advanceTimersByTime(999));
      expect(FakeSource.instances).toHaveLength(1);
      act(() => vi.advanceTimersByTime(1));
      expect(live()).toHaveLength(1);
      act(() => FakeSource.instances[1].onerror());
      act(() => vi.advanceTimersByTime(1999));
      expect(FakeSource.instances).toHaveLength(2);
      act(() => vi.advanceTimersByTime(1));
      act(() => FakeSource.instances[2].onerror());
      act(() => vi.advanceTimersByTime(5000));
      expect(FakeSource.instances).toHaveLength(4);
      act(() => FakeSource.instances[3].emit("link", { stop: "idle" }));
      expect(result.current.error).toBeNull();
      expect(live()).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not reconnect after unmount", () => {
    vi.useFakeTimers();
    try {
      setEventSourceFactory((url) => new FakeSource(url));
      const { unmount } = renderHook(() => useLiveStream({ base: "b", robot, topics: ["/a"], enabled: true }));
      act(() => FakeSource.instances[0].onerror());
      unmount();
      act(() => vi.advanceTimersByTime(10000));
      expect(FakeSource.instances).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("collects image stats, stale flags and per-topic errors from the one stream", () => {
    setEventSourceFactory((url) => new FakeSource(url));
    const { result } = renderHook(() => useLiveStream({ base: "b", robot, topics: ["/a"], images: ["/cam"], enabled: true }));
    expect(FakeSource.instances).toHaveLength(1);
    const src = FakeSource.instances[0];
    expect(src.url).toContain("images=%2Fcam");
    act(() => src.emit("image_stats", { topic: "/cam", hz: 14.5, bytes_per_s: 120000, width: 640, height: 480 }));
    expect(result.current.imageStats["/cam"].width).toBe(640);
    act(() => src.emit("status", { topic: "/cam", stale: true }));
    expect(result.current.stale["/cam"]).toBe(true);
    act(() => src.emit("status", { topic: "/tf", error: "watch_failed" }));
    expect(result.current.topicErrors["/tf"]).toBe("watch_failed");
    expect(result.current.stale["/tf"]).toBeUndefined();
    act(() => src.listeners.data({ data: "not json" }));
    expect(Object.keys(result.current.payloads)).toHaveLength(0);
  });

  it("opens a stream for image stats alone", () => {
    setEventSourceFactory((url) => new FakeSource(url));
    renderHook(() => useLiveStream({ base: "b", robot, topics: [], images: ["/cam"], enabled: true }));
    expect(FakeSource.instances).toHaveLength(1);
    expect(FakeSource.instances[0].url).toContain("topics=&images=");
  });
});
