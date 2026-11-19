// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExecutive } from "./useExecutive.js";

const reply = (data) => ({ ok: true, status: 200, json: async () => data });

let visibility = "visible";
let calls = [];
let pending = [];

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  calls = [];
  pending = [];
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  vi.stubGlobal(
    "fetch",
    vi.fn((url) => {
      const path = String(url).split("/api/exec")[1];
      calls.push(path);
      if (path === "/health") return Promise.resolve(reply({ ok: true }));
      return new Promise((resolve) => pending.push((data) => resolve(reply(data))));
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const stateCalls = () => calls.filter((c) => c === "/state").length;
const flush = () => act(async () => {});

describe("useExecutive", () => {
  it("polls state at the given period and pauses while the tab is hidden", async () => {
    renderHook(() => useExecutive({ stateMs: 500, pauseHidden: true }));
    expect(stateCalls()).toBe(1);
    await act(async () => pending.shift()({ n: 1 }));
    await act(async () => vi.advanceTimersByTimeAsync(500));
    expect(stateCalls()).toBe(2);

    visibility = "hidden";
    await act(async () => pending.shift()({ n: 2 }));
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(stateCalls()).toBe(2); // parked

    visibility = "visible";
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(stateCalls()).toBe(3); // resumed at once
  });

  it("keeps polling while hidden without pauseHidden", async () => {
    renderHook(() => useExecutive({ stateMs: 500 }));
    visibility = "hidden";
    await act(async () => pending.shift()({ n: 1 }));
    await act(async () => vi.advanceTimersByTimeAsync(500));
    expect(stateCalls()).toBe(2);
  });

  it("accept() replaces state and drops a poll that started before it", async () => {
    const { result } = renderHook(() => useExecutive({ stateMs: 500 }));
    await act(async () => pending.shift()({ v: "first" }));
    expect(result.current.state).toEqual({ v: "first" });

    await act(async () => vi.advanceTimersByTimeAsync(500)); // second poll in flight
    act(() => result.current.accept({ v: "posted" }));
    expect(result.current.state).toEqual({ v: "posted" });
    await act(async () => pending.shift()({ v: "stale" }));
    expect(result.current.state).toEqual({ v: "posted" });

    await act(async () => vi.advanceTimersByTimeAsync(500));
    await act(async () => pending.shift()({ v: "next" }));
    expect(result.current.state).toEqual({ v: "next" });
    act(() => result.current.accept(null));
    expect(result.current.state).toEqual({ v: "next" });
    await flush();
  });
});
