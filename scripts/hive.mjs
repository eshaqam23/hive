#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const viteBin = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const backendScript = fileURLToPath(new URL("../backend/server.py", import.meta.url));
const rosSetup = process.env.HIVE_ROS_SETUP || "/opt/ros/jazzy/setup.bash";

function shellQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

const backend = spawn("bash", ["-lc", `source ${shellQuote(rosSetup)} && python3 ${shellQuote(backendScript)}`], {
  stdio: "inherit",
  shell: false,
});

const frontend = spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--open"], {
  stdio: "inherit",
  shell: false,
});

function stopChildren() {
  if (!backend.killed) backend.kill("SIGTERM");
  if (!frontend.killed) frontend.kill("SIGTERM");
}

backend.on("exit", (code) => {
  if (code && code !== 0) {
    stopChildren();
    process.exit(code);
  }
});

frontend.on("exit", (code) => {
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
