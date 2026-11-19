// Polls the Executive for the shell: GET /state every 2 s and GET /health every 5 s. A poll starts
// only after the last one ended, so a slow Executive never piles requests up. Plain fetch, no stream:
// the car page's single EventSource stays the only one.
//
// pauseHidden: no polls while the browser tab is hidden; one runs at once when it is shown again.
// accept(state): takes the state a POST returned. A state poll that started before it is dropped
// when it lands, so an older snapshot can never paint over the answer to the operator's action.
import { useCallback, useEffect, useRef, useState } from "react";
import { execGet } from "./api.js";

const INITIAL = { reachable: null, health: null, state: null, error: null };

const isHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

export function useExecutive({ stateMs = 2000, healthMs = 5000, pauseHidden = false } = {}) {
  const [exec, setExec] = useState(INITIAL);
  const pollState = useRef(() => {});
  const generation = useRef(0);

  useEffect(() => {
    let alive = true;
    const timers = {};
    const loop = (name, path, period, key) => {
      let running = false;
      const tick = async () => {
        if (running) return; // a refresh() while a poll is in flight waits for that poll
        window.clearTimeout(timers[name]);
        if (pauseHidden && isHidden()) return; // parked until visibilitychange
        running = true;
        const gen = generation.current;
        const fresh = () => alive && (key !== "state" || gen === generation.current);
        try {
          const data = await execGet(path);
          if (fresh()) setExec((e) => ({ ...e, reachable: true, error: null, [key]: data }));
        } catch (error) {
          if (fresh()) setExec((e) => ({ ...e, reachable: false, error: error.message, [key]: null }));
        }
        running = false;
        if (alive) timers[name] = window.setTimeout(tick, period);
      };
      tick();
      return tick;
    };
    const ticks = [loop("state", "/state", stateMs, "state"), loop("health", "/health", healthMs, "health")];
    pollState.current = ticks[0];
    const onVisibility = () => {
      if (!isHidden()) ticks.forEach((tick) => tick());
    };
    if (pauseHidden) document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      Object.values(timers).forEach((t) => window.clearTimeout(t));
      if (pauseHidden) document.removeEventListener("visibilitychange", onVisibility);
      pollState.current = () => {};
    };
  }, [stateMs, healthMs, pauseHidden]);

  const refresh = useCallback(() => pollState.current(), []);
  const accept = useCallback((state) => {
    if (!state || typeof state !== "object") return;
    generation.current += 1;
    setExec((e) => ({ ...e, reachable: true, error: null, state }));
  }, []);
  return { ...exec, refresh, accept };
}
