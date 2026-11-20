// Pure helpers for the Models page: form drafts, request bodies, role chains, test summaries.
// A draft never holds an API key: the key field is an uncontrolled password input read once at
// submit (see ProviderForm.jsx), so nothing here can carry one into React state.

export const ROLES = [
  { id: "executive", name: "Executive", blurb: "plans missions" },
  { id: "instance_agent", name: "Instance agent", blurb: "not built yet" },
];

/* backend/hive_exec/llm/providers.py MODES_BY_KIND */
export const MODES_BY_KIND = {
  ollama: ["ollama_format", "json_object"],
  openai_compatible: ["json_schema", "structured_outputs", "guided_json", "json_object"],
};
export const KINDS = Object.keys(MODES_BY_KIND);

export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const KEY_ENV_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

/* providers.py Sampling, in form order. think is always false and is not a field. */
export const SAMPLING_FIELDS = [
  { key: "temperature", label: "temperature", step: "0.05", min: 0, max: 2, int: false, nullable: false },
  { key: "top_p", label: "top_p", step: "0.05", min: 0, max: 1, int: false, nullable: false, exclusiveMin: true },
  { key: "top_k", label: "top_k", step: "1", min: 1, int: true, nullable: true },
  { key: "seed", label: "seed", step: "1", int: true, nullable: true },
  { key: "max_tokens", label: "max_tokens", step: "1", min: 16, max: 131072, int: true, nullable: false },
  { key: "timeout_s", label: "timeout_s", step: "1", min: 0, max: 600, int: false, nullable: false, exclusiveMin: true },
];
export const DEFAULT_SAMPLING = { temperature: 0.2, top_p: 0.8, top_k: 20, seed: 1234, max_tokens: 3072, timeout_s: 60, think: false };

/* a template has no id; these are the suggestions (presets bring their own) */
const TEMPLATE_IDS = { ollama_local: "ollama", vt_arc_vllm: "arc-vllm", hosted_oss: "", local_openai: "local" };

const text = (v) => (v === null || v === undefined ? "" : String(v));

function samplingDraft(sampling) {
  const s = { ...DEFAULT_SAMPLING, ...(sampling || {}) };
  return Object.fromEntries(SAMPLING_FIELDS.map((f) => [f.key, text(s[f.key])]));
}

/* An existing row (GET /providers) as an editable draft. */
export function draftFromProvider(p) {
  return {
    isNew: false,
    id: p.id,
    label: p.label && p.label !== p.id ? p.label : "",
    kind: p.kind,
    base_url: p.base_url,
    model: p.model,
    structured_mode: p.structured_mode,
    api_key_env: p.key_env || "",
    sampling: samplingDraft(p.sampling),
  };
}

/* A template (GET /templates), optionally narrowed by one of its presets. */
export function draftFromTemplate(template, preset = null) {
  const kind = template.kind;
  const mode = preset?.structured_mode || template.structured_mode;
  return {
    isNew: true,
    templateId: template.template_id,
    id: preset?.id ?? TEMPLATE_IDS[template.template_id] ?? "",
    label: preset?.label ?? (template.template_id === "hosted_oss" ? "" : template.label),
    kind,
    base_url: preset?.base_url ?? template.base_url ?? "",
    model: preset?.model ?? template.model ?? "",
    structured_mode: MODES_BY_KIND[kind]?.includes(mode) ? mode : MODES_BY_KIND[kind]?.[0] ?? "",
    api_key_env: preset?.key_env ?? "",
    sampling: samplingDraft(template.sampling),
  };
}

/* The draft with one field changed; a kind change keeps structured_mode valid for the kind. */
export function editDraft(draft, field, value) {
  if (field === "kind") {
    const modes = MODES_BY_KIND[value] || [];
    return { ...draft, kind: value, structured_mode: modes.includes(draft.structured_mode) ? draft.structured_mode : modes[0] };
  }
  return { ...draft, [field]: value };
}

export function editSampling(draft, key, value) {
  return { ...draft, sampling: { ...draft.sampling, [key]: value } };
}

function parseSampling(sampling) {
  const out = { think: false };
  const errors = {};
  for (const f of SAMPLING_FIELDS) {
    const raw = text(sampling?.[f.key]).trim();
    if (raw === "") {
      if (f.nullable) out[f.key] = null;
      else errors[`sampling.${f.key}`] = "required";
      continue;
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || (f.int && !Number.isInteger(n))) {
      errors[`sampling.${f.key}`] = f.int ? "a whole number" : "a number";
      continue;
    }
    const low = f.min !== undefined && (f.exclusiveMin ? n <= f.min : n < f.min);
    if (low || (f.max !== undefined && n > f.max)) {
      const lo = f.min === undefined ? "" : `${f.exclusiveMin ? ">" : "≥"} ${f.min}`;
      const hi = f.max === undefined ? "" : `≤ ${f.max}`;
      errors[`sampling.${f.key}`] = [lo, hi].filter(Boolean).join(" and ");
      continue;
    }
    out[f.key] = n;
  }
  return { sampling: out, errors };
}

/* Client-side checks that mirror ProviderRow, so a key is rarely typed into a form the server will refuse. */
export function draftErrors(draft, existingIds = []) {
  const errors = {};
  const id = draft.id.trim();
  if (!ID_PATTERN.test(id)) errors.id = "letters, digits, . _ - (up to 64), starting with a letter or digit";
  else if (draft.isNew && existingIds.includes(id)) errors.id = `${id} already exists: edit that card, or pick another id`;
  else if (id === "env") errors.id = "env is reserved for the QWEN_* row";
  if (!/^https?:\/\//.test(draft.base_url.trim())) errors.base_url = "must start with http:// or https://";
  if (!draft.model.trim()) errors.model = "required";
  if (!(MODES_BY_KIND[draft.kind] || []).includes(draft.structured_mode)) errors.structured_mode = `not available for ${draft.kind}`;
  const env = draft.api_key_env.trim();
  if (env && (!KEY_ENV_PATTERN.test(env) || !/KEY|TOKEN/i.test(env))) errors.api_key_env = "a variable name containing KEY or TOKEN";
  return { ...errors, ...parseSampling(draft.sampling).errors };
}

/* POST /providers body. api_key is present only when one was typed: blank keeps the stored key. */
export function providerBody(draft, key = "") {
  const body = {
    id: draft.id.trim(),
    label: draft.label.trim(),
    kind: draft.kind,
    base_url: draft.base_url.trim(),
    model: draft.model.trim(),
    structured_mode: draft.structured_mode,
    sampling: parseSampling(draft.sampling).sampling,
    api_key_env: draft.api_key_env.trim(),
  };
  const typed = typeof key === "string" ? key.trim() : "";
  return typed ? { ...body, api_key: typed } : body;
}

/* A stored row re-posted with only `model` changed (the roles' model picker); never a key. */
export function rowWithModel(p, model) {
  return providerBody({ ...draftFromProvider(p), model }, "");
}

export const chainOf = (roles, role) => (Array.isArray(roles?.[role]) ? roles[role] : []);

/* A new list with item i moved by delta (−1 up, +1 down); out of range returns a copy. */
export function moveItem(list, i, delta) {
  const j = i + delta;
  const out = [...list];
  if (i < 0 || i >= list.length || j < 0 || j >= list.length) return out;
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/* ok / latency / the provider's verbatim error, for the card. */
export function testSummary(result) {
  if (!result) return null;
  const steps = [
    ["models", result.models],
    ["chat", result.chat],
    ["schema", result.schema_roundtrip],
  ]
    .filter(([, s]) => s)
    .map(([name, s]) => ({ name, ok: Boolean(s.ok), ms: Number.isFinite(s.latency_ms) ? s.latency_ms : null }));
  return {
    ok: Boolean(result.ok),
    latencyMs: Number.isFinite(result.latency_ms) ? result.latency_ms : null,
    error: typeof result.error === "string" ? result.error : "",
    model: result.model || "",
    echo: result.chat?.model_echo || "",
    steps,
    schemaErrors: Array.isArray(result.schema_roundtrip?.errors) ? result.schema_roundtrip.errors : [],
  };
}
