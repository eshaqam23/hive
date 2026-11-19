// Small shared pieces of the mission chat.
import React, { useEffect, useRef, useState } from "react";

export const TONE_TEXT = {
  faint: "text-faint",
  muted: "text-muted",
  coral: "text-coral",
  warn: "text-warn",
  live: "text-live",
  danger: "text-danger",
};

export const TONE_DOT = {
  faint: "bg-faint",
  muted: "bg-muted",
  coral: "bg-coral",
  warn: "bg-warn",
  live: "bg-live",
  danger: "bg-danger",
};

/* Executive wall-clock seconds -> local hh:mm:ss */
export function clock(t) {
  if (!Number.isFinite(t)) return "";
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function latencyText(ms) {
  if (!Number.isFinite(ms)) return null;
  return ms < 1000 ? `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone("Copied");
    } catch {
      setDone("Copy failed");
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDone(null), 1600);
  };
  return (
    <button className="rounded-md border border-line-strong px-2.5 py-1 text-[13px] font-medium text-ink hover:border-ink" onClick={copy} type="button">
      <span aria-live="polite">{done ?? label}</span>
    </button>
  );
}

/* the small disclosure triangle used by collapsible cards */
export function Caret({ open }) {
  return (
    <svg aria-hidden="true" className={`size-3.5 flex-none transition-transform duration-150 ${open ? "rotate-90" : ""}`} viewBox="0 0 16 16">
      <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

export function Eyebrow({ children, className = "" }) {
  return <p className={`font-mono text-[12px] uppercase tracking-[0.08em] text-faint ${className}`}>{children}</p>;
}
