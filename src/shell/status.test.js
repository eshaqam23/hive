import { describe, expect, it } from "vitest";
import { elapsed, liveStatus, missionPill } from "./status.js";

const up = { state: "online", ros: { found: true } };
const execUp = { reachable: true, health: { ok: true, ready: true, missing: [], gateway: { connected: true, label: "Synapse" } } };

describe("liveStatus", () => {
  it("is green only when the car backend and the Executive are both healthy", () => {
    expect(liveStatus({ backend: up, exec: execUp })).toEqual({ ok: true, down: [], text: "Car backend and Executive healthy" });
  });

  it("names everything that is down", () => {
    const s = liveStatus({ backend: { state: "offline", ros: null }, exec: { reachable: false, health: null } });
    expect(s.ok).toBe(false);
    expect(s.down).toEqual(["car backend offline (npm run backend)", "Executive offline"]);
    expect(s.text).toBe("car backend offline (npm run backend) · Executive offline");
  });

  it("counts a missing ROS 2, an unready Executive and a disconnected gateway as down", () => {
    expect(liveStatus({ backend: { state: "online", ros: { found: false } }, exec: execUp }).down).toEqual(["car backend: ROS 2 not found"]);
    const unready = { reachable: true, health: { ok: true, ready: false, missing: ["planner", "gateway"] } };
    expect(liveStatus({ backend: up, exec: unready }).down).toEqual(["Executive not ready: planner, gateway"]);
    const noGateway = { reachable: true, health: { ok: true, ready: true, gateway: { connected: false, label: "Synapse" } } };
    expect(liveStatus({ backend: up, exec: noGateway }).down).toEqual(["Executive gateway not connected (Synapse)"]);
  });

  it("is not green while either is still being checked or answers without a health report", () => {
    expect(liveStatus({ backend: { state: "checking" }, exec: { reachable: null } }).down).toEqual(["car backend: checking…", "Executive: checking…"]);
    expect(liveStatus({ backend: up, exec: { reachable: true, health: { ok: true } } }).down).toEqual(["Executive not ready"]);
  });
});

describe("elapsed", () => {
  it("is mm:ss, then h:mm:ss, never negative", () => {
    expect(elapsed(0)).toBe("00:00");
    expect(elapsed(102.9)).toBe("01:42");
    expect(elapsed(3723)).toBe("1:02:03");
    expect(elapsed(-5)).toBe("00:00");
  });
});

describe("missionPill", () => {
  const at = (status, extra = {}) => ({ mission_id: "m1", status, active_revision: 1, created_at: 1000, ...extra });
  it("says mission running · rev N · mm:ss while executing", () => {
    expect(missionPill(at("executing"), 1102_000)).toBe("mission running · rev 1 · 01:42");
    expect(missionPill(at("evaluating", { active_revision: 3 }), 1001_000)).toBe("mission running · rev 3 · 00:01");
  });
  it("names other active statuses and leaves out a revision not made yet", () => {
    expect(missionPill(at("planning", { active_revision: null }), 1005_000)).toBe("mission planning · 00:05");
    expect(missionPill(at("awaiting_approval"), 1000_000)).toBe("mission awaiting approval · rev 1 · 00:00");
    expect(missionPill(at("recovering"), 1000_000)).toBe("mission recovering · rev 1 · 00:00");
    expect(missionPill(at("something_new"), 1000_000)).toBe("mission something new · rev 1 · 00:00");
  });
  it("is nothing without an active mission", () => {
    expect(missionPill(null, 0)).toBeNull();
    expect(missionPill(at("completed"), 0)).toBeNull();
    expect(missionPill(at("answered"), 0)).toBeNull(); // a question answered from the catalog ends the mission
  });
});
