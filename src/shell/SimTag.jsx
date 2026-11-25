import React from "react";

const TITLES = {
  SIM: "Simulated by the fake fleet, not the real car",
  EMULATED: "Emulated by the hive-agent fleet (its report says evidence emulated), not measured on the real car",
};

/* Marks data that comes from the fake world or fake fleet (SIM) or an emulated agent fleet (EMULATED),
   never from the real car. */
export function SimTag({ label = "SIM", className = "", ...rest }) {
  const text = label === "EMULATED" ? "EMULATED" : "SIM";
  return (
    <span
      className={`inline-flex flex-none items-center rounded border border-warn/60 px-1.5 font-mono text-[13px] font-semibold leading-5 tracking-wide text-warn ${className}`}
      title={TITLES[text]}
      {...rest}
    >
      {text}
    </span>
  );
}
