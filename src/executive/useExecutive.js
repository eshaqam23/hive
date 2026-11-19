// Polls the Executive for the shell: GET /state every 2 s and GET /health every 5 s. A poll starts
// only after the last one ended, so a slow Executive never piles requests up. Plain fetch, no stream:
// the car page's single EventSource stays the only one.
import { useCallback, useEffect, useRef, useState } from "react";
import { execGet } from "./api.js";

const INITIAL = { reachable: null, health: null, state: null, error: null };

export function useExecutive({ stateMs = 2000, healthMs = 5000 } = {}) {
  const [exec, setExec] = useState(INITIAL);
  const pollState = useRef(() => {});

  useEffect(() => {
    let alive = true;
    const timers = {};
    const loop = (name, path, period, key) => {
      let running = false;
      const tick = async () => {
        if (running) return; // a refresh() while a poll is in flight waits for that poll
        running = true;
        window.clearTimeout(timers[name]);
        try {
          const data = await execGet(path);
          if (alive) setExec((e) => ({ ...e, reachable: true, error: null, [key]: data }));
        } catch (error) {
          if (alive) setExec((e) => ({ ...e, reachable: false, error: error.message, [key]: null }));
        }
        running = false;
        if (alive) timers[name] = window.setTimeout(tick, period);
      };
      tick();
      return tick;
    };
    pollState.current = loop("state", "/state", stateMs, "state");
    loop("health", "/health", healthMs, "health");
    return () => {
      alive = false;
      Object.values(timers).forEach((t) => window.clearTimeout(t));
      pollState.current = () => {};
    };
  }, [stateMs, healthMs]);

  const refresh = useCallback(() => pollState.current(), []);
  return { ...exec, refresh };
}
