// Add or edit one provider row.
//
// THE KEY FIELD IS WRITE-ONLY. It is an uncontrolled password input: React never holds its value,
// submit reads it once, empties the input, and hands the string straight to the request body. A
// blank field sends no api_key at all, so an edit that did not touch the key keeps the stored one.
// The saved key is shown only as the server's four-character hint, in the placeholder.
//
// THE MODEL FIELD IS ALWAYS FREE TEXT. The provider's models endpoint fills a <datalist>, which
// suggests; it never restricts, because model ids churn faster than any list.
import React, { useEffect, useRef, useState } from "react";
import { Caret } from "../chat/ui.jsx";
import { KINDS, MODES_BY_KIND, SAMPLING_FIELDS, draftErrors, editDraft, editSampling, providerBody } from "./settingsModel.js";

const LABEL = "grid gap-1.5 text-[13px] font-medium text-muted";

function FieldError({ text, id }) {
  if (!text) return null;
  return (
    <span className="text-[13px] font-normal text-danger" id={id}>
      {text}
    </span>
  );
}

/* the suggestion list for a model input, or a one-line note when the provider lists none */
export function ModelHint({ entry, listId }) {
  if (!entry) return null;
  if (entry.loading) return <span className="text-[12px] font-normal text-faint">Asking the provider for its models…</span>;
  if (!entry.ok) {
    return (
      <span className="text-[12px] font-normal text-faint">
        No model list: <span className="font-mono">{entry.error || "the provider did not answer"}</span>. Type any id.
      </span>
    );
  }
  return (
    <>
      <datalist id={listId}>
        {entry.models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <span className="text-[12px] font-normal text-faint">{entry.models.length} models offered, or type any id.</span>
    </>
  );
}

export default function ProviderForm({ initial, template, provider, existingIds = [], models, onRequestModels, onSave, onCancel }) {
  const [draft, setDraft] = useState(initial);
  const [advanced, setAdvanced] = useState(false);
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState(null);
  const keyRef = useRef(null);
  const isNew = draft.isNew;
  const slug = isNew ? "new" : draft.id;
  const listId = `models-form-${slug}`;

  useEffect(() => {
    if (!isNew && provider?.id) onRequestModels?.(provider.id);
  }, [isNew, provider?.id, onRequestModels]);

  const set = (field) => (event) => setDraft((d) => editDraft(d, field, event.target.value));

  async function submit(event) {
    event.preventDefault();
    const found = draftErrors(draft, existingIds);
    setErrors(found);
    if (Object.keys(found).length) {
      if (Object.keys(found).some((k) => k.startsWith("sampling.") || k === "structured_mode" || k === "api_key_env")) setAdvanced(true);
      return;
    }
    // Read the key once and empty the field before anything else happens: from here on the
    // only copy is the request body, which goes out of scope when the request ends.
    const input = keyRef.current;
    const typed = input ? input.value : "";
    if (input) input.value = "";
    setSaving(true);
    setFailure(null);
    try {
      await onSave(providerBody(draft, typed));
    } catch (error) {
      setFailure({ text: error.message, keyCleared: Boolean(typed.trim()) });
      setSaving(false);
    }
  }

  const modes = MODES_BY_KIND[draft.kind] || [];
  const keyPlaceholder = provider?.key_present
    ? `blank keeps the saved key ${provider.key_hint}`
    : template?.key === "none"
      ? "not needed for this server"
      : template?.key === "optional"
        ? "only if the server was started with --api-key"
        : "paste the key";

  return (
    <form
      aria-label={isNew ? "New provider" : `Edit ${draft.id}`}
      autoComplete="off"
      className="grid gap-4 rounded-2xl border border-coral/60 bg-card p-5"
      noValidate
      onSubmit={submit}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[17px] font-semibold">{isNew ? `New provider${template ? ` · ${template.label}` : ""}` : `Edit ${draft.id}`}</p>
        <span className="font-mono text-[12px] text-faint">{draft.kind}</span>
      </div>
      {template?.hint && <p className="max-w-[72ch] rounded-xl border border-line bg-sidebar px-4 py-3 text-[14px] leading-relaxed text-muted">{template.hint}</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className={LABEL}>
          Provider id
          <input
            aria-describedby={errors.id ? "err-id" : undefined}
            aria-invalid={Boolean(errors.id)}
            autoCapitalize="off"
            autoComplete="off"
            className={`field font-mono ${errors.id ? "border-danger" : ""}`}
            disabled={!isNew}
            name="id"
            onChange={set("id")}
            placeholder="e.g. together"
            spellCheck={false}
            value={draft.id}
          />
          <FieldError id="err-id" text={errors.id} />
        </label>
        <label className={LABEL}>
          Label
          <input autoComplete="off" className="field" name="label" onChange={set("label")} placeholder={draft.id || "shown in the planner chip"} value={draft.label} />
        </label>
        <label className={LABEL}>
          Kind
          <select className="field font-mono" name="kind" onChange={set("kind")} value={draft.kind}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className={LABEL}>
          Base URL
          <input
            aria-invalid={Boolean(errors.base_url)}
            autoCapitalize="off"
            autoComplete="off"
            className={`field font-mono ${errors.base_url ? "border-danger" : ""}`}
            name="base_url"
            onChange={set("base_url")}
            placeholder="https://api.example.com/v1"
            spellCheck={false}
            value={draft.base_url}
          />
          <FieldError text={errors.base_url} />
        </label>
        <label className={LABEL}>
          Model
          <input
            aria-invalid={Boolean(errors.model)}
            autoCapitalize="off"
            autoComplete="off"
            className={`field font-mono ${errors.model ? "border-danger" : ""}`}
            list={models?.ok ? listId : undefined}
            name="model"
            onChange={set("model")}
            placeholder="model id"
            spellCheck={false}
            value={draft.model}
          />
          <FieldError text={errors.model} />
          <ModelHint entry={models} listId={listId} />
        </label>
        <label className={LABEL}>
          API key
          <input
            autoCapitalize="off"
            autoComplete="off"
            className="field font-mono"
            data-1p-ignore=""
            data-bwignore=""
            data-lpignore="true"
            name="hive-provider-secret"
            placeholder={keyPlaceholder}
            ref={keyRef}
            spellCheck={false}
            type="password"
          />
          <span className="text-[12px] font-normal text-faint">Write-only: stored in ~/.hive/providers.yaml (0600), never sent back to this page.</span>
        </label>
      </div>

      <div className="rounded-xl border border-line">
        <button
          aria-controls={`advanced-${slug}`}
          aria-expanded={advanced}
          className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-[14px] font-medium text-muted hover:text-ink"
          onClick={() => setAdvanced((a) => !a)}
          type="button"
        >
          <Caret open={advanced} />
          Advanced
          <span className="ml-auto truncate font-mono text-[12px] text-faint">
            {draft.structured_mode} · temp {draft.sampling.temperature || "—"}
          </span>
        </button>
        {advanced && (
          <div className="grid gap-4 border-t border-line px-4 pb-4 pt-3" id={`advanced-${slug}`}>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className={LABEL}>
                structured_mode
                <select className="field font-mono" name="structured_mode" onChange={set("structured_mode")} value={draft.structured_mode}>
                  {modes.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
                <FieldError text={errors.structured_mode} />
              </label>
              <label className={LABEL}>
                Key variable
                <input
                  autoCapitalize="off"
                  autoComplete="off"
                  className={`field font-mono ${errors.api_key_env ? "border-danger" : ""}`}
                  name="api_key_env"
                  onChange={set("api_key_env")}
                  placeholder="derived, e.g. HIVE_KEY_LOCAL"
                  spellCheck={false}
                  value={draft.api_key_env}
                />
                <FieldError text={errors.api_key_env} />
              </label>
            </div>
            <fieldset className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <legend className="mb-2 text-[13px] font-medium text-muted">Sampling (thinking stays off)</legend>
              {SAMPLING_FIELDS.map((f) => (
                <label className={LABEL} key={f.key}>
                  <span className="font-mono">{f.label}</span>
                  <input
                    aria-invalid={Boolean(errors[`sampling.${f.key}`])}
                    className={`field font-mono ${errors[`sampling.${f.key}`] ? "border-danger" : ""}`}
                    inputMode={f.int ? "numeric" : "decimal"}
                    name={`sampling.${f.key}`}
                    onChange={(event) => setDraft((d) => editSampling(d, f.key, event.target.value))}
                    placeholder={f.nullable ? "unset" : ""}
                    value={draft.sampling[f.key]}
                  />
                  <FieldError text={errors[`sampling.${f.key}`]} />
                </label>
              ))}
            </fieldset>
          </div>
        )}
      </div>

      {failure && (
        <p className="rounded-xl border border-danger/50 bg-danger/10 px-4 py-3 text-[14px]" role="alert">
          <span className="font-semibold text-danger">Not saved. </span>
          <span className="font-mono text-[13px]">{failure.text}</span>
          {failure.keyCleared && <span className="block pt-1 text-muted">The key field was cleared; paste the key again.</span>}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-primary" disabled={saving} type="submit">
          {saving ? "Saving…" : isNew ? "Add provider" : "Save"}
        </button>
        <button className="btn btn-secondary" disabled={saving} onClick={onCancel} type="button">
          Cancel
        </button>
      </div>
    </form>
  );
}
