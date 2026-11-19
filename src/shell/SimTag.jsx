import React from "react";

/* Marks data that comes from the fake world or fake fleet, never from the real car. */
export function SimTag({ className = "" }) {
  return (
    <span
      className={`inline-flex flex-none items-center rounded border border-warn/60 px-1.5 font-mono text-[13px] font-semibold leading-5 tracking-wide text-warn ${className}`}
      title="Simulated by the fake fleet, not the real car"
    >
      SIM
    </span>
  );
}
