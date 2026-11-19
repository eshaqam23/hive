// One provider as the server reports it: label, kind, endpoint, model, whether a key is stored (and
// its four-character hint), the roles that use it, the last Test result, and Test / Edit / Delete.
// Only whitelisted fields of the row are drawn; anything else the server might send is ignored.
import React, { useState } from "react";
import { latencyText } from "../chat/ui.jsx";
import { testSummary } from "./settingsModel.js";

const ROLE_NAME = { executive: "Executive", instance_agent: "Instance agent" };

export function TestResult({ result, id }) {
  const s = testSummary(result);
  if (!s) return null;
  return (
    <div
      aria-label={`Test result for ${id}`}
      className={`rounded-xl border px-4 py-3 text-[14px] ${s.ok ? "border-live/40 bg-live/8" : "border-danger/50 bg-danger/10"}`}
      role={s.ok ? "status" : "alert"}
    >
      <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`font-mono text-[13px] font-semibold uppercase tracking-[0.06em] ${s.ok ? "text-live" : "text-danger"}`}>{s.ok ? "ok" : "failed"}</span>
        {s.latencyMs !== null && <span className="font-mono text-[13px] text-muted">{latencyText(s.latencyMs)}</span>}
        {s.steps.map((step) => (
          <span className={`font-mono text-[12px] ${step.ok ? "text-faint" : "text-danger"}`} key={step.name}>
            {step.name} {step.ok ? "✓" : "✗"}
            {step.ms !== null && step.name !== "models" ? ` ${latencyText(step.ms)}` : ""}
          </span>
        ))}
        {s.ok && s.echo && s.echo !== s.model && <span className="font-mono text-[12px] text-faint">ran {s.echo}</span>}
      </p>
      {!s.ok && s.error && (
        <p className="mt-1.5 whitespace-pre-wrap break-words font-mono text-[13px] text-ink" data-testid={`test-error-${id}`}>
          {s.error}
        </p>
      )}
      {!s.ok && s.schemaErrors.length > 0 && (
        <ul className="mt-1.5 grid gap-0.5 font-mono text-[12px] text-muted">
          {s.schemaErrors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Meta({ label, children }) {
  return (
    <>
      <dt className="text-faint">{label}</dt>
      <dd className="min-w-0 break-all font-mono text-ink">{children}</dd>
    </>
  );
}

export default function ProviderCard({ provider: p, test, testing = false, busy = false, onTest, onEdit, onDelete }) {
  const [confirming, setConfirming] = useState(false);
  const readOnly = Boolean(p.read_only);
  return (
    <article aria-label={`Provider ${p.id}`} className="grid gap-3 rounded-2xl border border-line bg-card p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-[17px] font-semibold">{p.label || p.id}</h3>
          <p className="font-mono text-[13px] text-faint">{p.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {(p.roles || []).map((r) => (
            <span className="rounded-full border border-coral/50 px-2.5 py-0.5 text-[12px] font-medium text-coral" key={r}>
              {ROLE_NAME[r] || r}
            </span>
          ))}
          {readOnly && <span className="rounded-full border border-warn/50 px-2.5 py-0.5 text-[12px] font-medium text-warn">read-only · QWEN_* env</span>}
          <span className="rounded-full bg-raised px-2.5 py-0.5 font-mono text-[12px] text-muted">{p.kind}</span>
        </div>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[14px]">
        <Meta label="base_url">{p.base_url}</Meta>
        <Meta label="model">{p.model}</Meta>
        <dt className="text-faint">key</dt>
        <dd className="font-mono">
          {p.key_present ? (
            <span className="text-ink" title="only these characters ever reach this page">
              stored <span className="text-muted">{p.key_hint}</span>
            </span>
          ) : (
            <span className="text-faint">none</span>
          )}
          {p.key_env && <span className="ml-2 text-[12px] text-faint">via {p.key_env}</span>}
        </dd>
      </dl>

      {testing ? (
        <p className="shimmer-text text-[14px]" role="status">
          Testing {p.id}: models, ping, plan round trip…
        </p>
      ) : (
        <TestResult id={p.id} result={test} />
      )}

      <footer className="flex flex-wrap items-center gap-2 pt-1">
        <button className="btn btn-secondary min-h-10 px-4 text-[14px]" disabled={busy || testing} onClick={onTest} type="button">
          {testing ? "Testing…" : "Test"}
        </button>
        {!readOnly && (
          <button className="btn btn-secondary min-h-10 px-4 text-[14px]" disabled={busy} onClick={onEdit} type="button">
            Edit
          </button>
        )}
        <span className="flex-1" />
        {!readOnly &&
          (confirming ? (
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] text-muted">Delete {p.id} and its key?</span>
              <button
                className="btn btn-danger min-h-10 px-4 text-[14px]"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  onDelete();
                }}
                type="button"
              >
                Delete
              </button>
              <button className="btn btn-secondary min-h-10 px-4 text-[14px]" onClick={() => setConfirming(false)} type="button">
                Keep
              </button>
            </span>
          ) : (
            <button className="btn btn-secondary min-h-10 px-4 text-[14px] text-danger" disabled={busy} onClick={() => setConfirming(true)} type="button">
              Delete
            </button>
          ))}
      </footer>
    </article>
  );
}
