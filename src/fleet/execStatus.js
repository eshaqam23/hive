// What a Fleet panel shows from the Executive's GET /api/exec/state for the robot its preset maps to.
// Everything here comes from the Executive's world, so the panel tags it SIM (fake fleet) or EMULATED
// (an agent fleet whose report says evidence emulated); connection data (link state, IP, domain, topics)
// comes only from live-data's backend.

/* Mission statuses after which nothing moves (backend hive_exec/executive.py TERMINAL_MISSION). */
export const TERMINAL_MISSION = new Set(["completed", "blocked", "rejected", "canceled", "planner_error", "answered"]);

/* Task runtime states that mean the robot is working on the task now. */
const ACTIVE_TASK = new Set(["dispatched", "accepted", "running", "canceling"]);

/* A8: an agent fleet's report says whether its evidence is emulated (docs/instance-agent-contract.md). */
const isEmulated = (gateway) => gateway?.fleet?.kind === "hive_agent" && gateway?.fleet?.evidence === "emulated";

/* Fail-safe: simulated unless the Executive says plainly that its gateway is not, and never while the
   fleet report says emulated. Only a hive_agent fleet with evidence real is not simulated. */
export const isSimulated = (gateway) => isEmulated(gateway) || !(gateway && gateway.simulated === false);

/* The tag every Executive-sourced view shows: "SIM" (fake fleets), "EMULATED" (agent fleet, evidence
   emulated) or null (an agent fleet with evidence real). */
export const simTag = (gateway) => (isEmulated(gateway) ? "EMULATED" : isSimulated(gateway) ? "SIM" : null);

/* The sim drawer (resets with world/seed, faults, truth) is for the fake fleets only: the in-process fake
   or the ROS fake fleet, which send no fleet report. Fail-safe: hidden when the gateway is unknown or
   says it is not simulated. */
export const isFakeFleet = (gateway) => Boolean(gateway) && !gateway.fleet && gateway.simulated !== false;

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
    tag: simTag(state.gateway),
    battery: Number.isFinite(s.battery_pct) ? s.battery_pct : null,
    location: locationLabel(state, s.location_id),
    task: currentTask(state, entry.robot_id),
  };
  if (s.available === false) return { ...base, kind: "unavailable", label: "unavailable", reason: "marked unavailable" };
  if (s.health && s.health !== "ok") return { ...base, kind: "unavailable", label: "unavailable", reason: `health ${s.health}` };
  if (base.task) return { ...base, kind: "executing", label: `executing ${base.task.task_id} ${base.task.skill}` };
  return { ...base, kind: "idle", label: "idle" };
}
