// The Fleet tab: one panel per saved robot, with its live-data link and the Executive's view of it.
// Connection data (link state, address, domain, topics, stop confirmation) comes from live-data's
// backend; everything in a panel's Executive block comes from the Executive's world and is tagged SIM
// while that world is the fake fleet.
import React from "react";
import { stopUnconfirmed } from "../live/link.js";
import { HiveMark, PencilIcon, PlusIcon, TrashIcon } from "../shell/icons.jsx";
import { SimTag } from "../shell/SimTag.jsx";
import { execStatus } from "./execStatus.js";
import { ModelImage, PageFooter, PlayButton, SectionHead, StatusBadge, modelAlt, stageBg } from "./parts.jsx";
import { LINK_TONE, PRESET_KEYS, PRESET_ROBOTS, PRESET_SENSORS, displayStatus, linkState } from "./presets.jsx";

export function greeting(date = new Date()) {
  const hour = date.getHours();
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

const EXEC_DOT = { idle: "bg-live", executing: "bg-coral", unavailable: "bg-warn", missing: "bg-faint" };

export { SimTag };

export default function FleetView({ robots, links = {}, exec, backend, messages, onAdd, onOpen, onChat, onConnect, onDisconnect, onRemove, onEdit }) {
  const live = robots.filter((robot) => robot.status === "connected").length;
  const domains = robots.map((robot) => robot.domainId).filter(Number.isInteger);
  const low = Math.min(...domains);
  const high = Math.max(...domains);
  const domainRange = domains.length ? (low === high ? `${low}` : `${low}–${high}`) : "from 42";
  const backendDown = backend?.state === "offline";
  const noRos = backend?.state === "online" && backend.ros && backend.ros.found === false;

  return (
    <div>
      <section
        className="px-8 pb-8 pt-10"
        style={{ background: "radial-gradient(ellipse 60% 120% at 92% 0%, rgb(217 119 87 / 0.16), transparent 70%)" }}
      >
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="text-[15px] font-medium text-muted">{greeting()}</p>
            <h1 className="mt-2 font-display text-[clamp(36px,4vw,56px)] leading-[1.05] tracking-display">Your fleet</h1>
          </div>
          <dl className="flex flex-wrap gap-x-10 gap-y-3">
            {[
              ["Robots", robots.length],
              ["Live now", live],
              ["ROS domains", domainRange],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-[13px] font-medium text-muted">{label}</dt>
                <dd className="mt-1 font-display text-[30px] leading-none tracking-title tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <button className="btn btn-primary" onClick={() => onAdd("cameraRcCar")} type="button">
            <PlusIcon size={18} />
            Add a robot
          </button>
        </div>
        {(backendDown || noRos) && (
          <p className="mt-6 rounded-xl border border-warn/50 bg-warn/10 px-4 py-3 text-[14px] text-ink">
            {backendDown ? (
              <>
                The car backend is not answering, so Connect will fail. Start it with <code className="font-mono">npm run backend</code>.
              </>
            ) : (
              <>
                ROS 2 was not found where the car backend runs. Looked for <code className="font-mono">{backend.ros.setup}</code>.
              </>
            )}
          </p>
        )}
      </section>

      <div className="space-y-14 px-8">
        <section aria-label="Robots">
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr))]">
            {robots.map((robot) => (
              <FleetPanel
                exec={exec}
                key={robot.id}
                link={links[robot.id] ?? null}
                onConnect={onConnect}
                onDisconnect={onDisconnect}
                onEdit={onEdit}
                onOpen={onOpen}
                onRemove={onRemove}
                robot={robot}
              />
            ))}
            <AddCard onAdd={() => onAdd("cameraRcCar")} />
          </div>
        </section>

        <section>
          <SectionHead title="Start from a preset" />
          <div className="grid gap-4 sm:grid-cols-3">
            {PRESET_KEYS.map((key) => (
              <PresetCard key={key} onAdd={() => onAdd(key)} preset={key} />
            ))}
          </div>
        </section>

        <CommandCallout messages={messages} onOpen={onChat} />
      </div>

      <PageFooter />
    </div>
  );
}

/* One robot. The whole panel opens the robot page; its controls sit above that button. */
export function FleetPanel({ robot, link = null, exec, onOpen, onConnect, onDisconnect, onRemove, onEdit }) {
  const preset = PRESET_ROBOTS[robot.preset];
  const live = robot.status === "connected";
  const danger = stopUnconfirmed(robot, link);
  const state = linkState(robot);
  const found = robot.discovery;
  return (
    <article
      className={`group relative flex flex-col overflow-hidden rounded-2xl border bg-card transition-colors ${danger ? "border-danger" : "border-line hover:border-line-strong"}`}
    >
      <button aria-label={`Open ${robot.name}`} className="absolute inset-0 z-0 rounded-2xl" onClick={() => onOpen(robot)} type="button" />

      <div className="pointer-events-none relative flex gap-4 p-4">
        <span className="model relative block h-[84px] w-[112px] flex-none overflow-hidden rounded-xl" style={{ background: stageBg(0.18) }}>
          <ModelImage alt={modelAlt(robot.preset)} className="absolute inset-[4%] h-[92%] w-[92%]" preset={robot.preset} />
        </span>
        <div className="min-w-0 flex-1 pr-20">
          <h3 className={`truncate text-[17px] font-semibold ${live ? "text-live" : "text-ink"}`}>{robot.name}</h3>
          <p className="truncate text-[14px] text-muted">{preset.label}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <StatusBadge compact status={displayStatus(robot)} />
            <span className="font-mono text-[13px]" title="Link state from the car backend">
              <span className="text-faint">link </span>
              <span className={LINK_TONE[state]} data-testid="link-state">
                {state}
              </span>
            </span>
          </div>
          {danger && (
            <span
              className="mt-2 inline-block rounded-full bg-danger px-2.5 py-0.5 text-[13px] font-semibold text-panel"
              role="status"
              title="The car has not confirmed the last stop: it may still be moving"
            >
              Stop not confirmed
            </span>
          )}
        </div>
        <span className="pointer-events-auto absolute right-3 top-3 z-10 flex gap-1">
          <button
            aria-label={`Change address of ${robot.name}`}
            className="ctl reveal size-9 hover:bg-raised"
            onClick={() => onEdit(robot)}
            title="Change IP address or domain"
            type="button"
          >
            <PencilIcon size={17} />
          </button>
          <button
            aria-label={`Remove ${robot.name}`}
            className="ctl reveal size-9 hover:bg-raised hover:text-danger"
            onClick={() => onRemove(robot)}
            title={`Remove ${robot.name}`}
            type="button"
          >
            <TrashIcon size={17} />
          </button>
        </span>
        <PlayButton className="pointer-events-auto absolute bottom-4 right-4 z-10" onConnect={onConnect} onDisconnect={onDisconnect} robot={robot} size={44} />
      </div>

      <div className="pointer-events-none relative flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line px-4 py-3 font-mono text-[13px] text-faint">
        <span translate="no">{robot.host || "no address"}</span>
        <span>domain {robot.domainId}</span>
        <span title={found ? `Found at the last connect, ${new Date(found.at).toLocaleTimeString()}` : undefined}>
          {found ? `${plural(found.nodes.length, "node", "nodes")} · ${plural(found.topics.length, "topic", "topics")}` : "not connected yet"}
        </span>
        <span className="flex gap-1.5">
          {(PRESET_SENSORS[robot.preset] ?? []).map((sensor) => (
            <span className="rounded bg-raised px-1.5 font-sans text-muted" key={sensor}>
              {sensor}
            </span>
          ))}
        </span>
      </div>

      <ExecBlock status={execStatus(robot, exec)} />
    </article>
  );
}

function ExecBlock({ status }) {
  const { kind, label, reason, robotId, tag, battery, location, task } = status;
  const known = kind !== "missing" && robotId;
  return (
    <div
      aria-label={robotId ? `Executive status for ${robotId}` : "Executive status"}
      className="pointer-events-none relative mt-auto border-t border-line bg-panel/40 px-4 py-3"
      role="group"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-muted">
          Executive{robotId && <span className="font-mono text-faint"> · {robotId}</span>}
        </span>
        {known && tag && <SimTag label={tag} />}
      </div>
      <div className="mt-1.5 flex min-w-0 items-center gap-2">
        <span className={`size-2 flex-none rounded-full ${EXEC_DOT[kind]}`} />
        <span className={`truncate text-[14px] ${kind === "missing" ? "text-muted" : "font-mono"}`}>{label}</span>
        {reason && <span className="truncate text-[13px] text-faint">{reason}</span>}
      </div>
      {known && (
        <>
          <p className="mt-1 truncate font-mono text-[13px] text-faint" title={task?.line ?? undefined}>
            {task ? task.line || `${task.task_id} ${task.skill}` : "no current task"}
          </p>
          <div className="mt-2.5 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 text-[13px]">
            <div className="flex items-center gap-2">
              <span
                aria-label={tag === "EMULATED" ? "Battery (emulated)" : tag ? "Battery (simulated)" : "Battery"}
                aria-valuemax={100}
                aria-valuemin={0}
                aria-valuenow={battery == null ? undefined : Math.round(battery)}
                className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised"
                role="meter"
              >
                {battery != null && (
                  <span
                    className={`block h-full rounded-full ${battery < 20 ? "bg-danger" : battery < 40 ? "bg-warn" : "bg-live"}`}
                    style={{ width: `${Math.max(0, Math.min(100, battery))}%` }}
                  />
                )}
              </span>
              {battery == null ? (
                <span className="text-right text-muted">battery unknown</span>
              ) : (
                <span className="w-10 text-right font-mono tabular-nums text-muted">{`${Math.round(battery)}%`}</span>
              )}
            </div>
            <span className="truncate text-muted" title="Location in the Executive's world">
              {location ?? "location unknown"}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function AddCard({ onAdd }) {
  return (
    <button
      className="group grid min-h-[260px] place-items-center rounded-2xl border border-dashed border-line-strong text-center transition-colors hover:border-faint hover:bg-card/50"
      onClick={onAdd}
      type="button"
    >
      <span>
        <span className="mx-auto grid size-12 place-items-center rounded-full bg-raised text-ink transition-colors group-hover:bg-coral group-hover:text-coral-ink">
          <PlusIcon size={22} />
        </span>
        <span className="mt-3 block text-[16px] font-semibold">Add a robot</span>
        <span className="mt-1 block text-[14px] text-muted">Pick a preset and enter its IP address</span>
      </span>
    </button>
  );
}

/* A preset's card: the model it adds, and what it carries. */
function PresetCard({ preset, onAdd }) {
  const p = PRESET_ROBOTS[preset];
  return (
    <button
      aria-label={`Add ${p.label}`}
      className="group overflow-hidden rounded-2xl border border-line bg-card text-left transition-colors hover:border-line-strong"
      onClick={onAdd}
      type="button"
    >
      <span className="model relative block aspect-[16/11]" style={{ background: stageBg(0.18) }}>
        <ModelImage alt={modelAlt(preset)} className="absolute inset-[4%] h-[92%] w-[92%]" preset={preset} />
        <span className="play reveal absolute bottom-3 right-3 size-11" aria-hidden="true">
          <PlusIcon size={20} />
        </span>
      </span>
      <span className="block border-t border-line p-4">
        <span className="block text-[17px] font-semibold">{p.label}</span>
        <span className="mt-1 line-clamp-2 block text-[14px] leading-snug text-muted">{p.summary}</span>
      </span>
    </button>
  );
}

/* Claude's full-bleed coral callout, showing the real log rather than a picture of one. */
function CommandCallout({ messages, onOpen }) {
  const recent = messages.slice(-2);
  return (
    <section className="grid items-center gap-8 overflow-hidden rounded-2xl bg-coral p-7 text-coral-ink md:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)] md:p-10">
      <div>
        <h2 className="font-display text-[clamp(30px,3.4vw,46px)] leading-[1.05] tracking-display">A command log for the whole fleet</h2>
        <p className="mt-3 max-w-[42ch] text-[17px] font-medium leading-relaxed">Write an instruction once and Hive sends it to every connected robot.</p>
        <button className="btn mt-6 bg-sidebar text-ink hover:bg-raised" onClick={onOpen} type="button">
          Send a command
        </button>
      </div>
      <div className="rounded-xl bg-cream p-5 text-cream-ink shadow-paper">
        <p className="text-[13px] font-medium text-cream-muted">Latest in the log</p>
        <div className="mt-3 space-y-3">
          {recent.map((m, i) => (
            <div className={m.role === "operator" ? "flex justify-end" : "flex gap-2.5"} key={`${i}-${m.text}`}>
              {m.role === "agent" && <HiveMark className="mt-0.5 flex-none text-coral-press" size={18} />}
              <p
                className={
                  m.role === "operator"
                    ? "max-w-[85%] rounded-2xl rounded-br-md bg-cream-3 px-3.5 py-2 text-[15px]"
                    : "line-clamp-3 whitespace-pre-line text-[15px] leading-relaxed"
                }
              >
                {m.text}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
