import { describe, expect, it } from "vitest";
import { resolvePanel, stageTopics, VIEWERS } from "./registry.js";

const t = (name, kind, type = "x/Y") => ({ name, kind, type });

describe("resolvePanel", () => {
  it("maps kinds", () => {
    expect(resolvePanel({ kind: "image" })).toBe("CameraPanel");
    expect(resolvePanel({ kind: "scan" })).toBe("LidarPanel");
    expect(resolvePanel({ kind: "value" })).toBe("ValuePanel");
    expect(resolvePanel({ kind: "generic" })).toBe("GenericPanel");
  });
  it("falls back to GenericPanel", () => expect(resolvePanel({ kind: "weird" })).toBe("GenericPanel"));
  it("type beats kind, topic beats type", () => {
    VIEWERS.unshift({ match: { type: "t/T" }, panel: "LidarPanel" }, { match: { topic: "/special" }, panel: "ValuePanel" });
    try {
      expect(resolvePanel({ kind: "image", type: "t/T", topic: "/other" })).toBe("LidarPanel");
      expect(resolvePanel({ kind: "image", type: "t/T", topic: "/special" })).toBe("ValuePanel");
    } finally {
      VIEWERS.splice(0, 2);
    }
  });
});

describe("stageTopics", () => {
  it("keeps image and scan, images first", () => {
    const out = stageTopics([t("/scan", "scan"), t("/v", "value"), t("/cam", "image")]);
    expect(out.map((x) => x.name)).toEqual(["/cam", "/scan"]);
  });
  it("prefers compressed over raw", () => {
    const out = stageTopics([t("/cam", "image"), t("/cam/compressed", "image"), t("/other", "image")]);
    expect(out.map((x) => x.name)).toEqual(["/cam/compressed", "/other"]);
  });
  it("keeps raw when no compressed twin", () => {
    expect(stageTopics([t("/cam", "image")]).map((x) => x.name)).toEqual(["/cam"]);
  });
});
