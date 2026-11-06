// Talks to the Python backend (backend/server.py). Same contract the old Hive UI used.
export const API_BASE_URL = import.meta.env.VITE_HIVE_API_URL || "http://127.0.0.1:8787/api";

export async function postApi(path, payload) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.stderr || `Request failed with status ${response.status}`);
  }
  return data;
}
