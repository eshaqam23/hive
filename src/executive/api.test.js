import { describe, expect, it } from "vitest";
import { describeAction } from "./api.js";

// Must match describe_command in backend/hive_exec/agent_commands.py (tested there with the same cases).
describe("describeAction", () => {
  it("quotes strings and renders an object query by its label", () => {
    expect(describeAction({ kind: "inspect_area", area_id: "work_area", object_query: { label: "red_block" } }))
      .toBe('inspect_area(area_id="work_area", object_query="red_block")');
  });

  it("drops null parameters", () => {
    expect(describeAction({ kind: "navigate_to", location_id: "pickup_zone", route_id: null }))
      .toBe('navigate_to(location_id="pickup_zone")');
  });

  it("renders numbers, booleans and other objects like JSON", () => {
    expect(describeAction({ kind: "x", n: 2.0, f: true, o: { a: 1 } })).toBe('x(n=2, f=true, o={"a":1})');
  });

  it("is empty for no action", () => {
    expect(describeAction(null)).toBe("");
  });
});
