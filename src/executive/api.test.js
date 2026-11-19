import { describe, expect, it } from "vitest";
import { describeAction, errorText } from "./api.js";

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

describe("errorText", () => {
  it("keeps the server's message and drops an object detail it already names", () => {
    expect(errorText({ ok: false, error: "Not ready: planner llm is down", detail: { missing: ["planner:llm"] } }, 503))
      .toBe("Not ready: planner llm is down");
  });

  it("reads FastAPI string details and 422 field lists", () => {
    expect(errorText({ detail: "Unknown fault x" }, 400)).toBe("Unknown fault x");
    expect(errorText({ detail: [{ msg: "Field required" }, { msg: "Extra inputs are not permitted" }] }, 422))
      .toBe("Field required; Extra inputs are not permitted");
    expect(errorText({ error: "Mission is executing, not awaiting approval.", detail: null }, 409))
      .toBe("Mission is executing, not awaiting approval.");
  });

  it("falls back to the status", () => {
    expect(errorText({}, 500)).toBe("Request failed (500)");
    expect(errorText(null, 502)).toBe("Request failed (502)");
    expect(errorText({ detail: { a: 1 } }, 400)).toBe('{"a":1}');
  });
});
