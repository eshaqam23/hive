import { describe, expect, it } from "vitest";
import { connectPatch, driveBody, gripperEnabled, gripperSliderShown, keepaliveBody, notFoundHint } from "./carHelpers.js";

describe("carHelpers", () => {
  it("puts power in drive and keepalive bodies without mutating the input", () => {
    const base = { ip: "1.2.3.4", domain_id: 42, action: "forward" };
    expect(driveBody(base, 0.8)).toEqual({ ...base, power: 0.8 });
    expect(base.power).toBeUndefined();
    expect(keepaliveBody({ ip: "1.2.3.4", domain_id: 42 }, 0.3)).toEqual({ ip: "1.2.3.4", domain_id: 42, power: 0.3 });
  });

  it("gates the gripper by connection and interface", () => {
    expect(gripperEnabled({ status: "connected", gripperInterface: "trigger" })).toBe(true);
    expect(gripperEnabled({ status: "connected", gripperInterface: null })).toBe(false);
    expect(gripperEnabled({ status: "offline", gripperInterface: "bool" })).toBe(false);
    expect(gripperSliderShown({ gripperInterface: "position" })).toBe(true);
    expect(gripperSliderShown({ gripperInterface: "trigger" })).toBe(false);
  });

  it("builds the not_found hint with the DDS port range", () => {
    expect(notFoundHint(42, "No robot.")).toBe(
      "No robot. Expected ROS domain 42; the Pi and this laptop must allow UDP 17900–18149. See docs/robot-setup.md.",
    );
  });

  it("maps a connect reply to a robot patch", () => {
    expect(connectPatch({ services: [{ name: "/s" }], gripper_interface: "position", other_publishers: 2, camera: { quality: "low" } })).toEqual({
      services: [{ name: "/s" }],
      gripperInterface: "position",
      otherPublishers: 2,
      camera: { quality: "low" },
    });
    expect(connectPatch({})).toEqual({ services: [], gripperInterface: null, otherPublishers: 0, camera: null });
  });
});
