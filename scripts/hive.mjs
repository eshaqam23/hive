#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const viteBin = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const backendScript = fileURLToPath(new URL("../backend/server.py", import.meta.url));

// On Windows, "python3" usually resolves to the Microsoft Store stub, so use "python" there.
// Override with HIVE_PYTHON if needed.
const python = process.env.HIVE_PYTHON || (process.platform === "win32" ? "python" : "python3");
const backendOnly = process.argv.includes("--backend-only");

const backend = spawn(python, [backendScript], {
  stdio: "inherit",
  shell: false,
});

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
  console.error(`Failed to start backend with "${python}": ${err.message}`);
  console.error("Set HIVE_PYTHON to your Python executable and try again.");
  stopChildren();
  process.exit(1);
});

if (backendOnly) {
  backend.on("exit", (code) => process.exit(code ?? 0));
}

backend.on("exit", (code) => {
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
