// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import complete from "./__fixtures__/complete.json";
import uncertain from "./__fixtures__/uncertain.json";
import { EARLIER_KEY, MAX_EARLIER, loadEarlier, missionRecord, saveEarlier, withMission } from "./earlierMissions.js";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("missionRecord", () => {
  it("keeps command, outcome, duration, revision count and the export", () => {
    const exp = { schema_version: "hive.run-export.v1", run_id: complete.run_id };
    const r = missionRecord(complete, exp);
    expect(r).toMatchObject({
      id: `${complete.run_id}:${complete.mission.mission_id}`,
      command: complete.mission.command,
      outcome: "completed",
      revisions: 3,
      export: exp,
    });
    expect(r.duration_s).toBeGreaterThan(0);
    expect(missionRecord({ mission: null }, null)).toBeNull();
    expect(missionRecord(complete, null).export).toBeNull();
  });
});

describe("missionRecord for an answered question", () => {
  it("keeps the answered outcome and the answer itself", () => {
    const state = { run_id: "run-a", timeline: [], mission: { mission_id: "m1", command: "What can the gripper car do?", status: "answered", created_at: 10, revisions: [], outcome: { status: "answered", reason: "It picks and places." } } };
    expect(missionRecord(state, null)).toMatchObject({ outcome: "answered", reason: "It picks and places.", revisions: 0 });
  });
});

describe("storage", () => {
  it("round-trips newest first, replaces the same mission and caps the list", () => {
    const a = missionRecord(complete, null);
    const b = missionRecord(uncertain, null);
    let list = withMission([], a);
    list = withMission(list, b);
    list = withMission(list, { ...a, outcome: "completed" });
    expect(list.map((r) => r.id)).toEqual([a.id, b.id]);
    saveEarlier(list);
    expect(loadEarlier().map((r) => r.id)).toEqual([a.id, b.id]);
    const many = Array.from({ length: MAX_EARLIER + 5 }, (_, i) => ({ ...a, id: `r${i}` })).reduce(withMission, []);
    expect(many).toHaveLength(MAX_EARLIER);
    expect(many[0].id).toBe(`r${MAX_EARLIER + 4}`);
  });

  it("survives junk and blocked storage", () => {
    localStorage.setItem(EARLIER_KEY, "{not json");
    expect(loadEarlier()).toEqual([]);
    localStorage.setItem(EARLIER_KEY, JSON.stringify({ not: "a list" }));
    expect(loadEarlier()).toEqual([]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(loadEarlier()).toEqual([]);
  });

  it("drops exports, then old entries, when the quota is full", () => {
    const big = { blob: "x".repeat(50) };
    const list = [
      { id: "new", export: big },
      { id: "mid", export: big },
      { id: "old", export: big },
    ];
    const real = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (k, v) {
      if (v.length > 120) throw new DOMException("full", "QuotaExceededError");
      return real.call(this, k, v);
    });
    const saved = saveEarlier(list);
    expect(saved.map((r) => [r.id, Boolean(r.export)])).toEqual([
      ["new", false],
      ["mid", false],
      ["old", false],
    ]);
    expect(JSON.parse(localStorage.getItem(EARLIER_KEY)).map((r) => r.id)).toEqual(["new", "mid", "old"]);
  });

  it("gives up quietly when nothing fits", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(saveEarlier([{ id: "a" }])).toEqual([]);
  });
});

describe("missionRecord keeps the next-stage preview and its diff in the stored export", () => {
  it("round-trips next_stage_preview, preview_changes and preview_diff through storage", () => {
    const exp = JSON.parse(JSON.stringify({ schema_version: "hive.run-export.v1", run_id: complete.run_id, mission: complete.mission }));
    const [first, second] = exp.mission.revisions;
    first.proposal.next_stage_preview = { summary: "Carry it.", tasks: [], waits_for: ["camera_01 sighting"] };
    second.proposal.preview_changes = ["Evidence as previewed."];
    second.preview_diff = { previous_revision: 1, matches_preview: true, kept: [], changed: [], added: [], removed: [] };
    const saved = saveEarlier(withMission([], missionRecord(complete, exp)));
    const back = loadEarlier()[0].export.mission.revisions;
    expect(saved).toHaveLength(1);
    expect(back[0].proposal.next_stage_preview.waits_for).toEqual(["camera_01 sighting"]);
    expect(back[1].proposal.preview_changes).toEqual(["Evidence as previewed."]);
    expect(back[1].preview_diff.matches_preview).toBe(true);
  });
});
