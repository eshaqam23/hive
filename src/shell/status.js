// The header's LIVE dot and mission pill, as plain functions of what the two backends report.
import { TERMINAL_MISSION } from "../fleet/execStatus.js";

/* backend: useBackendHealth's { state: "checking" | "online" | "offline", ros }.
   exec: useExecutive's { reachable: null (not asked yet) | true | false, health }. */
export function liveStatus({ backend, exec }) {
  const down = [];
  if (backend?.state === "offline") down.push("car backend offline (npm run backend)");
  else if (backend?.state !== "online") down.push("car backend: checking…");
  else if (backend.ros && backend.ros.found === false) down.push("car backend: ROS 2 not found");

  const health = exec?.health;
  if (exec?.reachable == null) down.push("Executive: checking…");
  else if (!exec.reachable) down.push("Executive offline");
  else if (!health?.ok || health.ready !== true) {
    const missing = Array.isArray(health?.missing) ? health.missing.filter(Boolean) : [];
    down.push(missing.length ? `Executive not ready: ${missing.join(", ")}` : "Executive not ready");
  } else if (health.gateway && health.gateway.connected === false) {
    down.push(`Executive gateway not connected (${health.gateway.label || health.gateway.kind || "gateway"})`);
  }
  return { ok: down.length === 0, down, text: down.length ? down.join(" · ") : "Car backend and Executive healthy" };
}

const two = (n) => String(n).padStart(2, "0");

export function elapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${two(m)}:${two(s % 60)}` : `${two(m)}:${two(s % 60)}`;
}

const WORDS = {
  executing: "mission running",
  evaluating: "mission running",
  recovering: "mission recovering",
  planning: "mission planning",
  clarification: "mission needs an answer",
  awaiting_approval: "mission awaiting approval",
  canceling: "mission canceling",
};

/* "mission running · rev N · mm:ss" for an active mission, else null. created_at is the Executive's
   wall clock in seconds; nowMs the page's, in ms (same machine). */
export function missionPill(mission, nowMs) {
  if (!mission || TERMINAL_MISSION.has(mission.status)) return null;
  const words = WORDS[mission.status] ?? `mission ${String(mission.status).replaceAll("_", " ")}`;
  const rev = Number.isFinite(mission.active_revision) ? ` · rev ${mission.active_revision}` : "";
  const since = Number.isFinite(mission.created_at) ? ` · ${elapsed(nowMs / 1000 - mission.created_at)}` : "";
  return `${words}${rev}${since}`;
}
