#!/usr/bin/env node
// Start Hive: the car backend, then (HIVE_GATEWAY=synapse, the default) the fake fleet and the
// bridge, then the Executive, then Vite. Each one's port must open before the next starts. One
// Ctrl+C stops all of them, the car backend first so it stops every robot.
// Flags: --no-executive, --no-bridge, --backend-only (see launchPlan in backend-command.mjs).
import { spawn } from "node:child_process";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { backendCommand, launchPlan, serviceCommand, withWslEnv, withWslPaths, wslSignals } from "./backend-command.mjs";
import { busyPorts, portInUseMessage, waitForPort } from "./ports.mjs";
import { pipePrefixed } from "./prefix.mjs";
import { shutdownAll } from "./shutdown.mjs";

const viteBin = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const repoDir = fileURLToPath(new URL("..", import.meta.url));
const isWin = process.platform === "win32";
const say = (msg) => console.log(`[hive] ${msg}`);

const plan = launchPlan({ argv: process.argv.slice(2), env: process.env });
if (plan.error) {
  console.error(`hive: ${plan.error}`);
  process.exit(2);
}

// Unbuffered so banners and logs appear at once through the pipes. HIVE_GATEWAY is set so the
// Executive gets the launcher's default. WSLENV carries these variables across into WSL on Windows,
// with paths such as HIVE_ROS_SETUP turned into WSL paths first.
const baseEnv = { ...process.env, PYTHONUNBUFFERED: "1", HIVE_GATEWAY: plan.gateway };
const env = isWin ? withWslPaths(baseEnv) : baseEnv;
if (isWin) env.WSLENV = withWslEnv(process.env.WSLENV, env);

const procs = {}; // name -> { name, step, child, pidfile, failed, closed }
const abort = new AbortController(); // ends the startup waits once stopping begins
let stopping = false;

// Windows: run a small command inside WSL (against a pidfile). SIGINT is what Ctrl+C gives each
// process on Ubuntu: the backend's handler stops every robot before it exits. A console Ctrl+C can
// kill wsl.exe without that happening, so the launcher does not rely on it. Resolves to the exit code.
function inWsl(script) {
  const distro = process.env.HIVE_WSL_DISTRO ? ["-d", process.env.HIVE_WSL_DISTRO] : [];
  const child = spawn("wsl.exe", [...distro, "--exec", "sh", "-c", script], { stdio: "ignore", windowsHide: true });
  return new Promise((resolve) => {
    child.on("error", () => resolve(-1));
    child.on("exit", (code) => resolve(code ?? -1));
  });
}

function commandFor(name, pidfile) {
  if (name === "backend") return backendCommand({ platform: process.platform, repoDir, env: process.env, pidfile });
  if (name === "vite") return { cmd: process.execPath, args: [viteBin, "--host", "127.0.0.1", "--open"] };
  return serviceCommand(name, { platform: process.platform, repoDir, env: process.env, pidfile, home: homedir(), nodePath: process.execPath });
}

function start(step) {
  const { name } = step;
  // Windows: each process records its pid in WSL so shutdown can signal it there.
  const pidfile = isWin && name !== "vite" ? `/tmp/hive-${name}-${process.pid}.pid` : undefined;
  const { cmd, args, cwd } = commandFor(name, pidfile);
  const child = spawn(cmd, args, {
    cwd,
    env,
    shell: false,
    // Linux services get their own process group, so the whole group can be stopped (like setsid in WSL).
    detached: !isWin && !["backend", "vite"].includes(name),
    // Vite keeps the terminal for its shortcuts; nothing else reads stdin.
    stdio: [name === "vite" ? "inherit" : "ignore", "pipe", "pipe"],
  });
  const proc = { name, step, child, pidfile, failed: false, closed: false };
  procs[name] = proc;
  pipePrefixed(child.stdout, `[${name}]`, process.stdout);
  pipePrefixed(child.stderr, `[${name}]`, process.stderr);
  child.on("close", () => (proc.closed = true));
  child.on("error", (err) => failedToStart(proc, cmd, err));
  child.on("exit", (code, signal) => exited(proc, code, signal));
}

function failedToStart(proc, cmd, err) {
  proc.failed = true;
  proc.closed = true;
  console.error(`Failed to start the ${proc.step.label} with "${cmd}": ${err.message}`);
  if (isWin) console.error("Hive's backend, fake fleet, bridge and Executive run in WSL on Windows. Check that WSL is installed (set HIVE_WSL_DISTRO to pick a distro).");
  else if (proc.name === "backend") console.error("Set HIVE_PYTHON to your Python executable and try again.");
  else if (proc.name === "exec") console.error("Set HIVE_EXEC_PYTHON to the hive-exec venv's python (default ~/.venvs/hive-exec/bin/python).");
  stopAll(1);
}

function exited(proc, code, signal) {
  if (stopping) return;
  // A console Ctrl+C reaches the children as well as the launcher. Give the launcher's own SIGINT a
  // moment to arrive so a child that died of it is not reported as a crash.
  setTimeout(() => {
    if (stopping) return;
    const expected = proc.name === "vite" || plan.steps.length === 1; // Vite quit, or --backend-only
    if (!expected || code) say(`the ${proc.step.label} exited (${code != null ? `code ${code}` : signal}); stopping everything`);
    stopAll(expected ? (code ?? 0) : code || 1);
  }, 500);
}

// How shutdownAll stops one process (see shutdown.mjs for the ask / wait / ask again / kill order).
function control(proc) {
  const { name, child, pidfile } = proc;
  const base = { name, child: proc.failed ? null : child, waitMs: name === "fleet" ? 6000 : undefined };
  if (isWin) {
    // Windows: SIGINT inside WSL, asked again once if unanswered; gone means gone inside WSL, since
    // wsl.exe can exit first. Last resort: end wsl.exe and SIGKILL inside WSL (the backend's workers
    // then stop their robots on their own).
    const sig = wslSignals(pidfile, { group: name !== "backend" });
    return {
      ...base,
      requestStop: () => inWsl(sig.stop),
      retryStop: () => inWsl(sig.retry),
      isGone: async () => (await inWsl(sig.alive)) !== 0,
      hardKill: async () => {
        child.kill("SIGTERM");
        await inWsl(sig.kill);
      },
    };
  }
  // Linux backend: SIGTERM (a terminal Ctrl+C has already reached it), then SIGKILL.
  if (name === "backend") return { ...base, requestStop: () => child.kill("SIGTERM") };
  // Linux services: SIGINT to the leader, then to its process group, then SIGKILL the group.
  const group = (sig) => {
    try {
      process.kill(-child.pid, sig);
      return true;
    } catch {
      return false;
    }
  };
  return {
    ...base,
    requestStop: () => child.kill("SIGINT"),
    retryStop: () => group("SIGINT"),
    isGone: async () => !group(0),
    hardKill: () => group("SIGKILL"),
  };
}

async function stopAll(code = 0) {
  if (stopping) return;
  stopping = true;
  abort.abort();
  if (Object.keys(procs).length) say("stopping: the car backend first (it stops every robot), then the rest");
  const services = ["exec", "bridge", "fleet"].filter((n) => procs[n]).map((n) => control(procs[n]));
  const outcomes = await shutdownAll({
    backend: procs.backend && control(procs.backend),
    services,
    frontend: procs.vite?.child,
    platform: process.platform,
  });
  for (const [name, outcome] of Object.entries(outcomes)) {
    if (outcome !== "exited") console.error(`[hive] ${procs[name].step.label} did not stop on its own (${outcome}).`);
  }
  // Let the last log lines (the backend's "stopping <robot>") come through the pipes, briefly.
  const deadline = Date.now() + 2000;
  while (Object.values(procs).some((p) => !p.closed) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 50));
  if (Object.keys(procs).length) say("stopped");
  process.exit(code);
}

function banner() {
  const has = (n) => plan.steps.some((s) => s.name === n);
  const parts = [];
  if (has("vite")) parts.push("UI http://127.0.0.1:5173");
  parts.push("car backend :8787");
  if (has("exec")) {
    const sim = plan.gateway === "fake" ? "in-process sim, SIM data" : has("fleet") ? "fake fleet on ROS_DOMAIN_ID 77, SIM data" : "your own bridge on :8790";
    parts.push(`Executive :8788/api/exec (gateway ${plan.gateway}: ${sim})`);
  }
  if (has("bridge")) parts.push("bridge :8790");
  if (has("fleet")) parts.push("rosbridge :9090");
  say(`ready · ${parts.join(" · ")} · Ctrl+C stops everything`);
}

async function main() {
  const busy = await busyPorts(plan.steps.map((s) => s.port));
  if (busy.length) {
    for (const port of busy) console.error(`hive: ${portInUseMessage(port, process.platform)}`);
    process.exit(1);
  }
  const gateway = plan.steps.some((s) => s.name === "exec") ? ` (HIVE_GATEWAY=${plan.gateway})` : "";
  say(`starting ${plan.steps.map((s) => s.name).join(", ")}${gateway}`);
  for (const step of plan.steps) {
    if (stopping) return;
    start(step);
    const r = await waitForPort(step.port, { timeoutMs: step.waitMs, signal: abort.signal });
    if (r === "aborted") return;
    if (r === "timeout") {
      console.error(`[hive] the ${step.label} did not open port ${step.port} within ${step.waitMs / 1000} s; stopping everything`);
      return stopAll(1);
    }
  }
  banner();
}

process.on("SIGINT", () => stopAll(0));
process.on("SIGTERM", () => stopAll(0));
process.on("SIGHUP", () => stopAll(0)); // console window closed (Windows)
main();
