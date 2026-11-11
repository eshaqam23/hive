#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const viteBin = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const backendScript = fileURLToPath(new URL("../backend/server.py", import.meta.url));

// On Windows, "python3" usually resolves to the Microsoft Store stub, so use "python" there.
// Override with HIVE_PYTHON if needed.
const python = process.env.HIVE_PYTHON || (process.platform === "win32" ? "python" : "python3");
const backendOnly = process.argv.includes("--backend-only");
const noExecutive = process.argv.includes("--no-executive");

// Executive service (FastAPI). Prefer the backend virtualenv created from backend/requirements.txt.
const backendDir = fileURLToPath(new URL("../backend/", import.meta.url));
const venvPython = fileURLToPath(
  new URL(process.platform === "win32" ? "../backend/.venv/Scripts/python.exe" : "../backend/.venv/bin/python", import.meta.url),
);
const execPython = process.env.HIVE_EXEC_PYTHON || (existsSync(venvPython) ? venvPython : python);

const backend = spawn(python, [backendScript], {
  stdio: "inherit",
  shell: false,
});

const executive = noExecutive
  ? null
  : spawn(execPython, ["-m", "hive_exec"], { cwd: backendDir, stdio: "inherit", shell: false });

executive?.on("error", (err) => {
  console.error(`Failed to start the Executive with "${execPython}": ${err.message}`);
  console.error("Create backend/.venv (see README) or set HIVE_EXEC_PYTHON.");
});

executive?.on("exit", (code) => {
  if (code) console.error(`Executive service exited with code ${code}; manual control keeps running.`);
});

const frontend = backendOnly
  ? null
  : spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--open"], {
      stdio: "inherit",
      shell: false,
    });

function stopChildren() {
  if (!backend.killed) backend.kill("SIGTERM");
  if (executive && !executive.killed) executive.kill("SIGTERM");
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
