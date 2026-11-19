import { describe, expect, it } from "vitest";
import { llmErrorText } from "./api.js";
import { draftErrors, draftFromProvider, draftFromTemplate, editDraft, moveItem, providerBody, rowWithModel, testSummary } from "./settingsModel.js";

const groq = {
  id: "groq",
  label: "Groq",
  kind: "openai_compatible",
  base_url: "https://api.groq.com/openai/v1",
  model: "llama-3.3-70b-versatile",
  structured_mode: "json_schema",
  sampling: { temperature: 0.7, top_p: 0.8, top_k: null, seed: 1234, max_tokens: 3072, timeout_s: 60, think: false },
  key_env: "GROQ_API_KEY",
  key_present: true,
  key_hint: "…4f2a",
  roles: ["executive"],
};

const hosted = {
  template_id: "hosted_oss",
  label: "Hosted OSS API",
  kind: "openai_compatible",
  base_url: "",
  model: "",
  structured_mode: "json_schema",
  key: "required",
  sampling: { temperature: 0.7, top_p: 0.8, top_k: null, seed: 1234, max_tokens: 3072, timeout_s: 60, think: false },
  presets: [{ id: "cerebras", label: "Cerebras", base_url: "https://api.cerebras.ai/v1", model: "qwen-3-32b", key_env: "CEREBRAS_API_KEY" }],
};

describe("settingsModel", () => {
  it("round-trips a stored row into the same POST body, with no key", () => {
    const body = providerBody(draftFromProvider(groq));
    expect(body).toEqual({
      id: "groq",
      label: "Groq",
      kind: "openai_compatible",
      base_url: "https://api.groq.com/openai/v1",
      model: "llama-3.3-70b-versatile",
      structured_mode: "json_schema",
      sampling: { think: false, temperature: 0.7, top_p: 0.8, top_k: null, seed: 1234, max_tokens: 3072, timeout_s: 60 },
      api_key_env: "GROQ_API_KEY",
    });
    expect("api_key" in body).toBe(false);
  });

  it("adds api_key only when one was typed, trimmed", () => {
    const draft = draftFromProvider(groq);
    expect("api_key" in providerBody(draft, "   ")).toBe(false);
    expect(providerBody(draft, "  gsk_new  ").api_key).toBe("gsk_new");
    expect("api_key" in rowWithModel(groq, "qwen/qwen3-32b")).toBe(false);
    expect(rowWithModel(groq, "qwen/qwen3-32b").model).toBe("qwen/qwen3-32b");
  });

  it("fills a draft from a preset, and from a template without one", () => {
    const d = draftFromTemplate(hosted, hosted.presets[0]);
    expect(d).toMatchObject({ isNew: true, id: "cerebras", label: "Cerebras", base_url: "https://api.cerebras.ai/v1", model: "qwen-3-32b", api_key_env: "CEREBRAS_API_KEY" });
    expect(draftFromTemplate(hosted, null)).toMatchObject({ id: "", label: "", base_url: "" });
  });

  it("keeps structured_mode valid when the kind changes", () => {
    const d = editDraft(draftFromProvider(groq), "kind", "ollama");
    expect(d.structured_mode).toBe("ollama_format");
    expect(editDraft(d, "kind", "openai_compatible").structured_mode).toBe("json_schema");
    expect(editDraft({ ...d, structured_mode: "json_object" }, "kind", "openai_compatible").structured_mode).toBe("json_object"); // valid for both
  });

  it("checks what the server would refuse", () => {
    const d = { ...draftFromTemplate(hosted, null), id: "bad id", base_url: "api.example.com", model: "" };
    const errors = draftErrors(d, []);
    expect(Object.keys(errors).sort()).toEqual(["base_url", "id", "model"]);
    expect(draftErrors({ ...draftFromProvider(groq), isNew: true }, ["groq"]).id).toMatch(/already exists/);
    expect(draftErrors({ ...draftFromProvider(groq), id: "env" }, []).id).toMatch(/reserved/);
    expect(draftErrors({ ...draftFromProvider(groq), api_key_env: "GROQ_SECRET" }, []).api_key_env).toMatch(/KEY or TOKEN/);
    const sampling = { ...draftFromProvider(groq).sampling, top_p: "0", max_tokens: "12.5", top_k: "" };
    const se = draftErrors({ ...draftFromProvider(groq), sampling }, []);
    expect(se["sampling.top_p"]).toBe("> 0 and ≤ 1");
    expect(se["sampling.max_tokens"]).toBe("a whole number");
    expect(se["sampling.top_k"]).toBeUndefined(); // nullable
  });

  it("moves fallbacks without touching the original list", () => {
    const list = ["a", "b", "c"];
    expect(moveItem(list, 2, -1)).toEqual(["a", "c", "b"]);
    expect(moveItem(list, 0, -1)).toEqual(["a", "b", "c"]);
    expect(list).toEqual(["a", "b", "c"]);
  });

  it("summarises a probe, keeping the error verbatim", () => {
    const s = testSummary({
      ok: false,
      latency_ms: 812,
      model: "m",
      error: "HTTP 401: Invalid API Key (sent Bearer …4f2a)",
      models: { ok: false },
      chat: { ok: false, latency_ms: 400 },
      schema_roundtrip: { ok: false, errors: ["steps: required"] },
    });
    expect(s).toMatchObject({ ok: false, latencyMs: 812, error: "HTTP 401: Invalid API Key (sent Bearer …4f2a)", schemaErrors: ["steps: required"] });
    expect(s.steps.map((x) => x.name)).toEqual(["models", "chat", "schema"]);
    expect(testSummary(null)).toBeNull();
  });

  it("turns a 422 into loc: msg lines", () => {
    const data = { detail: [{ loc: ["body", "sampling", "top_p"], msg: "Input should be greater than 0" }, { loc: ["body", "model"], msg: "Field required" }] };
    expect(llmErrorText(data, 422)).toBe("sampling.top_p: Input should be greater than 0; model: Field required");
    expect(llmErrorText({ detail: "unknown provider 'x'" }, 404)).toBe("unknown provider 'x'");
    expect(llmErrorText({}, 500)).toBe("Request failed (500)");
  });
});
