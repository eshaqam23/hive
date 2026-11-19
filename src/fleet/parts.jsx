// CAD thumbnails, status badge, connect button and page pieces shared by the Fleet tab and the robot
// page (moved from App.jsx).
import React, { useEffect, useState } from "react";
import { PRESET_MODELS, snapshot } from "../robotModels.js";
import { PauseIcon, PlayIcon, SignalBars, Spinner } from "../shell/icons.jsx";
import { STATUS } from "./presets.jsx";

/* A snapshot of the preset's real CAD model, drawn by the one shared offscreen renderer
   (robotModels.js). 720x480, so it sits in every frame without layout shift. */
export function useSnapshot(preset) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let alive = true;
    setSrc(null);
    snapshot(preset).then((url) => alive && setSrc(url)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [preset]);
  return src;
}

export function ModelImage({ preset, alt = "", className = "" }) {
  const src = useSnapshot(preset);
  if (!src) {
    return <span aria-hidden="true" className={`block animate-pulse rounded-lg bg-raised/40 ${className}`} />;
  }
  return (
    <img
      alt={alt}
      className={`animate-fade object-contain ${className}`}
      decoding="async"
      draggable={false}
      height={480}
      src={src}
      width={720}
    />
  );
}

export function ModelThumb({ preset, size = 48, className = "" }) {
  return (
    <span
      className={`grid flex-none place-items-center overflow-hidden rounded-md ${className}`}
      style={{ height: size, width: size, background: stageBg(0.12, "var(--color-raised)") }}
    >
      <ModelImage className="h-full w-full scale-125" preset={preset} />
    </span>
  );
}

/* the floor light behind a model: a faint coral glow on the card */
export const stageBg = (alpha = 0.16, base = "var(--color-card)") =>
  `radial-gradient(ellipse 70% 55% at 50% 66%, rgb(217 119 87 / ${alpha}), transparent 72%), ${base}`;

export const modelAlt = (preset) => `CAD model: ${PRESET_MODELS[preset]?.label}`;

/* ================================================================== shared pieces */

export function StatusBadge({ status, compact = false }) {
  const s = STATUS[status] || STATUS.offline;
  const label = compact && status === "failed" ? "Failed" : s.label;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[14px] font-semibold ${s.text}`}>
      {status === "connected" ? <SignalBars /> : <span className={`size-2 flex-none rounded-full ${s.dot}`} />}
      {label}
    </span>
  );
}

/* Spotify's play/pause, meaning connect/disconnect. While Hive keeps trying to connect, pressing it
   stops trying. */
export function PlayButton({ robot, onConnect, onDisconnect, size = 48, className = "" }) {
  const connecting = robot.status === "connecting";
  const connected = robot.status === "connected";
  const iconSize = Math.round(size * 0.42);
  const label = connecting
    ? `Stop ${robot.reconnecting ? "reconnecting" : "connecting"} to ${robot.name}`
    : connected
      ? `Disconnect ${robot.name}`
      : `Connect ${robot.name}`;
  return (
    <button
      aria-label={label}
      className={`play ${className}`}
      onClick={(event) => {
        event.stopPropagation();
        connected || connecting ? onDisconnect(robot) : onConnect(robot);
      }}
      style={{ height: size, width: size }}
      title={label}
      type="button"
    >
      {connecting ? (
        <Spinner size={iconSize} />
      ) : connected ? (
        <PauseIcon size={iconSize} />
      ) : (
        <PlayIcon size={iconSize} style={{ marginLeft: size * 0.04 }} />
      )}
    </button>
  );
}

export function SectionHead({ title, children }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <h2 className="font-display text-[28px] leading-tight tracking-title">{title}</h2>
      {children}
    </div>
  );
}

export function PageFooter() {
  return (
    <footer className="mx-8 mt-10 border-t border-line py-8 text-[13px] text-faint">
      <p>
        Hive drives ROS 2 Jazzy robots over your local network. CAD: MR-Buggy3 by Rudis Laboratories (CC&nbsp;BY&nbsp;4.0, via Gazebo Fuel); Intel RealSense D435, ROBOTIS TurtleBot3 LDS-01 and OpenMANIPULATOR-X meshes (Apache&nbsp;2.0). Details in /models/CREDITS.md.
      </p>
    </footer>
  );
}
