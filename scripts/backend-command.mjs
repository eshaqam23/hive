// How to start the Hive backend on this machine.
// Linux: python3 backend/server.py. Windows: rclpy only exists in WSL, so run it there.

export function toWslPath(winPath) {
  const p = winPath.replace(/\\/g, "/").replace(/\/+$/, "");
  const m = /^([A-Za-z]):(\/.*)?$/.exec(p);
  if (!m) return p;
  return `/mnt/${m[1].toLowerCase()}${m[2] ?? ""}`;
}

export function backendCommand({ platform, repoDir, env }) {
  if (platform === "win32") {
    const distro = env.HIVE_WSL_DISTRO ? ["-d", env.HIVE_WSL_DISTRO] : [];
    return {
      cmd: "wsl.exe",
      args: [...distro, "--cd", toWslPath(repoDir), "--exec", "python3", "backend/server.py"],
    };
  }
  return { cmd: env.HIVE_PYTHON || "python3", args: [`${repoDir.replace(/\/+$/, "")}/backend/server.py`] };
}
