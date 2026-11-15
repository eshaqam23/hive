import { describe, expect, it } from "vitest";
import { ddsPorts, describeLink, displayMode, newerLink, NOT_REPORTING, stopUnconfirmed } from "./link.js";

describe("describeLink", () => {
  it("null", () => expect(describeLink(null)).toEqual({ level: "muted", text: "No link data yet" }));
  it("confirmed", () => {
    expect(describeLink({ stop: "confirmed", last_line: "stop\n", last_line_age_ms: 12.6, other_publishers: 0 })).toEqual({
      level: "ok",
      text: "Car confirmed stop · 13 ms ago",
    });
  });
  it("pending", () => expect(describeLink({ stop: "pending" })).toEqual({ level: "muted", text: "Stopping…" }));
  it("unconfirmed", () =>
    expect(describeLink({ stop: "unconfirmed" })).toEqual({ level: "danger", text: "Stop not confirmed: car may still be moving" }));
  it("unreported", () => expect(describeLink({ stop: "unreported" })).toEqual({ level: "warn", text: NOT_REPORTING }));
  it("idle with no line yet is Ready", () => expect(describeLink({ stop: "idle" })).toEqual({ level: "muted", text: "Ready" }));
  it("unknown stop falls back to Ready", () => expect(describeLink({}).text).toBe("Ready"));
  it("appends other publishers", () => {
    expect(describeLink({ stop: "pending", other_publishers: 2 }).text).toBe("Stopping… · Someone else is also driving this car");
    expect(describeLink({ stop: "idle", other_publishers: 1 }).text).toBe("Ready · Someone else is also driving this car");
  });
  it("confirmed tolerates missing fields", () => {
    expect(describeLink({ stop: "confirmed" }).text).toBe("Car confirmed  · 0 ms ago");
  });

  // final audit #6: the car's confirmation shows while driving, and "not reporting" whenever it is true
  it("while driving (idle) shows the line the car confirmed and its age", () => {
    expect(describeLink({ stop: "idle", last_line: "127,1,127,1\n", last_line_age_ms: 40, reporting: true })).toEqual({
      level: "ok",
      text: "Car confirmed 127,1,127,1 · 40 ms ago",
    });
  });
  it("an old confirmation while idle is shown muted, not green", () => {
    expect(describeLink({ stop: "idle", last_line: "0,1,0,1", last_line_age_ms: 9000, reporting: true }).level).toBe("muted");
  });
  it("shows the not-reporting warning whenever reporting is false, also while driving", () => {
    expect(describeLink({ stop: "idle", reporting: false })).toEqual({ level: "warn", text: NOT_REPORTING });
    expect(describeLink({ stop: "idle", last_line: "1,1,1,1", last_line_age_ms: 5, reporting: false })).toEqual({ level: "warn", text: NOT_REPORTING });
    expect(describeLink({ stop: "confirmed", last_line: "0,1,0,1", reporting: false }).level).toBe("warn");
    expect(describeLink({ stop: "idle", reporting: false, other_publishers: 1 }).text).toBe(`${NOT_REPORTING} · Someone else is also driving this car`);
  });
  it("an unconfirmed stop stays danger even if reporting dropped", () => {
    expect(describeLink({ stop: "unconfirmed", reporting: false }).level).toBe("danger");
  });
});

describe("newerLink (R28: the highest seq wins, from any source)", () => {
  it("keeps the newer of two links whatever order they arrive in", () => {
    const old = { stop: "confirmed", seq: 5 };
    const fresh = { stop: "pending", seq: 9 };
    expect(newerLink(old, fresh)).toBe(fresh);
    expect(newerLink(fresh, old)).toBe(fresh);
  });
  it("handles missing links and links without a seq", () => {
    const link = { stop: "idle", seq: 1 };
    expect(newerLink(null, link)).toBe(link);
    expect(newerLink(link, null)).toBe(link);
    expect(newerLink(undefined, undefined)).toBeNull();
    expect(newerLink(link, { stop: "idle" })).toBe(link); // no seq never beats a seq
    const a = { stop: "idle" };
    const b = { stop: "pending" };
    expect(newerLink(a, b)).toBe(b); // neither has one: the newest arrival
  });
});

describe("displayMode (final audit #5: never 'Stopped' before the car confirms)", () => {
  const robot = (patch) => ({ status: "connected", mode: "Stopping…", stopping: { seq: 10 }, ...patch });

  it("shows the robot's own mode when no stop is being followed", () => {
    expect(displayMode({ status: "connected", mode: "Driving forward" }, { stop: "confirmed", seq: 99 })).toEqual({ text: "Driving forward", level: "muted" });
    expect(displayMode({ status: "offline", mode: "Idle", stopping: { seq: 1 } }, { stop: "confirmed", seq: 2 }).text).toBe("Idle");
    expect(displayMode(undefined, null).text).toBe("");
  });
  it("says Stopping… until a link at least as new as the stop reply confirms it", () => {
    expect(displayMode(robot({ stopping: { seq: null } }), { stop: "confirmed", seq: 50 })).toEqual({ text: "Stopping…", level: "muted" });
    expect(displayMode(robot(), { stop: "confirmed", seq: 9 }).text).toBe("Stopping…"); // an older stop's confirmation
    expect(displayMode(robot(), null).text).toBe("Stopping…");
    expect(displayMode(robot(), { stop: "pending", seq: 10 }).text).toBe("Stopping…");
    expect(displayMode(robot(), { stop: "confirmed", seq: 11 })).toEqual({ text: "Stopped", level: "ok" });
  });
  it("says so plainly when the stop is not confirmed or the car is not reporting", () => {
    expect(displayMode(robot(), { stop: "unconfirmed", seq: 12 })).toEqual({ text: "Stop not confirmed: car may still be moving", level: "danger" });
    expect(displayMode(robot(), { stop: "unreported", seq: 12 })).toEqual({ text: "Stop sent · car is not reporting motor commands", level: "warn" });
    expect(displayMode(robot(), { stop: "idle", seq: 12 }).text).toBe("Stopping…");
  });
  it("a stop reply without a link accepts any later link", () => {
    expect(displayMode(robot({ stopping: { seq: -Infinity } }), { stop: "confirmed" }).text).toBe("Stopped");
  });
});

describe("stopUnconfirmed", () => {
  it("is true only for a connected car whose newest link says unconfirmed", () => {
    expect(stopUnconfirmed({ status: "connected" }, { stop: "unconfirmed" })).toBe(true);
    expect(stopUnconfirmed({ status: "connected" }, { stop: "confirmed" })).toBe(false);
    expect(stopUnconfirmed({ status: "connecting" }, { stop: "unconfirmed" })).toBe(false);
    expect(stopUnconfirmed({ status: "connected" }, null)).toBe(false);
  });
});

describe("ddsPorts", () => {
  it("computes the range", () => {
    expect(ddsPorts(42)).toEqual([17900, 18149]);
    expect(ddsPorts(0)).toEqual([7400, 7649]);
  });
});
