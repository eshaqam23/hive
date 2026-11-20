// What a Fleet panel shows from the Executive's GET /api/exec/state for the robot its preset maps to.
// Everything here comes from the Executive's world (the fake fleet today), so the panel tags it SIM;
// connection data (link state, IP, domain, topics) comes only from live-data's backend.

/* Mission statuses after which nothing moves (backend hive_exec/executive.py TERMINAL_MISSION). */
export const TERMINAL_MISSION = new Set(["completed", "blocked", "rejected", "canceled", "planner_error", "answered"]);

/* Task runtime states that mean the robot is working on the task now. */
const ACTIVE_TASK = new Set(["dispatched", "accepted", "running", "canceling"]);

/* Fail-safe: SIM unless the Executive says plainly that its gateway is not simulated. No gateway does
   today (fake and synapse are both answered by the fake fleet), so everything is SIM. */
export const isSimulated = (gateway) => !(gateway && gateway.simulated === false);

export function activeMission(state) {
  const mission = state?.mission;
  return mission && !TERMINAL_MISSION.has(mission.status) ? mission : null;
}

export function activeRevision(mission) {
  return (mission?.revisions ?? []).find((rev) => rev.revision === mission.active_revision) ?? null;
}

/* The task of the active revision this robot is working on now, if any. */
export function currentTask(state, robotId) {
  const revision = activeRevision(activeMission(state));
  if (!revision) return null;
  for (const task of revision.tasks ?? []) {
    const runtime = revision.runtime?.[task.task_id];
    if (task.robot_id === robotId && ACTIVE_TASK.has(runtime?.state)) {
      return { task_id: task.task_id, skill: task.skill, line: task.agent_command?.line ?? null, state: runtime.state };
    }
  }
  return null;
}

const locationLabel = (state, id) => (id == null ? null : (state.catalog?.locations ?? []).find((l) => l.location_id === id)?.label ?? id);

/* robot: a live-data robot ({ preset, ... }). exec: { reachable, state } from useExecutive. */
export function execStatus(robot, exec) {
  const state = exec?.state;
  if (!exec?.reachable) return { kind: "unavailable", label: "unavailable", reason: "Executive offline" };
  if (!Array.isArray(state?.robots)) return { kind: "unavailable", label: "unavailable", reason: "Executive has not reported its robots" };
  const entry = state.robots.find((r) => r.preset === robot.preset);
  if (!entry) return { kind: "missing", label: "Not in Executive catalog" };
  const s = entry.state ?? {};
  const base = {
    robotId: entry.robot_id,
    displayName: entry.display_name ?? entry.robot_id,
    sim: isSimulated(state.gateway),
    battery: Number.isFinite(s.battery_pct) ? s.battery_pct : null,
    location: locationLabel(state, s.location_id),
    task: currentTask(state, entry.robot_id),
  };
  if (s.available === false) return { ...base, kind: "unavailable", label: "unavailable", reason: "marked unavailable" };
  if (s.health && s.health !== "ok") return { ...base, kind: "unavailable", label: "unavailable", reason: `health ${s.health}` };
  if (base.task) return { ...base, kind: "executing", label: `executing ${base.task.task_id} ${base.task.skill}` };
  return { ...base, kind: "idle", label: "idle" };
}
