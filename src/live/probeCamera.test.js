import { describe, expect, it, vi } from "vitest";
import { probeCamera } from "./probeCamera.js";

const res = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

describe("probeCamera", () => {
  it("409 -> blocked with the backend reason, then forceFull re-probe -> ready", async () => {
    const fetchFn = vi.fn(async (url) => (url.includes("force_full=1") ? res(200) : res(409, { code: "camera_untuned", reason: "set failed" })));
    const first = await probeCamera(fetchFn, "http://h/cam");
    expect(first).toEqual({ state: "blocked", reason: "set failed" });
    const abort = vi.fn();
    const second = await probeCamera(fetchFn, "http://h/cam&force_full=1", { abort });
    expect(second).toEqual({ state: "ready" });
    expect(abort).toHaveBeenCalledOnce();
  });

  it("409 without a reason still blocks with a default message", async () => {
    const r = await probeCamera(async () => ({ ok: false, status: 409, json: async () => { throw new Error("x"); } }), "u");
    expect(r.state).toBe("blocked");
    expect(r.reason).toMatch(/not tuned/);
  });

  it("other statuses and network failures are errors; aborts are quiet", async () => {
    expect(await probeCamera(async () => res(400, { error: "use_sse" }), "u")).toEqual({ state: "error", reason: "use_sse" });
    expect((await probeCamera(async () => res(500, {}), "u")).reason).toMatch(/500/);
    expect((await probeCamera(async () => { throw new TypeError("net"); }, "u")).state).toBe("error");
    const abortError = Object.assign(new Error("a"), { name: "AbortError" });
    expect(await probeCamera(async () => { throw abortError; }, "u")).toEqual({ state: "aborted" });
  });
});
