// The Models page's client for /api/exec/llm (backend/hive_exec/llm/api.py).
//
// Keys only ever travel one way: saveProvider() may carry one in its body, and nothing here reads
// one back (the server answers with key_present and a four-character hint). Request bodies are
// never logged, and errors are built from the response only, never from what was sent.
import { EXEC_API_URL } from "../executive/api.js";

export const LLM_API_URL = `${EXEC_API_URL}/llm`;

/* The server's words: a string detail, or a 422's loc/msg list ("base_url: must start with …"). */
export function llmErrorText(data, status) {
  const d = data?.detail;
  if (Array.isArray(d) && d.length) {
    return d
      .map((e) => {
        const loc = (Array.isArray(e?.loc) ? e.loc : []).filter((part) => part !== "body").join(".");
        const msg = String(e?.msg ?? "invalid");
        return loc ? `${loc}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (typeof d === "string" && d) return d;
  if (typeof data?.error === "string" && data.error) return data.error;
  return `Request failed (${status})`;
}

async function call(path, { method = "GET", body } = {}) {
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`${LLM_API_URL}${path}`, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(llmErrorText(data, response.status));
  return data;
}

const enc = encodeURIComponent;

/* { providers, roles, problems, store } */
export const getProviders = () => call("/providers");
export const getTemplates = () => call("/templates");
/* body: one row; api_key only when the operator typed one (blank keeps the stored key) */
export const saveProvider = (body) => call("/providers", { method: "POST", body });
export const deleteProvider = (id) => call(`/providers/${enc(id)}`, { method: "DELETE" });
/* { executive: ["primary", "fallback", …] }; roles left out keep their chain */
export const saveRoles = (roles) => call("/roles", { method: "POST", body: roles });
export const testProvider = (id, model = "") => call(`/providers/${enc(id)}/test`, { method: "POST", body: model ? { model } : {} });
/* { ok, models, error }: ok false means "no list, type the id", not a failure */
export const listModels = (id) => call(`/providers/${enc(id)}/models`);
