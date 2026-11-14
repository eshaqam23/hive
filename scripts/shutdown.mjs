// Shut Hive down so the backend always gets to stop the robots before anything is killed.
//
// The backend's own SIGINT/SIGTERM handler stops every robot it drives, so the backend is always asked
// to stop and given time to do it before it is killed. On Windows the backend runs in WSL, detached
// from the launcher's console (a console Ctrl+C would otherwise kill wsl.exe at once, hanging up the
// backend before its handler runs), so the launcher delivers SIGINT inside WSL itself.

const DEFAULT_WAIT_MS = 3000;

function hasExited(child) {
  return !child || (child.exitCode !== null && child.exitCode !== undefined) || child.signalCode != null;
}

// Resolves true when the child has exited (and, if given, isGone() says the real backend is gone too),
// false after ms. Polls, because on Windows wsl.exe can exit before the backend inside WSL has.
export function waitForExit(child, ms, { isGone, setTimer = setTimeout, now = Date.now, pollMs = 100 } = {}) {
  const deadline = now() + ms;
  return new Promise((resolve) => {
    const tick = async () => {
      let done = hasExited(child);
      if (done && isGone) {
        try {
          done = await isGone();
        } catch {
          done = false;
        }
      }
      if (done) return resolve(true);
      if (now() >= deadline) return resolve(false);
      setTimer(tick, pollMs);
    };
    tick();
  });
}

// backend, frontend: child processes (frontend may be null).
// requestStop(): ask the backend to stop gracefully (Windows: SIGINT inside WSL, Linux: SIGTERM). May
//   return a promise; failures are ignored.
// isGone(): optional, resolves true once the real backend process is gone (Windows: checked inside
//   WSL, since wsl.exe may exit first).
// retryStop(): optional second request if the first got no answer within waitMs (Windows).
// Order: ask, wait, (ask again, wait), hard-kill the backend, then stop the frontend.
// Returns "exited" (first wait), "retried" (second wait) or "killed".
export async function shutdown({ backend, frontend, platform, requestStop, retryStop, isGone, waitMs = DEFAULT_WAIT_MS, timers }) {
  let outcome = "exited";
  // Fire and forget: a request that hangs (wsl.exe stuck) must not stop the wait and the kill below.
  const attempt = (fn) => {
    try {
      Promise.resolve(fn?.()).catch(() => {});
    } catch {
      // ignore: the wait, then the hard kill, still follow
    }
  };
  attempt(requestStop);
  if (!(await waitForExit(backend, waitMs, { isGone, ...timers }))) {
    outcome = "killed";
    if (retryStop) {
      attempt(retryStop);
      if (await waitForExit(backend, waitMs, { isGone, ...timers })) outcome = "retried";
    }
    if (outcome === "killed") backend.kill(platform === "win32" ? "SIGTERM" : "SIGKILL");
  }
  if (frontend && !hasExited(frontend)) frontend.kill("SIGTERM");
  return outcome;
}
