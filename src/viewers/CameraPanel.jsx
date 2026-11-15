import React, { useEffect, useState } from "react";
import { cameraUrl } from "../live/api.js";
import { probeCamera } from "../live/probeCamera.js";

const FULL_LABEL = "Stream full size anyway (~27 MB/s, may disrupt driving)";

export const statsLine = (stats) =>
  stats
    ? [
        stats.hz != null ? `${Number(stats.hz).toFixed(1)} fps` : null,
        stats.bytes_per_s != null ? `${(stats.bytes_per_s / 1e6).toFixed(2)} MB/s from car` : null,
        stats.width && stats.height ? `${stats.width}×${stats.height}` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

/* Probe the MJPEG URL first (R20): the backend answers 409 camera_untuned when the camera is not yet at
   a safe size. On 200 the probe is aborted and the <img> takes over. fps, MB/s, size and the Stale
   overlay come from the page's single SSE stream (image_stats and status events). */
export default function CameraPanel({ base, robot, topic, stale, stats, onForceFull }) {
  const [forceFull, setForceFull] = useState(false);
  const [phase, setPhase] = useState({ state: "probing" }); // probing | ready | blocked | error
  const [attempt, setAttempt] = useState(0);
  const url = cameraUrl(base, robot, topic.name, { forceFull });

  useEffect(() => {
    const controller = new AbortController();
    setPhase({ state: "probing" });
    probeCamera(fetch, url, { signal: controller.signal, abort: () => controller.abort() }).then((result) => {
      if (result.state !== "aborted") setPhase(result);
    });
    return () => controller.abort();
  }, [url, attempt]);

  const full = () => {
    setForceFull(true);
    onForceFull?.();
  };

  if (phase.state === "blocked") {
    return (
      <div className="grid h-full place-items-center p-6 text-center">
        <div className="max-w-md">
          <p className="text-[15px] text-ink">{phase.reason}</p>
          <button className="btn btn-secondary mt-4 px-4" onClick={full} type="button">
            {FULL_LABEL}
          </button>
        </div>
      </div>
    );
  }
  if (phase.state === "error") {
    return (
      <div className="grid h-full place-items-center p-6 text-center">
        <div>
          <p className="text-[14px] text-danger">{phase.reason}</p>
          <button className="btn btn-secondary mt-4 px-4" onClick={() => setAttempt((n) => n + 1)} type="button">
            Retry
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="relative h-full w-full">
      {phase.state === "ready" && (
        <img
          alt={`Live camera ${topic.name}`}
          className="h-full w-full object-contain"
          key={attempt}
          onError={() => setPhase({ state: "error", reason: "The camera stream stopped." })}
          src={url}
        />
      )}
      {phase.state === "probing" && <p className="absolute inset-0 grid place-items-center text-[14px] text-muted">Starting camera…</p>}
      {stats && <p className="pointer-events-none absolute left-3 top-2 font-mono text-[12px] text-faint">{statsLine(stats)}</p>}
      {stale && <div className="absolute inset-0 grid place-items-center bg-black/50 text-[15px] font-semibold text-warn">Stale</div>}
    </div>
  );
}
