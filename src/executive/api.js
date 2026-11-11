export const EXEC_API_URL = import.meta.env.VITE_HIVE_EXEC_URL || "http://127.0.0.1:8788/api/exec";

export async function execGet(path) {
  const response = await fetch(`${EXEC_API_URL}${path}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || `Request failed with status ${response.status}`);
  return data;
}

export async function execPost(path, payload = {}) {
  const response = await fetch(`${EXEC_API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => d.message || d.msg || JSON.stringify(d)).join("; ")
      : data.detail;
    throw new Error([data.error, detail].filter(Boolean).join(" — ") || `Request failed (${response.status})`);
  }
  return data;
}

export function describeAction(action) {
  if (!action) return "";
  const { kind, ...params } = action;
  const args = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === "object" ? v.label || JSON.stringify(v) : v}`)
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
    default:
      return JSON.stringify(goal);
  }
}
