import React from "react";

export default function GenericPanel({ payload, topic }) {
  return (
    <div className="h-full overflow-auto p-4">
      {payload ? (
        <>
          {payload.note && <p className="mb-2 text-[13px] text-warn">{payload.note}</p>}
          <pre className="whitespace-pre-wrap break-words font-mono text-[13px] text-ink/90" translate="no">
            {JSON.stringify(payload.data, null, 2)}
          </pre>
        </>
      ) : (
        <p className="text-[14px] text-muted">Waiting for {topic?.name ?? "data"}…</p>
      )}
    </div>
  );
}
