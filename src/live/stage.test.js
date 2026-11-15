import { describe, expect, it } from "vitest";
import { currentTab, MODEL_TAB, stageTabs } from "./stage.js";

const t = (name, kind) => ({ name, kind, type: "x/msg/Y" });
const topics = [t("/scan", "scan"), t("/ros_data", "value"), t("/cam", "image"), t("/tf", "generic")];

describe("stageTabs", () => {
  it("offers image and scan topics, images first", () => {
    expect(stageTabs(topics, null).map((x) => x.name)).toEqual(["/cam", "/scan"]);
  });
  it("adds a clicked row that is not already a tab, using the discovered entry", () => {
    expect(stageTabs(topics, { name: "/tf" }).map((x) => x.name)).toEqual(["/cam", "/scan", "/tf"]);
    expect(stageTabs(topics, { name: "/scan" }).map((x) => x.name)).toEqual(["/cam", "/scan"]);
    expect(stageTabs(topics, { name: "/not-on-the-car" }).map((x) => x.name)).toEqual(["/cam", "/scan"]);
  });
});

describe("currentTab", () => {
  const tabs = stageTabs(topics, null);
  it("is the chosen tab, else the first one", () => {
    expect(currentTab(tabs, "/scan").name).toBe("/scan");
    expect(currentTab(tabs, null).name).toBe("/cam");
    expect(currentTab(tabs, "/gone").name).toBe("/cam");
  });
  it("is null for the 3D model tab or when there is nothing to show", () => {
    expect(currentTab(tabs, MODEL_TAB)).toBeNull();
    expect(currentTab([], null)).toBeNull();
  });
});
