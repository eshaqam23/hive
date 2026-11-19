// How to start the Hive backend on this machine.
// Linux: python3 backend/server.py. Windows: rclpy only exists in WSL, so run it there.

export function toWslPath(winPath) {
  const p = winPath.replace(/\\/g, "/").replace(/\/+$/, "");
  const m = /^([A-Za-z]):(\/.*)?$/.exec(p);
  if (!m) return p;
  return `/mnt/${m[1].toLowerCase()}${m[2] ?? ""}`;
}

// Shell script run inside WSL when the launcher must be able to stop the backend itself.
// A console Ctrl+C on Windows can kill wsl.exe, which hangs up whatever is attached to it. So the
// backend ignores SIGHUP, writes to a log file (not a pipe that dies with wsl.exe, which would make its
// shutdown print fail), and its pid goes in a pidfile. The log is shown with tail, which exits with the
// backend. The launcher then sends the backend SIGINT: its shutdown handler stops every robot.
function supervisedScript(pidfile) {
  const log = `${pidfile}.log`;
  return [
    "trap '' HUP",
    `: > ${log}`,
    `python3 backend/server.py > ${log} 2>&1 &`,
    "PY=$!",
    `echo $PY > ${pidfile}`,
    `tail -n +1 -f --pid=$PY ${log} &`,
    "TL=$!",
    "wait $PY",
    "C=$?",
    "wait $TL",
    `rm -f ${pidfile} ${log}`,
    "exit $C",
  ].join("\n");
}

// pidfile (Windows only): a WSL path where the backend records its pid, so the launcher can send it
// SIGINT inside WSL (see shutdown.mjs).
export function backendCommand({ platform, repoDir, env, pidfile }) {
  if (platform === "win32") {
    const distro = env.HIVE_WSL_DISTRO ? ["-d", env.HIVE_WSL_DISTRO] : [];
    const run = pidfile ? ["bash", "-c", supervisedScript(pidfile)] : ["python3", "backend/server.py"];
    return { cmd: "wsl.exe", args: [...distro, "--cd", toWslPath(repoDir), "--exec", ...run] };
  }
  return { cmd: env.HIVE_PYTHON || "python3", args: [`${repoDir.replace(/\/+$/, "")}/backend/server.py`] };
}

// ------------------------------------------------------------------ the Executive stack

// What the launcher starts, in order. Each step's port must open before the next step starts, so
// the fake fleet (rosbridge :9090) and the bridge (:8790) are up before the Executive, and Vite is
// last. The ports are fixed: Vite's origin is the only one the backends accept.
const STEPS = {
  backend: { name: "backend", label: "car backend", port: 8787, waitMs: 60000 },
  fleet: { name: "fleet", label: "fake fleet (rosbridge)", port: 9090, waitMs: 90000 },
  bridge: { name: "bridge", label: "bridge", port: 8790, waitMs: 30000 },
  exec: { name: "exec", label: "Executive", port: 8788, waitMs: 60000 },
  vite: { name: "vite", label: "Vite dev server", port: 5173, waitMs: 30000 },
};
const FLAGS = new Set(["--backend-only", "--no-executive", "--no-bridge"]);
const GATEWAYS = ["synapse", "fake"];

// argv: the launcher's own arguments. Returns { gateway, steps } or { error }.
// HIVE_GATEWAY=synapse (default): the fake fleet + bridge carry the Executive's actions over ROS 2.
// HIVE_GATEWAY=fake: the Executive's in-process sim, no ROS. --no-bridge: an Executive that talks to
// a bridge you run yourself. --no-executive: live-data's backend + Vite. --backend-only: the car
// backend alone.
export function launchPlan({ argv = [], env = {} }) {
  const unknown = argv.find((a) => !FLAGS.has(a));
  if (unknown) return { error: `unknown option ${unknown} (use --backend-only, --no-executive or --no-bridge)` };
  const gateway = env.HIVE_GATEWAY || "synapse";
  if (!GATEWAYS.includes(gateway)) return { error: `HIVE_GATEWAY must be "synapse" or "fake" (got "${gateway}")` };
  const has = (f) => argv.includes(f);
  if (has("--backend-only")) return { gateway, steps: [STEPS.backend] };
  const executive = !has("--no-executive");
  const synapse = executive && gateway === "synapse" && !has("--no-bridge");
  const steps = [STEPS.backend];
  if (synapse) steps.push(STEPS.fleet, STEPS.bridge);
  if (executive) steps.push(STEPS.exec);
  steps.push(STEPS.vite);
  return { gateway, steps };
}

// Variables that must reach the WSL side (WSLENV lists them; plain names, no path translation).
// Only the ones set in env: WSL turns a listed but unset variable into an empty string, which
// float("") and friends then reject.
const WSL_VARS = ["PYTHONUNBUFFERED", "HIVE_GATEWAY", "HIVE_PROVIDERS_FILE", "HIVE_SIM_TIME_SCALE", "HIVE_EXEC_PYTHON", "HIVE_ROS_SETUP"];
export function withWslEnv(existing, env) {
  const parts = (existing || "").split(":").filter(Boolean);
  const names = new Set(parts.map((p) => p.split("/")[0]));
  const add = WSL_VARS.filter((v) => !names.has(v) && env[v] != null && env[v] !== "");
  return [...parts, ...add].join(":");
}

// Variables holding a path that a WSL process reads. A Windows path (C:\... or the C:/... that Git
// Bash hands node for $PWD) becomes its /mnt/<drive> form; WSL and relative paths stay as they are
// (the backend runs from the repo root). Returns a new env.
const WSL_PATH_VARS = ["HIVE_ROS_SETUP"];
export function withWslPaths(env) {
  const out = { ...env };
  for (const v of WSL_PATH_VARS) if (out[v]) out[v] = toWslPath(out[v]);
  return out;
}

const EXEC_PYTHON = "~/.venvs/hive-exec/bin/python";
const expandHome = (p, home) => (p === "~" || p.startsWith("~/") ? `${home}${p.slice(1)}` : p);

// Per service, inside WSL: setup lines, then the command line.
const WSL_SERVICE = {
  exec: {
    setup: [
      'PYBIN="${HIVE_EXEC_PYTHON:-$HOME/.venvs/hive-exec/bin/python}"',
      'case "$PYBIN" in "~/"*) PYBIN="$HOME/${PYBIN#"~/"}" ;; esac',
      '[ -x "$PYBIN" ] || { echo "Executive Python not found: $PYBIN (create ~/.venvs/hive-exec or set HIVE_EXEC_PYTHON)" >&2; exit 127; }',
      "cd backend || exit 1",
    ],
    run: '"$PYBIN" -m hive_exec',
  },
  fleet: { setup: [], run: "bash ros/run_fake_fleet.sh" },
  bridge: {
    setup: [
      '[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"',
      'command -v node > /dev/null || { echo "node not found in WSL; the bridge needs Node 20+ (nvm or a system install)" >&2; exit 127; }',
    ],
    run: "node bridge/server.mjs",
  },
};

// Like supervisedScript, for a service. The service also ignores SIGINT through the shell (trap) so
// a console Ctrl+C reaching WSL cannot cut the script short, gets SIGINT handling back for itself
// (env --default-signal: a background job of a non-interactive bash would otherwise ignore SIGINT),
// and runs in its own process group (setsid) so every child it starts can be found and stopped. When
// the leader exits, whatever is left of its group is stopped before the pidfile goes away.
function serviceScript(name, pidfile) {
  const log = `${pidfile}.log`;
  const { setup, run } = WSL_SERVICE[name];
  return [
    "trap '' HUP INT",
    ...setup,
    `: > ${log}`,
    `setsid env --default-signal=INT ${run} > ${log} 2>&1 < /dev/null &`,
    "PY=$!",
    `echo $PY > ${pidfile}`,
    `tail -n +1 -f --pid=$PY ${log} &`,
    "TL=$!",
    "wait $PY",
    "C=$?",
    "kill -TERM -- -$PY 2>/dev/null",
    "for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do kill -0 -- -$PY 2>/dev/null || break; sleep 0.25; done",
    "kill -KILL -- -$PY 2>/dev/null",
    "wait $TL",
    `rm -f ${pidfile} ${log}`,
    "exit $C",
  ].join("\n");
}

// name: "exec" | "fleet" | "bridge". Linux: run it directly (cwd given). Windows: inside WSL,
// supervised like the backend. home: the Linux home directory; nodePath: the Node binary on Linux.
export function serviceCommand(name, { platform, repoDir, env, pidfile, home, nodePath = "node" }) {
  const repo = repoDir.replace(/[\\/]+$/, "");
  if (platform === "win32") {
    const distro = env.HIVE_WSL_DISTRO ? ["-d", env.HIVE_WSL_DISTRO] : [];
    return { cmd: "wsl.exe", args: [...distro, "--cd", toWslPath(repoDir), "--exec", "bash", "-c", serviceScript(name, pidfile)] };
  }
  if (name === "exec") return { cmd: expandHome(env.HIVE_EXEC_PYTHON || EXEC_PYTHON, home), args: ["-m", "hive_exec"], cwd: `${repo}/backend` };
  if (name === "fleet") return { cmd: "bash", args: ["ros/run_fake_fleet.sh"], cwd: repo };
  if (name === "bridge") return { cmd: nodePath, args: ["bridge/server.mjs"], cwd: repo };
  throw new Error(`unknown service ${name}`);
}

// Shell commands the Windows launcher runs inside WSL against a pidfile. The backend (group: false)
// is signalled by pid, as live-data always did. A service (group: true) is asked by pid first (ros2
// launch and uvicorn then stop their own children), and the retry, liveness check and last-resort
// kill cover its whole process group.
export function wslSignals(pidfile, { group }) {
  if (!group) {
    return {
      stop: `kill -INT $(cat ${pidfile})`,
      retry: `kill -INT $(cat ${pidfile})`,
      alive: `kill -0 $(cat ${pidfile}) 2>/dev/null`,
      kill: `kill -KILL $(cat ${pidfile})`,
    };
  }
  const P = `P=$(cat ${pidfile} 2>/dev/null) || exit 1`;
  return {
    stop: `kill -INT $(cat ${pidfile})`,
    retry: `${P}; kill -INT -- -$P 2>/dev/null; kill -INT $P 2>/dev/null; exit 0`,
    alive: `${P}; kill -0 $P 2>/dev/null || kill -0 -- -$P 2>/dev/null`,
    kill: `${P}; kill -KILL -- -$P 2>/dev/null; kill -KILL $P 2>/dev/null; exit 0`,
  };
}
