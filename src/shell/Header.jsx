// The app header: mark, view tabs, mission pill, LIVE dot, theme, models and STOP ALL. It only draws
// what App hands it; STOP ALL's work happens in App (runStopAll), so this stays a dumb component.
import React, { useEffect, useRef, useState } from "react";
import { SimTag } from "./SimTag.jsx";
import { CloseIcon, GearIcon, HiveMark, MoonIcon, SunIcon } from "./icons.jsx";
import { missionPill } from "./status.js";
import { setTheme, useTheme } from "./theme.js";

const TABS = [
  ["fleet", "Fleet"],
  ["map", "Map"],
  ["chat", "Chat"],
];

/* The robot page is part of Fleet, so Fleet stays lit while it is open. */
const tabOf = (view) => (view === "robot" ? "fleet" : view);

const TONE = { live: "text-live", warn: "text-warn", danger: "text-danger", muted: "text-muted", faint: "text-faint" };

function useNow(active, everyMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [active, everyMs]);
  return now;
}

/* Closes a popover on Escape or a click outside it. */
function useDismiss(open, ref, onClose) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => event.key === "Escape" && onClose();
    const onDown = (event) => ref.current && !ref.current.contains(event.target) && onClose();
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open, ref, onClose]);
}

function MissionPill({ mission, sim, onOpen }) {
  const now = useNow(Boolean(mission));
  const text = missionPill(mission, now);
  if (!text) return null;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <button
        className="inline-flex min-h-9 min-w-0 items-center gap-2 rounded-full border border-coral/50 bg-coral/10 px-3.5 font-mono text-[13px] text-ink transition-colors hover:bg-coral/20"
        onClick={onOpen}
        title="Open the mission in Chat"
        type="button"
      >
        <span aria-hidden="true" className="size-2 flex-none animate-signal rounded-full bg-coral" />
        <span className="truncate" data-testid="mission-pill">
          {text}
        </span>
      </button>
      {sim && <SimTag />}
    </span>
  );
}

function LiveDot({ live }) {
  return (
    <span className="group relative">
      <span
        aria-label={`LIVE: ${live.text}`}
        className="inline-flex min-h-9 cursor-default items-center gap-2 rounded-full px-2.5 font-mono text-[13px] font-semibold tracking-wide outline-none focus-visible:ring-2 focus-visible:ring-coral"
        role="status"
        tabIndex={0}
        title={live.text}
      >
        <span aria-hidden="true" className={`size-2.5 rounded-full ${live.ok ? "bg-live" : "bg-warn"}`} data-testid="live-dot" />
        <span className={live.ok ? "text-live" : "text-warn"}>LIVE</span>
      </span>
      <span
        aria-hidden="true"
        className="pointer-events-none invisible absolute right-0 top-full z-30 mt-2 w-max max-w-[320px] rounded-xl border border-line bg-raised px-3.5 py-2.5 text-[13px] leading-snug text-ink opacity-0 shadow-float transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {live.ok ? (
          live.text
        ) : (
          <>
            <span className="block font-semibold text-warn">Not everything is up</span>
            {live.down.map((line) => (
              <span className="block" key={line}>
                {line}
              </span>
            ))}
          </>
        )}
      </span>
    </span>
  );
}

function ModelsMenu({ models }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const close = React.useCallback(() => setOpen(false), []);
  useDismiss(open, ref, close);
  const llm = models?.llm;
  return (
    <span className="relative" ref={ref}>
      <button
        aria-expanded={open}
        aria-label="Models"
        className="ctl size-10 hover:bg-raised"
        onClick={() => setOpen((o) => !o)}
        title="Models"
        type="button"
      >
        <GearIcon size={19} />
      </button>
      {open && (
        <div aria-label="Models" className="absolute right-0 top-full z-30 mt-2 w-[300px] rounded-xl border border-line bg-raised p-4 shadow-float" role="group">
          <p className="text-[15px] font-semibold">Models</p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[14px]">
            <dt className="text-faint">Default planner</dt>
            <dd className="font-mono">{models?.defaultPlanner ?? "—"}</dd>
            <dt className="text-faint">LLM</dt>
            <dd className="font-mono">{llm ? [llm.provider_label, llm.model].filter(Boolean).join(" · ") || "—" : "—"}</dd>
          </dl>
          <p className="mt-3 text-[13px] text-muted">Model settings arrive with mission chat.</p>
        </div>
      )}
    </span>
  );
}

function StopResults({ stop, onClose }) {
  return (
    <div
      aria-label="STOP ALL results"
      className="absolute right-4 top-full z-40 mt-2 w-[min(380px,calc(100vw-2rem))] rounded-xl border border-danger/50 bg-raised p-4 shadow-float"
      role="status"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[15px] font-semibold">STOP ALL</p>
          <p className="text-[12px] text-faint">software stop, not a hardware e-stop · {stop.at}</p>
        </div>
        <button aria-label="Close STOP ALL results" className="ctl size-8 hover:bg-hover" onClick={onClose} type="button">
          <CloseIcon size={16} />
        </button>
      </div>
      <ul className="mt-3 grid gap-1.5 text-[14px]">
        {stop.lines.map((line) => (
          <li className="flex items-baseline justify-between gap-3" key={line.key}>
            <span className="truncate font-medium">{line.label}</span>
            <span className={`text-right font-mono text-[13px] ${TONE[line.level] ?? "text-muted"}`} data-testid={`stop-line-${line.key}`}>
              {line.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* live: liveStatus(); mission: the active mission or null; sim: its data is from the fake fleet;
   models: { defaultPlanner, llm }; stop: { at, lines: [{ key, label, text, level }] } or null. */
export default function Header({ view, onNavigate, live, mission, sim, models, stop, onStopAll, onCloseStop }) {
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const current = tabOf(view);
  return (
    <header className="relative z-20 flex h-16 flex-none items-center gap-3 border-b border-line bg-sidebar px-4 sm:gap-5 sm:px-5">
      <span className="flex flex-none items-center gap-2">
        <HiveMark className="text-coral" size={26} />
        <span className="hidden font-display text-[22px] leading-none tracking-title sm:inline">HIVE</span>
      </span>

      <nav aria-label="Views" className="flex flex-none items-center gap-1">
        {TABS.map(([id, label]) => (
          <button
            aria-current={current === id ? "page" : undefined}
            className={`min-h-10 rounded-lg px-3 text-[15px] font-medium transition-colors ${
              current === id ? "bg-card text-ink" : "text-muted hover:bg-card hover:text-ink"
            }`}
            key={id}
            onClick={() => onNavigate(id)}
            type="button"
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="flex min-w-0 flex-1 justify-center">
        <MissionPill mission={mission} onOpen={() => onNavigate("chat")} sim={sim} />
      </div>

      <div className="flex flex-none items-center gap-1.5">
        <LiveDot live={live} />
        <button
          aria-label={`Switch to ${next} theme`}
          className="ctl size-10 hover:bg-raised"
          onClick={() => setTheme(next)}
          title={`Switch to ${next} theme`}
          type="button"
        >
          {theme === "dark" ? <SunIcon size={19} /> : <MoonIcon size={19} />}
        </button>
        <ModelsMenu models={models} />
        <button className="btn btn-danger ml-1.5 min-h-10 px-4 font-semibold tracking-wide" onClick={onStopAll} title="software stop, not a hardware e-stop" type="button">
          STOP ALL
        </button>
      </div>

      {stop && <StopResults onClose={onCloseStop} stop={stop} />}
    </header>
  );
}
