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
