// Port checks for the launcher: refuse to start next to a leftover process, and wait for each
// process to open its port before starting the next one.
import net from "node:net";

// true when something accepts a TCP connection on host:port. A connect that neither connects nor
// fails (WSL's mirrored networking can drop a connect to a closed port) counts as closed after
// timeoutMs.
export function probePort(port, { host = "127.0.0.1", timeoutMs = 500, connect = net.connect } = {}) {
  return new Promise((resolve) => {
    let done = false;
    let sock;
    const finish = (open) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      sock?.destroy();
      resolve(open);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    try {
      sock = connect({ host, port });
    } catch {
      return finish(false);
    }
    sock.once("connect", () => finish(true));
    sock.once("error", () => finish(false));
  });
}

// Resolves "open", "timeout" (nothing listened within timeoutMs) or "aborted" (signal fired).
export function waitForPort(port, { timeoutMs, intervalMs = 250, signal, probe = probePort } = {}) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    let timer;
    const onAbort = () => {
      clearTimeout(timer);
      resolve("aborted");
    };
    if (signal?.aborted) return resolve("aborted");
    signal?.addEventListener("abort", onAbort, { once: true });
    const settle = (result) => {
      signal?.removeEventListener("abort", onAbort);
      resolve(result);
    };
    const tick = async () => {
      const open = await probe(port, { timeoutMs: Math.min(500, intervalMs * 4) });
      if (signal?.aborted) return; // onAbort already resolved
      if (open) return settle("open");
      if (Date.now() >= deadline) return settle("timeout");
      timer = setTimeout(tick, intervalMs);
    };
    tick();
  });
}

// The ports in the list that something already listens on.
export async function busyPorts(ports, { probe = probePort } = {}) {
  const open = await Promise.all(ports.map((p) => probe(p)));
  return ports.filter((_, i) => open[i]);
}

// What owns each port, and the command that stops a leftover copy of it.
const OWNERS = {
  8787: { what: "the car backend", pkill: "backend/server.py", note: "its workers stop their robots as they exit" },
  8788: { what: "the Executive", pkill: "hive_exec" },
  8790: { what: "the bridge", pkill: "bridge/server.mjs" },
  9090: { what: "rosbridge (the fake fleet)", pkill: "fake_fleet.launch.py" },
};

export function portInUseMessage(port, platform) {
  if (port === 5173) {
    return "Port 5173 is already in use: another Vite dev server is running. Stop it first (the backends only accept this origin, so Vite will not move to another port).";
  }
  const o = OWNERS[port];
  if (!o) return `Port ${port} is already in use.`;
  const cmd = `${platform === "win32" ? "wsl " : ""}pkill -f ${o.pkill}`;
  return `Port ${port} is already in use: ${o.what} is probably still running from an earlier start. Stop it with \`${cmd}\`${o.note ? `; ${o.note}` : ""}.`;
}
