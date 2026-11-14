import React from "react";

export const compactValue = (data) => {
  if (data === null || data === undefined) return "";
  if (typeof data === "object") {
    const entries = Object.entries(data);
    if (entries.length === 1) return compactValue(entries[0][1]);
    return JSON.stringify(data);
  }
  return String(data);
};

export default function ValuePanel({ payload, stale }) {
  const text = compactValue(payload?.data);
  return (
    <div className="grid h-full place-items-center p-6 text-center">
      <p className={`max-w-full break-words font-mono text-[clamp(20px,3vw,40px)] ${stale ? "text-faint" : "text-ink"}`} translate="no">
        {payload ? text || "(empty)" : "Waiting for data…"}
      </p>
    </div>
  );
}
