#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { backendCommand } from "./backend-command.mjs";
import { shutdown } from "./shutdown.mjs";

const viteBin = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const repoDir = fileURLToPath(new URL("..", import.meta.url));
const backendOnly = process.argv.includes("--backend-only");
const isWin = process.platform === "win32";

// Windows: the backend records its pid in WSL so shutdown can send it SIGINT there.
const pidfile = isWin ? `/tmp/hive-backend-${process.pid}.pid` : undefined;

// Ubuntu: python3 backend/server.py. Windows: the backend runs inside WSL (see backend-command.mjs).
const { cmd, args } = backendCommand({ platform: process.platform, repoDir, env: process.env, pidfile });

// Unbuffered so the backend banner and logs appear at once even when output is piped. WSLENV carries
// the variable across into WSL on Windows.
const env = { ...process.env, PYTHONUNBUFFERED: "1" };
if (isWin) env.WSLENV = [env.WSLENV, "PYTHONUNBUFFERED"].filter(Boolean).join(":");

const backend = spawn(cmd, args, { stdio: "inherit", shell: false, env });

const frontend = backendOnly
  ? null
  : spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--open"], {
      stdio: "inherit",
      shell: false,
    });

// Windows: run a small command inside WSL against the backend's pid. SIGINT is what Ctrl+C gives the
// backend on Ubuntu: its handler stops every robot before it exits. A console Ctrl+C can kill wsl.exe
// without that happening, so the launcher does not rely on it. Resolves to the exit code.
function inWsl(script) {
  const distro = process.env.HIVE_WSL_DISTRO ? ["-d", process.env.HIVE_WSL_DISTRO] : [];
  const child = spawn("wsl.exe", [...distro, "--exec", "sh", "-c", script], { stdio: "ignore", windowsHide: true });
  return new Promise((resolve) => {
    child.on("error", () => resolve(-1));
    child.on("exit", (code) => resolve(code ?? -1));
  });
}
const sendIntInWsl = () => inWsl(`kill -INT $(cat ${pidfile})`);
const backendGoneInWsl = async () => (await inWsl(`kill -0 $(cat ${pidfile}) 2>/dev/null`)) !== 0;

let stopping = false;
async function stopAll() {
  if (stopping) return;
  stopping = true;
  const outcome = await shutdown({
    backend,
    frontend,
    platform: process.platform,
    // Windows: SIGINT inside WSL, asked again once if unanswered. Linux: SIGTERM (a terminal Ctrl+C
    // has already reached the backend).
    requestStop: isWin ? sendIntInWsl : () => backend.kill("SIGTERM"),
    retryStop: isWin ? sendIntInWsl : undefined,
    isGone: isWin ? backendGoneInWsl : undefined,
  });
  if (outcome === "killed" && isWin) await inWsl(`kill -KILL $(cat ${pidfile})`); // last resort; its workers stop on their own
  if (outcome !== "exited") console.error(`Backend did not stop on its own (${outcome}).`);
  process.exit(0);
}

backend.on("error", (err) => {
  console.error(`Failed to start the backend with "${cmd}": ${err.message}`);
  if (isWin) console.error("Hive's backend runs in WSL on Windows. Check that WSL and python3 are installed (set HIVE_WSL_DISTRO to pick a distro).");
  else console.error("Set HIVE_PYTHON to your Python executable and try again.");
  frontend?.kill("SIGTERM");
  process.exit(1);
});

backend.on("exit", (code) => {
  if (stopping) return;
  if (backendOnly) process.exit(code ?? 0);
  if (code && code !== 0) {
    frontend?.kill("SIGTERM");
    process.exit(code);
  }
});

frontend?.on("exit", (code) => {
  if (stopping) return;
  stopAll().then(() => process.exit(code ?? 0));
});

process.on("SIGINT", stopAll);
process.on("SIGTERM", stopAll);
process.on("SIGHUP", stopAll); // console window closed (Windows)
