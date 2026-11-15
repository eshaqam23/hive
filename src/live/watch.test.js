import { describe, expect, it } from "vitest";
import { ALWAYS_WATCHED, MAX_STREAM_TOPICS, watchSet } from "./watch.js";

const t = (name, kind) => ({ name, kind, type: "x/msg/Y" });
const listed = [
  t("/cmd_vel", "value"),
  t("/ros_data", "value"),
  t("/esp_data", "value"),
  t("/scan", "scan"),
  t("/tf", "generic"),
  t("/odom", "generic"),
  t("/camera/camera/color/image_raw", "image"),
];

describe("watchSet (R26: stream only what is viewed)", () => {
  it("streams the link topics and nothing else when nothing is on stage or watched", () => {
    expect(watchSet({ listed, stage: null })).toEqual({ topics: ["/ros_data", "/esp_data"], images: [] });
    expect(ALWAYS_WATCHED).toEqual(["/ros_data", "/esp_data"]);
  });
  it("adds the stage topic: a scan as data, an image as stats only", () => {
    expect(watchSet({ listed, stage: listed[3] })).toEqual({ topics: ["/scan", "/ros_data", "/esp_data"], images: [] });
    expect(watchSet({ listed, stage: listed[6] })).toEqual({ topics: ["/ros_data", "/esp_data"], images: ["/camera/camera/color/image_raw"] });
  });
  it("adds the rows the operator watches, once each, in order", () => {
    expect(watchSet({ listed, stage: listed[3], watched: ["/tf", "/scan", "/tf", "/ros_data"] }).topics).toEqual(["/scan", "/ros_data", "/esp_data", "/tf"]);
  });
  it("never asks for a topic the car does not have", () => {
    expect(watchSet({ listed: [t("/scan", "scan")], stage: t("/gone", "scan"), watched: ["/nope"] })).toEqual({ topics: ["/scan"].slice(1), images: [] });
  });
  it("caps each list at the backend's limit", () => {
    const many = Array.from({ length: 40 }, (_, i) => t(`/t${i}`, "generic"));
    const out = watchSet({ listed: many, stage: null, watched: many.map((x) => x.name) });
    expect(out.topics).toHaveLength(MAX_STREAM_TOPICS);
    expect(MAX_STREAM_TOPICS).toBe(32);
  });
});
