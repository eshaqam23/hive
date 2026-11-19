// One role: a primary provider, its model, and an ordered fallback chain.
//
// A role is an ordered chain of provider ids (POST /roles). The model belongs to the provider row
// (the store has no per-role model), so a changed model is saved onto that row first, without a
// key, and every role that uses the row runs it. Fallbacks are tried only on transport errors.
import React, { useEffect, useState } from "react";
import { ModelHint } from "./ProviderForm.jsx";
import { chainOf, moveItem } from "./settingsModel.js";

const SMALL = "rounded-md border border-line-strong px-2 py-1 text-[13px] font-medium text-muted hover:border-ink hover:text-ink disabled:opacity-40";

function initialDraft(roles, role, providers) {
  const chain = chainOf(roles, role);
  const primary = chain[0] ?? "";
  return { primary, model: providers.find((p) => p.id === primary)?.model ?? "", fallbacks: chain.slice(1) };
}

export default function RoleCard({ role, roles, providers, modelsById = {}, onRequestModels, onSave, busy = false }) {
  const chain = chainOf(roles, role.id);
  const serverKey = `${chain.join(",")}|${providers.find((p) => p.id === chain[0])?.model ?? ""}`;
  const [draft, setDraft] = useState(() => initialDraft(roles, role.id, providers));
  const [failure, setFailure] = useState(null);
  const [saving, setSaving] = useState(false);

  // a save anywhere on the page (or a delete that unbinds a row) re-reads the chain; keyed on the
  // chain and model only, so an unrelated save does not throw away an unsaved draft
  useEffect(() => {
    setDraft(initialDraft(roles, role.id, providers));
  }, [serverKey]);

  useEffect(() => {
    if (draft.primary) onRequestModels?.(draft.primary);
  }, [draft.primary, onRequestModels]);

  const byId = Object.fromEntries(providers.map((p) => [p.id, p]));
  const bindable = providers.filter((p) => !p.read_only);
  const primaryRow = byId[draft.primary];
  const boundEnv = chain[0] && byId[chain[0]]?.read_only;
  const dirty = serverKey !== `${[draft.primary, ...draft.fallbacks].filter(Boolean).join(",")}|${draft.model}` && !(boundEnv && draft.primary === chain[0]);
  const spare = bindable.filter((p) => p.id !== draft.primary && !draft.fallbacks.includes(p.id));
  const sharedWith = draft.primary ? (primaryRow?.roles || []).filter((r) => r !== role.id) : [];
  const listId = `models-role-${role.id}`;
  const models = draft.primary ? (modelsById[draft.primary] ?? null) : null;

  function pickPrimary(id) {
    // no primary means unbound: a chain is never fallbacks alone
    setDraft((d) => ({ primary: id, model: byId[id]?.model ?? "", fallbacks: id ? d.fallbacks.filter((f) => f !== id) : [] }));
  }

  async function save() {
    setSaving(true);
    setFailure(null);
    try {
      await onSave(role.id, { chain: draft.primary ? [draft.primary, ...draft.fallbacks] : [], model: draft.model.trim() });
    } catch (error) {
      setFailure(error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-label={`${role.name} role`} className="grid gap-4 rounded-2xl border border-line bg-card p-5">
      <header>
        <h3 className="text-[17px] font-semibold">
          {role.name}: <span className="font-normal text-muted">{role.blurb}</span>
        </h3>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="grid gap-1.5 text-[13px] font-medium text-muted">
          Provider
          <select
            className="field font-mono"
            disabled={busy || saving || (bindable.length === 0 && !draft.primary)}
            onChange={(event) => pickPrimary(event.target.value)}
            value={draft.primary}
          >
            <option value="">— not bound —</option>
            {boundEnv && (
              <option disabled value={chain[0]}>
                {chain[0]} (QWEN_* env, read-only)
              </option>
            )}
            {bindable.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id}
                {p.label && p.label !== p.id ? ` · ${p.label}` : ""}
              </option>
            ))}
          </select>
          {primaryRow && !primaryRow.key_present && primaryRow.kind !== "ollama" && (
            <span className="text-[12px] font-normal text-warn">No key stored for {primaryRow.id}.</span>
          )}
        </label>
        <label className="grid gap-1.5 text-[13px] font-medium text-muted">
          Model
          <input
            autoCapitalize="off"
            autoComplete="off"
            className="field font-mono"
            disabled={!draft.primary || Boolean(primaryRow?.read_only) || busy || saving}
            list={models?.ok ? listId : undefined}
            onChange={(event) => setDraft((d) => ({ ...d, model: event.target.value }))}
            placeholder={draft.primary ? "model id" : "pick a provider first"}
            spellCheck={false}
            value={draft.model}
          />
          {draft.primary && <ModelHint entry={models} listId={listId} />}
          {draft.primary && primaryRow && draft.model.trim() !== primaryRow.model && (
            <span className="text-[12px] font-normal text-faint">
              Saved on {primaryRow.id}
              {sharedWith.length ? `, which the ${sharedWith.join(" and ")} role also uses` : ""}.
            </span>
          )}
        </label>
      </div>

      <div>
        <p className="text-[13px] font-medium text-muted">Fallbacks, in order (used on connect errors, timeouts, 5xx and 429)</p>
        {draft.fallbacks.length === 0 ? (
          <p className="mt-1.5 text-[14px] text-faint">None.</p>
        ) : (
          <ol aria-label={`${role.name} fallbacks`} className="mt-2 grid gap-1.5">
            {draft.fallbacks.map((id, i) => (
              <li className="flex items-center gap-2 rounded-xl border border-line bg-sidebar px-3 py-2" key={id}>
                <span className="w-5 font-mono text-[12px] text-faint">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[14px]">
                  {id}
                  {byId[id] && <span className="text-faint"> · {byId[id].model}</span>}
                </span>
                <button aria-label={`Move ${id} up`} className={SMALL} disabled={i === 0 || saving} onClick={() => setDraft((d) => ({ ...d, fallbacks: moveItem(d.fallbacks, i, -1) }))} type="button">
                  ↑
                </button>
                <button
                  aria-label={`Move ${id} down`}
                  className={SMALL}
                  disabled={i === draft.fallbacks.length - 1 || saving}
                  onClick={() => setDraft((d) => ({ ...d, fallbacks: moveItem(d.fallbacks, i, 1) }))}
                  type="button"
                >
                  ↓
                </button>
                <button aria-label={`Remove ${id}`} className={SMALL} disabled={saving} onClick={() => setDraft((d) => ({ ...d, fallbacks: d.fallbacks.filter((f) => f !== id) }))} type="button">
                  ✕
                </button>
              </li>
            ))}
          </ol>
        )}
        {draft.primary && spare.length > 0 && (
          <select
            aria-label={`Add a ${role.name} fallback`}
            className="field mt-2 max-w-xs font-mono"
            disabled={saving}
            onChange={(event) => {
              const id = event.target.value;
              if (id) setDraft((d) => ({ ...d, fallbacks: [...d.fallbacks, id] }));
            }}
            value=""
          >
            <option value="">+ add a fallback…</option>
            {spare.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id}
              </option>
            ))}
          </select>
        )}
      </div>

      {failure && (
        <p className="rounded-xl border border-danger/50 bg-danger/10 px-4 py-3 font-mono text-[13px]" role="alert">
          {failure}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-primary min-h-10 px-5 text-[14px]" disabled={!dirty || saving || busy || (draft.primary && !draft.model.trim())} onClick={save} type="button">
          {saving ? "Saving…" : `Save ${role.name.toLowerCase()} role`}
        </button>
        {dirty && (
          <button className="btn btn-secondary min-h-10 px-4 text-[14px]" disabled={saving} onClick={() => setDraft(initialDraft(roles, role.id, providers))} type="button">
            Undo
          </button>
        )}
      </div>
    </section>
  );
}
