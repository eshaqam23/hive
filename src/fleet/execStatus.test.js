import { describe, expect, it } from "vitest";
import { activeMission, execStatus, isSimulated } from "./execStatus.js";
import { linkState } from "./presets.jsx";

const robotEntry = (over = {}) => ({
  robot_id: "lidar_01",
  display_name: "LiDAR RC Car",
  preset: "lidarRcCar",
  state: { robot_id: "lidar_01", location_id: "lidar_post", battery_pct: 72.5, health: "ok", available: true },
  ...over,
});
const catalog = { locations: [{ location_id: "lidar_post", label: "LiDAR post" }, { location_id: "work_area", label: "Work area" }] };
const mission = (status, runtimeState = "running") => ({
  mission_id: "m1",
  status,
  active_revision: 2,
  created_at: 1000,
  revisions: [
    { revision: 1, tasks: [{ task_id: "T9", robot_id: "lidar_01", skill: "inspect_area", agent_command: { line: "old" } }], runtime: { T9: { state: "running" } } },
    {
      revision: 2,
      tasks: [
        { task_id: "T1", robot_id: "camera_01", skill: "capture_image", agent_command: { line: "capture" } },
        { task_id: "T2", robot_id: "lidar_01", skill: "navigate_to", agent_command: { line: 'navigate_to(location="work_area")' } },
      ],
      runtime: { T1: { state: "succeeded" }, T2: { state: runtimeState } },
    },
  ],
});
const exec = (state, reachable = true) => ({ reachable, state });
const lidar = { id: "r1", name: "Rover", preset: "lidarRcCar", status: "offline" };

describe("linkState", () => {
  it("maps live-data's status to online / offline / connecting / reconnecting / lost", () => {
    expect(linkState({ status: "connected" })).toBe("online");
    expect(linkState({ status: "offline" })).toBe("offline");
    expect(linkState({ status: "connecting" })).toBe("connecting");
    expect(linkState({ status: "connecting", reconnecting: true })).toBe("reconnecting");
    expect(linkState({ status: "failed" })).toBe("lost");
    expect(linkState({ status: "weird" })).toBe("offline");
  });
});

describe("isSimulated", () => {
  it("is SIM unless the gateway says plainly that it is not simulated", () => {
    expect(isSimulated({ kind: "fake" })).toBe(true);
    expect(isSimulated({ kind: "synapse" })).toBe(true); // today synapse is answered by the fake fleet
    expect(isSimulated(undefined)).toBe(true);
    expect(isSimulated({ kind: "synapse", simulated: false })).toBe(false);
  });
});

describe("activeMission", () => {
  it("is the mission until it reaches a terminal status", () => {
    expect(activeMission({ mission: mission("executing") })?.mission_id).toBe("m1");
    expect(activeMission({ mission: mission("awaiting_approval") })?.mission_id).toBe("m1");
    for (const done of ["completed", "blocked", "rejected", "canceled", "planner_error", "answered"]) expect(activeMission({ mission: mission(done) })).toBeNull();
    expect(activeMission({ mission: null })).toBeNull();
    expect(activeMission({ ok: true })).toBeNull();
  });
});

describe("execStatus", () => {
  it("is unavailable while the Executive is down or has not reported robots", () => {
    expect(execStatus(lidar, exec(null, false))).toMatchObject({ kind: "unavailable", label: "unavailable", reason: "Executive offline" });
    expect(execStatus(lidar, exec({ ok: true }))).toMatchObject({ kind: "unavailable", reason: "Executive has not reported its robots" });
  });

  it("says when no catalog robot has this preset", () => {
    expect(execStatus(lidar, exec({ robots: [robotEntry({ preset: "cameraRcCar" })] }))).toMatchObject({ kind: "missing", label: "Not in Executive catalog" });
  });

  it("is idle with battery and location label, tagged SIM on the fake gateway", () => {
    const s = execStatus(lidar, exec({ robots: [robotEntry()], catalog, gateway: { kind: "fake" }, mission: null }));
    expect(s).toMatchObject({ kind: "idle", label: "idle", robotId: "lidar_01", battery: 72.5, location: "LiDAR post", task: null, sim: true });
  });

  it("names the task and skill it is executing in the active revision only", () => {
    const s = execStatus(lidar, exec({ robots: [robotEntry()], catalog, gateway: { kind: "synapse" }, mission: mission("executing") }));
    expect(s).toMatchObject({ kind: "executing", label: "executing T2 navigate_to", sim: true });
    expect(s.task).toMatchObject({ task_id: "T2", skill: "navigate_to", line: 'navigate_to(location="work_area")', state: "running" });
  });

  it("is not executing once its task finished or the mission ended", () => {
    const done = execStatus(lidar, exec({ robots: [robotEntry()], mission: mission("executing", "succeeded") }));
    expect(done).toMatchObject({ kind: "idle", task: null });
    const over = execStatus(lidar, exec({ robots: [robotEntry()], mission: mission("completed") }));
    expect(over).toMatchObject({ kind: "idle", task: null });
  });

  it("is unavailable when the robot is marked unavailable or unhealthy, and still shows its task", () => {
    const off = robotEntry({ state: { ...robotEntry().state, available: false } });
    expect(execStatus(lidar, exec({ robots: [off] }))).toMatchObject({ kind: "unavailable", reason: "marked unavailable" });
    const sick = robotEntry({ state: { ...robotEntry().state, health: "degraded" } });
    const s = execStatus(lidar, exec({ robots: [sick], mission: mission("executing") }));
    expect(s).toMatchObject({ kind: "unavailable", reason: "health degraded" });
    expect(s.task?.task_id).toBe("T2");
  });

  it("falls back to the raw location id and a missing battery", () => {
    const bare = robotEntry({ state: { location_id: "nowhere", health: "ok", available: true } });
    expect(execStatus(lidar, exec({ robots: [bare] }))).toMatchObject({ location: "nowhere", battery: null });
  });
});
