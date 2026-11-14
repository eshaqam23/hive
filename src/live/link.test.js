import { describe, expect, it } from "vitest";
import { ddsPorts, describeLink } from "./link.js";

describe("describeLink", () => {
  it("null", () => expect(describeLink(null, 0)).toEqual({ level: "muted", text: "No link data yet" }));
  it("confirmed", () => {
    expect(describeLink({ stop: "confirmed", last_line: "stop\n", last_line_age_ms: 12.6, other_publishers: 0 }, 0)).toEqual({
      level: "ok",
      text: "Car confirmed stop · 13 ms ago",
    });
  });
  it("pending", () => expect(describeLink({ stop: "pending" }, 0)).toEqual({ level: "muted", text: "Stopping…" }));
  it("unconfirmed", () =>
    expect(describeLink({ stop: "unconfirmed" }, 0)).toEqual({ level: "danger", text: "Stop not confirmed: car may still be moving" }));
  it("unreported", () =>
    expect(describeLink({ stop: "unreported" }, 0)).toEqual({
      level: "warn",
      text: "Car is not reporting motor commands: is motor_commands running?",
    }));
  it("idle", () => expect(describeLink({ stop: "idle" }, 0)).toEqual({ level: "muted", text: "Ready" }));
  it("unknown stop falls back to Ready", () => expect(describeLink({}, 0).text).toBe("Ready"));
  it("appends other publishers", () => {
    expect(describeLink({ stop: "pending", other_publishers: 2 }, 0).text).toBe("Stopping… · Someone else is also driving this car");
    expect(describeLink({ stop: "idle", other_publishers: 1 }, 0).text).toBe("Ready · Someone else is also driving this car");
  });
  it("confirmed tolerates missing fields", () => {
    expect(describeLink({ stop: "confirmed" }, 0).text).toBe("Car confirmed  · 0 ms ago");
  });
});

describe("ddsPorts", () => {
  it("computes the range", () => {
    expect(ddsPorts(42)).toEqual([17900, 18149]);
    expect(ddsPorts(0)).toEqual([7400, 7649]);
  });
});
