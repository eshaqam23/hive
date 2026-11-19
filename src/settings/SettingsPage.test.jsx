// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { PlannerChip } from "../chat/Composer.jsx";
import { EXEC_API_URL } from "../executive/api.js";
import { useExecutive } from "../executive/useExecutive.js";
import SettingsPage from "./SettingsPage.jsx";

/* key-like strings that must never reach the DOM, an input's value, storage or the console */
const CANARY = "gsk_LEAKCANARY_0123456789abcdef4f2a";
const TYPED = "sk-TYPEDCANARY-55aa77bb99cc";

const SAMPLING = { temperature: 0.7, top_p: 0.8, top_k: null, seed: 1234, max_tokens: 3072, timeout_s: 60, think: false };
const TEMPLATES = [
  { template_id: "ollama_local", label: "Ollama (local)", kind: "ollama", base_url: "http://127.0.0.1:11434", model: "qwen3:8b", structured_mode: "ollama_format", key: "none", sampling: SAMPLING, hint: "Ollama's native API." },
  {
    template_id: "vt_arc_vllm",
    label: "VT ARC vLLM",
    kind: "openai_compatible",
    base_url: "http://127.0.0.1:8000/v1",
    model: "Qwen/Qwen3-32B",
    structured_mode: "structured_outputs",
    key: "optional",
    sampling: SAMPLING,
    hint: "Open the tunnel yourself: ssh -L 8000:<compute-node>:8000 <pid>@tinkercliffs1.arc.vt.edu",
  },
  {
    template_id: "hosted_oss",
    label: "Hosted OSS API",
    kind: "openai_compatible",
    base_url: "",
    model: "",
    structured_mode: "json_schema",
    key: "required",
    sampling: SAMPLING,
    hint: "Pick a preset.",
    presets: [
      { id: "groq", label: "Groq", base_url: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile", key_env: "GROQ_API_KEY" },
      { id: "cerebras", label: "Cerebras", base_url: "https://api.cerebras.ai/v1", model: "qwen-3-32b", key_env: "CEREBRAS_API_KEY" },
    ],
  },
  { template_id: "local_openai", label: "Local OpenAI-compatible", kind: "openai_compatible", base_url: "", model: "", structured_mode: "json_object", key: "none", sampling: SAMPLING, hint: "llama.cpp or LM Studio.", presets: [] },
];

const row = (over) => ({ kind: "openai_compatible", structured_mode: "json_schema", sampling: SAMPLING, key_present: false, key_hint: "", source: "store", read_only: false, ...over });

let server;
function makeServer() {
  const s = {
    calls: [],
    rows: {
      groq: row({
        id: "groq",
        label: "Groq",
        base_url: "https://api.groq.com/openai/v1",
        model: "llama-3.3-70b-versatile",
        key_env: "GROQ_API_KEY",
        key_present: true,
        key_hint: "…4f2a",
        // a server bug must not become a page leak: stray secret-looking fields are never drawn
        api_key: CANARY,
        key: CANARY,
        secret: CANARY,
      }),
      ollama: row({ id: "ollama", label: "Ollama", kind: "ollama", base_url: "http://127.0.0.1:11434", model: "qwen3:8b", structured_mode: "ollama_format", key_env: "HIVE_KEY_OLLAMA" }),
      cerebras: row({ id: "cerebras", label: "Cerebras", base_url: "https://api.cerebras.ai/v1", model: "qwen-3-32b", key_env: "CEREBRAS_API_KEY", key_present: true, key_hint: "…9z9z" }),
    },
    roles: { executive: ["groq"], instance_agent: [] },
    models: { groq: { ok: true, models: ["llama-3.3-70b-versatile", "qwen/qwen3-32b"], error: "" } },
    testResult: null,
    failSave: null,
  };
  s.view = () => ({
    providers: Object.values(s.rows).map((r) => ({ ...r, roles: Object.keys(s.roles).filter((role) => s.roles[role].includes(r.id)) })),
    roles: { ...s.roles },
    problems: [],
    store: { path: "/tmp/hive-test/providers.yaml", exists: true, env_row: false, note: "" },
  });
  s.state = () => {
    const id = s.roles.executive[0];
    const r = s.rows[id];
    const llm = r ? { kind: "llm", configured: true, provider: id, provider_label: r.label || id, model: r.model } : { kind: "llm", configured: false };
    return { planners: { llm, baseline: { kind: "baseline", label: "Baseline" } } };
  };
  return s;
}

const reply = (status, data) => ({ ok: status < 400, status, json: async () => data });

async function fakeFetch(url, init = {}) {
  const u = String(url);
  if (!u.startsWith(EXEC_API_URL)) return reply(404, { error: "not found" });
  const path = u.slice(EXEC_API_URL.length);
  const method = init.method || "GET";
  const body = init.body ? JSON.parse(init.body) : undefined;
  server.calls.push({ path, method, body, raw: init.body ?? "" });
  if (path === "/state") return reply(200, server.state());
  if (path === "/health") return reply(200, { ok: true });
  if (path === "/llm/templates") return reply(200, TEMPLATES);
  if (path === "/llm/providers" && method === "GET") return reply(200, server.view());
  if (path === "/llm/providers" && method === "POST") {
    if (server.failSave) return reply(server.failSave.status, server.failSave.data);
    const { api_key: key, api_key_env: env, ...rest } = body;
    const prev = server.rows[body.id];
    server.rows[body.id] = row({
      ...rest,
      label: body.label || body.id,
      key_env: env || prev?.key_env || `HIVE_KEY_${body.id.toUpperCase()}`,
      key_present: Boolean(key) || Boolean(prev?.key_present),
      key_hint: key ? `…${key.slice(-4)}` : prev?.key_hint || "",
    });
    return reply(200, server.view());
  }
  if (path === "/llm/roles" && method === "POST") {
    server.roles = { ...server.roles, ...body };
    return reply(200, server.view());
  }
  const one = path.match(/^\/llm\/providers\/([^/]+)(\/test|\/models)?$/);
  if (one) {
    const id = decodeURIComponent(one[1]);
    if (!server.rows[id]) return reply(404, { detail: `unknown provider '${id}'` });
    if (one[2] === "/test") return reply(200, server.testResult);
    if (one[2] === "/models") return reply(200, server.models[id] ?? { ok: false, models: [], error: "HTTP 404: page not found" });
    if (method === "DELETE") {
      delete server.rows[id];
      server.roles = Object.fromEntries(Object.entries(server.roles).map(([r, c]) => [r, c.filter((x) => x !== id)]));
      return reply(200, server.view());
    }
  }
  return reply(404, { detail: `no route ${method} ${path}` });
}

const CONSOLE = ["log", "info", "warn", "error", "debug"];
let spies;

beforeEach(() => {
  server = makeServer();
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
  spies = CONSOLE.map((m) => vi.spyOn(console, m));
  try {
    window.localStorage.clear();
    window.sessionStorage.clear();
  } catch {
    /* storage may be unavailable */
  }
});

afterEach(() => {
  cleanup();
  spies.forEach((s) => s.mockRestore());
  vi.unstubAllGlobals();
});

const posts = (path) => server.calls.filter((c) => c.method === "POST" && c.path === path);
const keyInput = (form) => form.querySelector('input[type="password"]');

function nowhere(secret) {
  expect(document.documentElement.innerHTML).not.toContain(secret);
  for (const el of document.querySelectorAll("input, textarea, select")) expect(el.value).not.toContain(secret);
  const stored = [window.localStorage, window.sessionStorage].flatMap((st) => Object.keys(st).map((k) => `${k}=${st.getItem(k)}`));
  expect(stored.join("\n")).not.toContain(secret);
  for (const spy of spies) expect(spy.mock.calls.flat().map(String).join("\n")).not.toContain(secret);
}

async function page() {
  render(<SettingsPage />);
  return screen.findByRole("article", { name: "Provider groq" });
}

describe("SettingsPage", () => {
  it("never renders a key: not from a stray server field, and not the one just typed", async () => {
    const card = await page();
    expect(within(card).getByText("…4f2a")).toBeTruthy();
    nowhere(CANARY);
    nowhere("LEAKCANARY");

    fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit groq" });
    expect(keyInput(form).getAttribute("placeholder")).toBe("blank keeps the saved key …4f2a");
    expect(keyInput(form).value).toBe("");
    nowhere(CANARY);

    fireEvent.change(keyInput(form), { target: { value: TYPED } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    const saved = await screen.findByRole("article", { name: "Provider groq" });
    expect(within(saved).getByText("…99cc")).toBeTruthy();

    // it went out exactly once, in one request body, and nowhere else
    const carrying = server.calls.filter((c) => c.raw.includes(TYPED) || c.path.includes(TYPED));
    expect(carrying.map((c) => `${c.method} ${c.path}`)).toEqual(["POST /llm/providers"]);
    expect(carrying[0].body.api_key).toBe(TYPED);
    nowhere(TYPED);
    nowhere(CANARY);
  });

  it("clears the key field when a save fails, and says to paste it again", async () => {
    server.failSave = { status: 422, data: { detail: [{ loc: ["body", "base_url"], msg: "Input should be a valid URL" }] } };
    const card = await page();
    fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit groq" });
    fireEvent.change(keyInput(form), { target: { value: TYPED } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    const alert = await within(form).findByRole("alert");
    expect(alert.textContent).toContain("base_url: Input should be a valid URL");
    expect(alert.textContent).toContain("paste the key again");
    expect(keyInput(form).value).toBe("");
    nowhere(TYPED);
  });

  it("sends no api_key when an edit leaves the key blank", async () => {
    const card = await page();
    fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit groq" });
    fireEvent.change(within(form).getByLabelText(/^Model/), { target: { value: "qwen/qwen3-32b" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts("/llm/providers")).toHaveLength(1));
    const [call] = posts("/llm/providers");
    expect(call.body).toMatchObject({ id: "groq", model: "qwen/qwen3-32b", api_key_env: "GROQ_API_KEY" });
    expect("api_key" in call.body).toBe(false);
    expect(call.raw).not.toContain("api_key\"");
    expect(await screen.findByRole("article", { name: "Provider groq" })).toBeTruthy();
    expect(server.rows.groq.key_hint).toBe("…4f2a"); // the stored key stayed
  });

  it("shows the provider's error verbatim when Test fails", async () => {
    const error = 'HTTP 401: {"error":{"message":"Invalid API Key","type":"invalid_request_error","code":"invalid_api_key"}} (sent Bearer …4f2a)';
    server.testResult = {
      ok: false,
      provider_id: "groq",
      latency_ms: 231,
      model: "llama-3.3-70b-versatile",
      error,
      models: { ok: false, served: [], model_listed: false, error },
      chat: { ok: false, latency_ms: 231, model_echo: "", error },
      schema_roundtrip: { ok: false, latency_ms: null, error: "skipped", errors: [] },
    };
    const card = await page();
    fireEvent.click(within(card).getByRole("button", { name: "Test" }));
    expect((await within(card).findByTestId("test-error-groq")).textContent).toBe(error);
    expect(within(card).getByRole("alert", { name: "Test result for groq" }).textContent).toMatch(/^failed/);
    expect(posts("/llm/providers/groq/test")).toEqual([expect.objectContaining({ body: {} })]);
  });

  it("shows ok and latency when Test passes", async () => {
    server.testResult = {
      ok: true,
      provider_id: "groq",
      latency_ms: 742,
      model: "llama-3.3-70b-versatile",
      error: "",
      models: { ok: true, served: [], model_listed: true, error: "" },
      chat: { ok: true, latency_ms: 301, model_echo: "llama-3.3-70b-versatile", error: "" },
      schema_roundtrip: { ok: true, latency_ms: 441, error: "", errors: [] },
    };
    const card = await page();
    fireEvent.click(within(card).getByRole("button", { name: "Test" }));
    const result = await within(card).findByRole("status", { name: "Test result for groq" });
    expect(result.textContent).toMatch(/^ok/);
    expect(result.textContent).toContain("742 ms");
  });

  it("saves a role through POST /roles, with fallbacks in arrow order", async () => {
    await page();
    const role = screen.getByRole("region", { name: "Executive role" });
    const add = () => within(role).getByRole("combobox", { name: "Add a Executive fallback" });
    fireEvent.change(add(), { target: { value: "ollama" } });
    fireEvent.change(add(), { target: { value: "cerebras" } });
    fireEvent.click(within(role).getByRole("button", { name: "Move cerebras up" }));
    fireEvent.click(within(role).getByRole("button", { name: "Save executive role" }));
    await waitFor(() => expect(posts("/llm/roles")).toHaveLength(1));
    expect(posts("/llm/roles")[0].body).toEqual({ executive: ["groq", "cerebras", "ollama"] });
    expect(posts("/llm/providers")).toHaveLength(0); // the model did not change
  });

  it("saves a role's new model on its provider row first, without a key", async () => {
    await page();
    const role = screen.getByRole("region", { name: "Executive role" });
    await within(role).findByText("2 models offered, or type any id.");
    fireEvent.change(within(role).getByLabelText(/^Model/), { target: { value: "qwen/qwen3-32b" } });
    fireEvent.click(within(role).getByRole("button", { name: "Save executive role" }));
    await waitFor(() => expect(posts("/llm/roles")).toHaveLength(1));
    const [row] = posts("/llm/providers");
    expect(row.body).toMatchObject({ id: "groq", model: "qwen/qwen3-32b" });
    expect("api_key" in row.body).toBe(false);
    const order = server.calls.filter((c) => c.method === "POST").map((c) => c.path);
    expect(order).toEqual(["/llm/providers", "/llm/roles"]);
  });

  it("updates the Chat planner chip when the executive role changes", async () => {
    function Harness() {
      const exec = useExecutive({ stateMs: 60_000, healthMs: 60_000 });
      return (
        <>
          <PlannerChip onChange={() => {}} planner="llm" planners={exec.state?.planners} />
          <SettingsPage onChanged={exec.refresh} />
        </>
      );
    }
    render(<Harness />);
    const chip = () => within(screen.getByRole("group", { name: "Planner" })).getAllByRole("button")[0];
    await waitFor(() => expect(chip().textContent).toBe("Groq · llama-3.3-70b-versatile"));
    const role = await screen.findByRole("region", { name: "Executive role" });
    fireEvent.change(within(role).getByLabelText(/^Provider/), { target: { value: "ollama" } });
    fireEvent.click(within(role).getByRole("button", { name: "Save executive role" }));
    // the 60 s poll cannot be what updates it: only the page's onChanged -> exec.refresh can
    await waitFor(() => expect(chip().textContent).toBe("Ollama · qwen3:8b"));
    expect(posts("/llm/roles")[0].body).toEqual({ executive: ["ollama"] });
  });

  it("offers the templates, with the ARC tunnel hint, and fills the form from a preset", async () => {
    await page();
    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    const picker = screen.getByRole("group", { name: "Provider templates" });
    for (const label of ["Ollama (local)", "VT ARC vLLM", "Hosted OSS API", "Local OpenAI-compatible"]) expect(within(picker).getByText(label)).toBeTruthy();
    expect(within(picker).getByText(/ssh -L 8000:<compute-node>:8000/)).toBeTruthy();
    expect(within(picker).getByRole("button", { name: /^Groq/ }).disabled).toBe(true); // already a row
    fireEvent.click(within(picker).getByRole("button", { name: "Use VT ARC vLLM" }));
    const form = screen.getByRole("form", { name: "New provider" });
    expect(within(form).getByText(/tinkercliffs1\.arc\.vt\.edu/)).toBeTruthy();
    expect(within(form).getByLabelText(/^Provider id/).value).toBe("arc-vllm");
    expect(within(form).getByLabelText(/^Base URL/).value).toBe("http://127.0.0.1:8000/v1");
    expect(keyInput(form).getAttribute("placeholder")).toBe("only if the server was started with --api-key");
    // Advanced is collapsed until opened
    expect(within(form).queryByLabelText(/^structured_mode/)).toBeNull();
    fireEvent.click(within(form).getByRole("button", { name: /Advanced/ }));
    expect(within(form).getByLabelText(/^structured_mode/).value).toBe("structured_outputs");
    expect(within(form).getByLabelText("temperature").value).toBe("0.7");
  });

  it("deletes only after an inline confirm", async () => {
    await page();
    const card = screen.getByRole("article", { name: "Provider ollama" });
    fireEvent.click(within(card).getByRole("button", { name: "Delete" }));
    expect(server.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(within(card).getByText("Delete ollama and its key?")).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "Provider ollama" })).toBeNull());
    expect(server.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/llm/providers/ollama"]);
  });
});
