// The app header: mark, view tabs, mission pill, LIVE dot, theme, models and STOP ALL. It only draws
// what App hands it; STOP ALL's work happens in App (runStopAll), so this stays a dumb component.
import React, { useEffect, useState } from "react";
import { SimTag } from "./SimTag.jsx";
import { CloseIcon, GearIcon, HiveMark, MoonIcon, SunIcon } from "./icons.jsx";
import { missionPill } from "./status.js";
import { setTheme, useTheme } from "./theme.js";

/* Mission (the chat view, where missions are sent) comes first: it is the app's main page. */
const TABS = [
  ["chat", "Mission"],
  ["fleet", "Fleet"],
  ["map", "Map"],
];

/* The robot page is part of Fleet, so Fleet stays lit while it is open. */
/* the robot page and the fleet command log both sit under Fleet */
const tabOf = (view) => (view === "robot" || view === "log" ? "fleet" : view);

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

function MissionPill({ mission, sim, onOpen }) {
  const now = useNow(Boolean(mission));
  const text = missionPill(mission, now);
  if (!text) return null;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <button
        className="inline-flex min-h-9 min-w-0 items-center gap-2 rounded-full border border-coral/50 bg-coral/10 px-3.5 font-mono text-[13px] text-ink transition-colors hover:bg-coral/20"
        onClick={onOpen}
        title="Open the Mission tab"
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

/* The gear opens the Models page; its tooltip names what the Executive plans with right now. */
function ModelsButton({ models, active, onOpen }) {
  const llm = models?.llm;
  const bound = llm && [llm.provider_label, llm.model].filter(Boolean).join(" · ");
  const title = `Models · planner ${models?.defaultPlanner ?? "—"} · LLM ${bound || "not bound"}`;
  return (
    <button
      aria-current={active ? "page" : undefined}
      aria-label="Models"
      className={`ctl size-10 ${active ? "bg-card text-ink" : "hover:bg-raised"}`}
      onClick={onOpen}
      title={title}
      type="button"
    >
      <GearIcon size={19} />
    </button>
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
      <button
        aria-label="HIVE: open the Mission tab"
        className="flex flex-none items-center gap-2 rounded-lg"
        onClick={() => onNavigate("chat")}
        type="button"
      >
        <HiveMark className="text-coral" size={26} />
        <span className="hidden font-display text-[22px] leading-none tracking-title sm:inline">HIVE</span>
      </button>

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
        <ModelsButton active={view === "models"} models={models} onOpen={() => onNavigate("models")} />
        <button className="btn btn-danger ml-1.5 min-h-10 px-4 font-semibold tracking-wide" onClick={onStopAll} title="software stop, not a hardware e-stop" type="button">
          STOP ALL
        </button>
      </div>

      {stop && <StopResults onClose={onCloseStop} stop={stop} />}
    </header>
  );
}
