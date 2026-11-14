#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { backendCommand } from "./backend-command.mjs";

const viteBin = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const repoDir = fileURLToPath(new URL("..", import.meta.url));
const backendOnly = process.argv.includes("--backend-only");

// Ubuntu: python3 backend/server.py. Windows: the backend runs inside WSL (see backend-command.mjs).
const { cmd, args } = backendCommand({ platform: process.platform, repoDir, env: process.env });

// Unbuffered so the backend banner and logs appear at once even when output is piped. WSLENV carries
// the variable across into WSL on Windows.
const env = { ...process.env, PYTHONUNBUFFERED: "1" };
if (process.platform === "win32") env.WSLENV = [env.WSLENV, "PYTHONUNBUFFERED"].filter(Boolean).join(":");

const backend = spawn(cmd, args, { stdio: "inherit", shell: false, env });

const frontend = backendOnly
  ? null
  : spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--open"], {
      stdio: "inherit",
      shell: false,
    });

function stopChildren() {
  if (!backend.killed) backend.kill("SIGTERM");
  if (frontend && !frontend.killed) frontend.kill("SIGTERM");
}

backend.on("error", (err) => {
  console.error(`Failed to start the backend with "${cmd}": ${err.message}`);
  if (process.platform === "win32") console.error("Hive's backend runs in WSL on Windows. Check that WSL and python3 are installed (set HIVE_WSL_DISTRO to pick a distro).");
  else console.error("Set HIVE_PYTHON to your Python executable and try again.");
  stopChildren();
  process.exit(1);
});

backend.on("exit", (code) => {
  if (backendOnly) process.exit(code ?? 0);
  if (code && code !== 0) {
    stopChildren();
    process.exit(code);
  }
});

frontend?.on("exit", (code) => {
  stopChildren();
  process.exit(code ?? 0);
});

process.on("SIGINT", () => {
  stopChildren();
  process.exit(0);
});

process.on("SIGTERM", () => {
  stopChildren();
  process.exit(0);
});
