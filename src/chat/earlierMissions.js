// Finished missions, kept in this browser only. The Executive holds one mission per run and forgets
// it on reset, so the chat keeps a short list here: command, outcome, duration, revision count and
// the run export captured when the mission finished. Storage may be full, blocked or cleared; every
// access is guarded and the chat works without it.
import { finishedAt } from "./chatModel.js";

export const EARLIER_KEY = "hive.chat.earlier.v1";
export const MAX_EARLIER = 20;

export function missionRecord(state, exported) {
  const m = state?.mission;
  if (!m) return null;
  const end = finishedAt(m, state.timeline || []);
  return {
    id: `${state.run_id}:${m.mission_id}`,
    run_id: state.run_id,
    mission_id: m.mission_id,
    command: m.command,
    outcome: m.status,
    reason: m.outcome?.reason ?? null,
    created_at: m.created_at,
    finished_at: end,
    duration_s: end && m.created_at ? end - m.created_at : null,
    revisions: (m.revisions || []).length,
    export: exported ?? null,
  };
}

export function loadEarlier() {
  try {
    const list = JSON.parse(localStorage.getItem(EARLIER_KEY) || "[]");
    return Array.isArray(list) ? list.filter((r) => r && typeof r.id === "string") : [];
  } catch {
    return [];
  }
}

/* newest first, one entry per mission, at most MAX_EARLIER */
export const withMission = (list, record) => [record, ...list.filter((r) => r.id !== record.id)].slice(0, MAX_EARLIER);

const tryWrite = (list) => {
  try {
    localStorage.setItem(EARLIER_KEY, JSON.stringify(list));
    return true;
  } catch {
    return false;
  }
};

/* Saves the list, shedding exports from the oldest entry first, then the oldest entries, until it
   fits. Returns what was stored ([] if nothing could be). */
export function saveEarlier(list) {
  let next = [...list];
  if (tryWrite(next)) return next;
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (!next[i].export) continue;
    next = next.map((r, j) => (j === i ? { ...r, export: null } : r));
    if (tryWrite(next)) return next;
  }
  while (next.length > 1) {
    next = next.slice(0, -1);
    if (tryWrite(next)) return next;
  }
  return [];
}
