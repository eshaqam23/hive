// The Models page: which provider and model each role runs on, and the providers behind them.
//
// Ported from Bhavya007-17/hive@4fa0156 dashboard/src/settings/SettingsPage.tsx: roles on top,
// because "what is the Executive planning with?" is the question an operator arrives with, and the
// providers below as the supporting cast.
//
// STATE IS LOCAL to this page, not in useExecutive: nothing polls a provider list, and request /
// response state here must not leak into the mission view. After every write the page calls
// onChanged (App's exec.refresh), so the Chat tab's planner chip shows the new binding at once.
//
// No API key is ever held here. Reads carry key_present and a four-character hint only, and the
// one place a key is typed (ProviderForm) is an uncontrolled input emptied at submit.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Eyebrow } from "../chat/ui.jsx";
import { deleteProvider, getProviders, getTemplates, listModels, saveProvider, saveRoles, testProvider } from "./api.js";
import ProviderCard from "./ProviderCard.jsx";
import ProviderForm from "./ProviderForm.jsx";
import RoleCard from "./RoleCard.jsx";
import { ROLES, draftFromProvider, draftFromTemplate, rowWithModel } from "./settingsModel.js";

const without = (obj, key) => {
  const { [key]: _gone, ...rest } = obj;
  return rest;
};

function PresetButton({ template, preset, existingIds, onPick }) {
  const added = preset && existingIds.includes(preset.id);
  return (
    <button
      className="chip min-h-9 px-3 text-[13px] disabled:cursor-not-allowed disabled:opacity-50"
      disabled={added}
      onClick={() => onPick(template, preset)}
      title={added ? `${preset.id} already exists: edit its card` : preset ? `${preset.base_url} · ${preset.model}` : "start from the template's defaults"}
      type="button"
    >
      {preset ? preset.label : "Other"}
      {added && <span className="text-faint">added</span>}
    </button>
  );
}

function TemplatePicker({ templates, existingIds, onPick, onCancel }) {
  return (
    <div aria-label="Provider templates" className="grid gap-3 rounded-2xl border border-coral/60 bg-card p-5" role="group">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-[17px] font-semibold">Add a provider from a template</p>
        <button className="btn btn-secondary min-h-9 px-4 text-[14px]" onClick={onCancel} type="button">
          Cancel
        </button>
      </div>
      {templates.length === 0 && <p className="text-[14px] text-faint">No templates came back from GET /api/exec/llm/templates.</p>}
      <ul className="grid gap-3">
        {templates.map((t) => (
          <li className="grid gap-2 rounded-xl border border-line bg-sidebar p-4" key={t.template_id}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-[15px] font-semibold">{t.label}</p>
              <span className="font-mono text-[12px] text-faint">
                {t.kind}
                {t.base_url ? ` · ${t.base_url}` : ""}
              </span>
            </div>
            {t.hint && <p className="text-[13px] leading-relaxed text-muted">{t.hint}</p>}
            <div className="flex flex-wrap gap-1.5">
              {Array.isArray(t.presets) && t.presets.length > 0 ? (
                <>
                  {t.presets.map((p) => (
                    <PresetButton existingIds={existingIds} key={p.id} onPick={onPick} preset={p} template={t} />
                  ))}
                  <PresetButton existingIds={existingIds} onPick={onPick} template={t} />
                </>
              ) : (
                <button className="btn btn-secondary min-h-9 px-4 text-[14px]" onClick={() => onPick(t, null)} type="button">
                  Use {t.label}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function SettingsPage({ onChanged }) {
  const [view, setView] = useState(null); // { providers, roles, problems, store }
  const [loadError, setLoadError] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [tests, setTests] = useState({});
  const [testing, setTesting] = useState({});
  const [models, setModels] = useState({});
  const [editing, setEditing] = useState(null);
  const [picking, setPicking] = useState(false);
  const [adding, setAdding] = useState(null); // { template, draft }
  const [busy, setBusy] = useState(null);
  const [pageError, setPageError] = useState(null);
  const asked = useRef(new Set());

  const load = useCallback(async () => {
    setLoadError(null);
    const [providers, tpl] = await Promise.allSettled([getProviders(), getTemplates()]);
    if (providers.status === "fulfilled") setView(providers.value);
    else setLoadError(providers.reason?.message || "unreachable");
    if (tpl.status === "fulfilled" && Array.isArray(tpl.value)) setTemplates(tpl.value);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /* one models listing per provider per visit; the pickers stay free text while it runs or if it fails */
  const requestModels = useCallback((id) => {
    if (!id || asked.current.has(id)) return;
    asked.current.add(id);
    setModels((m) => ({ ...m, [id]: { loading: true, ok: false, models: [], error: "" } }));
    listModels(id)
      .then((r) => setModels((m) => ({ ...m, [id]: { loading: false, ok: Boolean(r.ok), models: Array.isArray(r.models) ? r.models : [], error: r.error || "" } })))
      .catch((error) => setModels((m) => ({ ...m, [id]: { loading: false, ok: false, models: [], error: error.message } })));
  }, []);

  function forget(id) {
    asked.current.delete(id);
    setModels((m) => without(m, id));
    setTests((t) => without(t, id));
  }

  function absorb(next) {
    setView(next);
    onChanged?.();
  }

  async function saveRow(body) {
    const next = await saveProvider(body);
    forget(body.id);
    absorb(next);
  }

  async function saveRole(role, { chain, model }) {
    const primary = view?.providers?.find((p) => p.id === chain[0]);
    if (primary && model && model !== primary.model) {
      absorb(await saveProvider(rowWithModel(primary, model)));
      setTests((t) => without(t, primary.id));
    }
    absorb(await saveRoles({ [role]: chain }));
  }

  async function runTest(id) {
    setTesting((t) => ({ ...t, [id]: true }));
    try {
      const result = await testProvider(id);
      setTests((t) => ({ ...t, [id]: result }));
    } catch (error) {
      setTests((t) => ({ ...t, [id]: { ok: false, provider_id: id, error: error.message } }));
    } finally {
      setTesting((t) => without(t, id));
    }
  }

  async function remove(id) {
    setBusy(id);
    setPageError(null);
    try {
      const next = await deleteProvider(id);
      forget(id);
      if (editing === id) setEditing(null);
      absorb(next);
    } catch (error) {
      setPageError(`Delete ${id}: ${error.message}`);
    } finally {
      setBusy(null);
    }
  }

  const providers = view?.providers || [];
  const ids = providers.map((p) => p.id);

  return (
    <div className="mx-auto w-full max-w-[920px] px-4 pb-16 pt-10 sm:px-8">
      <p className="break-all font-mono text-[13px] text-faint">/api/exec/llm · {view?.store?.path ?? "~/.hive/providers.yaml"}</p>
      <h1 className="mt-2 font-display text-[clamp(32px,3.4vw,48px)] leading-[1.05] tracking-display">Models</h1>
      <p className="mt-2 max-w-[62ch] text-[16px] leading-relaxed text-muted">
        Which model each role runs on, and the providers behind them. Keys are written to <span className="font-mono text-[14px]">~/.hive/providers.yaml</span> and never read back
        out; this page only ever sees the last four characters.
      </p>

      {loadError && (
        <div className="mt-6 flex flex-wrap items-center gap-3 rounded-xl border border-danger/50 bg-danger/10 px-4 py-3 text-[14px]" role="alert">
          <span>
            The Executive did not answer: <span className="font-mono">{loadError}</span>
          </span>
          <button className="btn btn-secondary min-h-9 px-4 text-[14px]" onClick={load} type="button">
            Retry
          </button>
        </div>
      )}
      {view?.store?.note && <p className="mt-6 rounded-xl border border-warn/50 bg-warn/10 px-4 py-3 text-[14px]">{view.store.note}</p>}
      {view?.problems?.length > 0 && (
        <div className="mt-6 rounded-xl border border-warn/50 bg-warn/10 px-4 py-3 text-[14px]" role="status">
          <p className="font-semibold">The provider store has problems</p>
          <ul className="mt-1 grid gap-0.5 font-mono text-[13px]">
            {view.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {pageError && (
        <p className="mt-6 rounded-xl border border-danger/50 bg-danger/10 px-4 py-3 font-mono text-[13px]" role="alert">
          {pageError}
        </p>
      )}

      <section aria-labelledby="roles-title" className="mt-10">
        <Eyebrow>Roles</Eyebrow>
        <h2 className="mt-1 font-display text-[28px] leading-tight tracking-title" id="roles-title">
          Who runs what
        </h2>
        {view && providers.filter((p) => !p.read_only).length === 0 && <p className="mt-2 text-[14px] text-faint">Add a provider below, then bind a role to it.</p>}
        <div className="mt-4 grid gap-4">
          {view &&
            ROLES.map((role) => (
              <RoleCard key={role.id} modelsById={models} onRequestModels={requestModels} onSave={saveRole} providers={providers} role={role} roles={view.roles} />
            ))}
          {!view && !loadError && <p className="text-[14px] text-faint">Loading…</p>}
        </div>
      </section>

      <section aria-labelledby="providers-title" className="mt-12">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <Eyebrow>Providers</Eyebrow>
            <h2 className="mt-1 font-display text-[28px] leading-tight tracking-title" id="providers-title">
              Endpoints and keys
            </h2>
          </div>
          {view && !picking && !adding && (
            <button className="btn btn-primary min-h-10 px-5 text-[14px]" onClick={() => setPicking(true)} type="button">
              Add provider
            </button>
          )}
        </div>
        <div className="mt-4 grid gap-4">
          {picking && (
            <TemplatePicker
              existingIds={ids}
              onCancel={() => setPicking(false)}
              onPick={(template, preset) => {
                setPicking(false);
                setAdding({ template, draft: draftFromTemplate(template, preset) });
              }}
              templates={templates}
            />
          )}
          {adding && (
            <ProviderForm
              existingIds={ids}
              initial={adding.draft}
              onCancel={() => setAdding(null)}
              onSave={async (body) => {
                await saveRow(body);
                setAdding(null);
              }}
              template={adding.template}
            />
          )}
          {providers.map((p) =>
            editing === p.id ? (
              <ProviderForm
                initial={draftFromProvider(p)}
                key={p.id}
                models={models[p.id] ?? null}
                onCancel={() => setEditing(null)}
                onRequestModels={requestModels}
                onSave={async (body) => {
                  await saveRow(body);
                  setEditing(null);
                }}
                provider={p}
                template={templates.find((t) => t.kind === p.kind && t.presets?.some((x) => x.id === p.id)) ?? null}
              />
            ) : (
              <ProviderCard
                busy={busy === p.id}
                key={p.id}
                onDelete={() => remove(p.id)}
                onEdit={() => setEditing(p.id)}
                onTest={() => runTest(p.id)}
                provider={p}
                test={tests[p.id]}
                testing={Boolean(testing[p.id])}
              />
            ),
          )}
          {view && providers.length === 0 && !picking && !adding && <p className="text-[14px] text-faint">No providers yet.</p>}
        </div>
      </section>
    </div>
  );
}
