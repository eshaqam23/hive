import React, { useEffect, useState } from "react";
import { cameraUrl } from "../live/api.js";

const FULL_LABEL = "Stream full size anyway (~27 MB/s, may disrupt driving)";

/* Probe the MJPEG URL first: the backend answers 409 camera_untuned when the camera is not yet at a
   safe size, and an <img> cannot read that reason. On 200 the probe is aborted and the <img> takes over. */
export default function CameraPanel({ base, robot, topic, stale, onForceFull }) {
  const [forceFull, setForceFull] = useState(false);
  const [phase, setPhase] = useState({ state: "probing" }); // probing | ready | blocked | error
  const [attempt, setAttempt] = useState(0);
  const url = cameraUrl(base, robot, topic.name, { forceFull });

  useEffect(() => {
    const controller = new AbortController();
    setPhase({ state: "probing" });
    fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (controller.signal.aborted) return;
        if (response.status === 409) {
          const body = await response.json().catch(() => ({}));
          setPhase({ state: "blocked", reason: body.reason || body.error || "The camera is not tuned for streaming yet." });
        } else if (response.ok) {
          controller.abort();
          setPhase({ state: "ready" });
        } else {
          const body = await response.json().catch(() => ({}));
          setPhase({ state: "error", reason: body.error || `Camera request failed (${response.status}).` });
        }
      })
      .catch((error) => {
        if (error?.name !== "AbortError") setPhase({ state: "error", reason: "Could not reach the camera stream." });
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
      {stale && <div className="absolute inset-0 grid place-items-center bg-black/50 text-[15px] font-semibold text-warn">Stale</div>}
    </div>
  );
}
