export const EXEC_API_URL = import.meta.env.VITE_HIVE_EXEC_URL || "http://127.0.0.1:8788/api/exec";

export async function execGet(path) {
  const response = await fetch(`${EXEC_API_URL}${path}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(data, response.status));
  return data;
}

export async function execPost(path, payload = {}) {
  const response = await fetch(`${EXEC_API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(data, response.status));
  return data;
}

/* The server's own error text: ExecError {error, detail}, FastAPI {detail: string}, or a 422's list
   of field errors. An object detail (the 503's {missing}) is already named in `error`. */
export function errorText(data, status) {
  const d = data?.detail;
  let detail = null;
  if (Array.isArray(d)) detail = d.map((x) => x?.message || x?.msg || JSON.stringify(x)).join("; ");
  else if (typeof d === "string") detail = d;
  else if (d && typeof d === "object" && !data?.error) detail = JSON.stringify(d);
  return [data?.error, detail].filter(Boolean).join(" — ") || `Request failed (${status})`;
}

// One-line agent command, e.g. inspect_area(area_id="work_area", object_query="red_block").
// Same format as describe_command in backend/hive_exec/agent_commands.py (agent_command.line in /state).
function formatArg(v) {
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "object") return v.label ? JSON.stringify(v.label) : JSON.stringify(v);
  return String(v);
}

export function describeAction(action) {
  if (!action) return "";
  const { kind, ...params } = action;
  const args = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${formatArg(v)}`)
    .join(", ");
  return `${kind}(${args})`;
}

export function describeGoal(goal) {
  switch (goal.kind) {
    case "object_at":
      return `${goal.object_query.label} at ${goal.location_id}`;
    case "object_located":
      return `${goal.object_query.label} located in ${goal.area_id}`;
    case "route_observed":
      return `${goal.route_id} observed`;
    case "image_captured":
      return `image of ${goal.area_id}`;
    case "area_mapped":
      return `${goal.area_id} mapped`;
    default:
      return JSON.stringify(goal);
  }
}
