import { describe, expect, it } from "vitest";
import { backendCommand, launchPlan, serviceCommand, toWslPath, withWslEnv, withWslPaths, wslSignals } from "./backend-command.mjs";

describe("toWslPath", () => {
  it("lower-cases the drive letter and converts backslashes", () => {
    expect(toWslPath("C:\\Users\\alice\\Projects\\hive")).toBe("/mnt/c/Users/alice/Projects/hive");
  });
  it("handles other drives and forward slashes", () => {
    expect(toWslPath("D:/work/hive")).toBe("/mnt/d/work/hive");
  });
  it("drops a trailing separator", () => {
    expect(toWslPath("C:\\a\\b\\")).toBe("/mnt/c/a/b");
  });
});

describe("backendCommand", () => {
  it("runs python3 on the server script on Linux", () => {
    expect(backendCommand({ platform: "linux", repoDir: "/home/u/hive", env: {} })).toEqual({
      cmd: "python3",
      args: ["/home/u/hive/backend/server.py"],
    });
  });
  it("honours HIVE_PYTHON on Linux", () => {
    const r = backendCommand({ platform: "linux", repoDir: "/home/u/hive", env: { HIVE_PYTHON: "/opt/py/bin/python3" } });
    expect(r.cmd).toBe("/opt/py/bin/python3");
  });
  it("starts the backend inside WSL on win32", () => {
    expect(backendCommand({ platform: "win32", repoDir: "C:\\Users\\alice\\Projects\\hive\\", env: {} })).toEqual({
      cmd: "wsl.exe",
      args: ["--cd", "/mnt/c/Users/alice/Projects/hive", "--exec", "python3", "backend/server.py"],
    });
  });
  it("selects the WSL distro when HIVE_WSL_DISTRO is set", () => {
    const r = backendCommand({ platform: "win32", repoDir: "C:\\hive", env: { HIVE_WSL_DISTRO: "Ubuntu-24.04" } });
    expect(r.args).toEqual(["-d", "Ubuntu-24.04", "--cd", "/mnt/c/hive", "--exec", "python3", "backend/server.py"]);
  });
  it("records the backend pid in a pidfile when asked (win32)", () => {
    const r = backendCommand({ platform: "win32", repoDir: "C:\\hive", env: {}, pidfile: "/tmp/hive-1.pid" });
    expect(r.args.slice(0, 5)).toEqual(["--cd", "/mnt/c/hive", "--exec", "bash", "-c"]);
    const script = r.args[5];
    expect(script).toContain("trap '' HUP");
    expect(script).toContain("python3 backend/server.py > /tmp/hive-1.pid.log 2>&1 &");
    expect(script).toContain("echo $PY > /tmp/hive-1.pid");
    expect(script).toContain("tail -n +1 -f --pid=$PY /tmp/hive-1.pid.log");
    expect(script).toContain("exit $C");
  });
});

describe("launchPlan", () => {
  const names = (plan) => plan.steps.map((s) => s.name);

  it("defaults to the Synapse stack: backend, fake fleet, bridge, Executive, then Vite", () => {
    const plan = launchPlan({ argv: [], env: {} });
    expect(plan.gateway).toBe("synapse");
    expect(names(plan)).toEqual(["backend", "fleet", "bridge", "exec", "vite"]);
  });
  it("waits for rosbridge :9090 and the bridge :8790 before starting the Executive", () => {
    const { steps } = launchPlan({ argv: [], env: {} });
    const port = (n) => steps.find((s) => s.name === n).port;
    expect(port("fleet")).toBe(9090);
    expect(port("bridge")).toBe(8790);
    expect(port("exec")).toBe(8788);
    expect(port("backend")).toBe(8787);
    expect(port("vite")).toBe(5173);
    expect(steps.findIndex((s) => s.name === "exec")).toBeGreaterThan(steps.findIndex((s) => s.name === "bridge"));
  });
  it("HIVE_GATEWAY=fake runs the Executive's in-process sim without ROS or the bridge", () => {
    const plan = launchPlan({ argv: [], env: { HIVE_GATEWAY: "fake" } });
    expect(plan.gateway).toBe("fake");
    expect(names(plan)).toEqual(["backend", "exec", "vite"]);
  });
  it("--no-bridge leaves out the fake fleet and the bridge", () => {
    expect(names(launchPlan({ argv: ["--no-bridge"], env: {} }))).toEqual(["backend", "exec", "vite"]);
  });
  it("--no-executive runs live-data's backend and Vite only", () => {
    expect(names(launchPlan({ argv: ["--no-executive"], env: {} }))).toEqual(["backend", "vite"]);
  });
  it("--backend-only runs the car backend alone, as before", () => {
    expect(names(launchPlan({ argv: ["--backend-only"], env: {} }))).toEqual(["backend"]);
  });
  it("defaults HIVE_SIM_WORLD to random and keeps a world the caller already set", () => {
    expect(launchPlan({ argv: [], env: {} }).simWorld).toBe("random");
    expect(launchPlan({ argv: [], env: { HIVE_SIM_WORLD: "fixed" } }).simWorld).toBe("fixed");
    expect(launchPlan({ argv: ["--backend-only"], env: {} }).simWorld).toBe("random");
  });
  it("rejects an unknown HIVE_SIM_WORLD", () => {
    expect(launchPlan({ argv: [], env: { HIVE_SIM_WORLD: "chaos" } }).error).toMatch(/HIVE_SIM_WORLD/);
  });
  it("rejects an unknown gateway and an unknown flag", () => {
    expect(launchPlan({ argv: [], env: { HIVE_GATEWAY: "ros" } }).error).toMatch(/HIVE_GATEWAY/);
    expect(launchPlan({ argv: ["--no-exec"], env: {} }).error).toMatch(/--no-exec/);
  });
});

describe("serviceCommand", () => {
  it("runs the Executive with the hive-exec venv from backend/ on Linux", () => {
    const r = serviceCommand("exec", { platform: "linux", repoDir: "/home/u/hive/", env: {}, home: "/home/u" });
    expect(r).toEqual({ cmd: "/home/u/.venvs/hive-exec/bin/python", args: ["-m", "hive_exec"], cwd: "/home/u/hive/backend" });
  });
  it("honours HIVE_EXEC_PYTHON (with ~) on Linux", () => {
    const r = serviceCommand("exec", { platform: "linux", repoDir: "/h", env: { HIVE_EXEC_PYTHON: "~/py/bin/python" }, home: "/home/u" });
    expect(r.cmd).toBe("/home/u/py/bin/python");
  });
  it("runs the fake fleet script and the bridge from the repo on Linux", () => {
    expect(serviceCommand("fleet", { platform: "linux", repoDir: "/h", env: {}, home: "/u" })).toEqual({
      cmd: "bash",
      args: ["ros/run_fake_fleet.sh"],
      cwd: "/h",
    });
    expect(serviceCommand("bridge", { platform: "linux", repoDir: "/h", env: {}, home: "/u", nodePath: "/usr/bin/node" })).toEqual({
      cmd: "/usr/bin/node",
      args: ["bridge/server.mjs"],
      cwd: "/h",
    });
  });

  const win = (name, env = {}) => serviceCommand(name, { platform: "win32", repoDir: "C:\\hive", env, pidfile: "/tmp/hive-exec-1.pid" });

  it("supervises each service inside WSL like the backend: HUP ignored, log file, pidfile", () => {
    const r = win("exec", { HIVE_WSL_DISTRO: "Ubuntu-24.04" });
    expect(r.cmd).toBe("wsl.exe");
    expect(r.args.slice(0, 7)).toEqual(["-d", "Ubuntu-24.04", "--cd", "/mnt/c/hive", "--exec", "bash", "-c"]);
    const script = r.args[7];
    expect(script).toContain("trap '' HUP INT");
    expect(script).toContain("echo $PY > /tmp/hive-exec-1.pid");
    expect(script).toContain("tail -n +1 -f --pid=$PY /tmp/hive-exec-1.pid.log");
    expect(script).toContain("rm -f /tmp/hive-exec-1.pid /tmp/hive-exec-1.pid.log");
    expect(script).toContain("exit $C");
  });
  it("starts each WSL service in its own process group with SIGINT handling restored", () => {
    // A background job of a non-interactive bash ignores SIGINT; the service must get it back.
    for (const name of ["exec", "fleet", "bridge"]) {
      expect(win(name).args.at(-1)).toMatch(/setsid env --default-signal=INT .+ > \/tmp\/hive-exec-1\.pid\.log 2>&1 < \/dev\/null &/);
    }
  });
  it("clears the whole process group once the service leader exits, so no child is orphaned", () => {
    const script = win("fleet").args.at(-1);
    expect(script).toContain("kill -TERM -- -$PY");
    expect(script).toContain("kill -KILL -- -$PY");
    expect(script.indexOf("wait $PY")).toBeLessThan(script.indexOf("kill -TERM -- -$PY"));
    expect(script.indexOf("kill -KILL -- -$PY")).toBeLessThan(script.indexOf("rm -f"));
  });
  it("finds the Executive python from HIVE_EXEC_PYTHON or the default venv inside WSL, then cds to backend/", () => {
    const script = win("exec").args.at(-1);
    expect(script).toContain('PYBIN="${HIVE_EXEC_PYTHON:-$HOME/.venvs/hive-exec/bin/python}"');
    expect(script).toContain("cd backend");
    expect(script).toContain('"$PYBIN" -m hive_exec');
  });
  it("loads nvm for the bridge inside WSL and runs the fleet script", () => {
    expect(win("bridge").args.at(-1)).toContain('. "$HOME/.nvm/nvm.sh"');
    expect(win("bridge").args.at(-1)).toContain("node bridge/server.mjs");
    expect(win("fleet").args.at(-1)).toContain("bash ros/run_fake_fleet.sh");
  });
});

describe("wslSignals", () => {
  it("signals the backend by its pid only, exactly as live-data did", () => {
    expect(wslSignals("/tmp/b.pid", { group: false })).toEqual({
      stop: "kill -INT $(cat /tmp/b.pid)",
      retry: "kill -INT $(cat /tmp/b.pid)",
      alive: "kill -0 $(cat /tmp/b.pid) 2>/dev/null",
      kill: "kill -KILL $(cat /tmp/b.pid)",
    });
  });
  it("asks a service leader first, then its group, and checks the group is gone", () => {
    const s = wslSignals("/tmp/s.pid", { group: true });
    expect(s.stop).toBe("kill -INT $(cat /tmp/s.pid)");
    expect(s.retry).toContain("kill -INT -- -$P");
    expect(s.alive).toContain("kill -0 $P 2>/dev/null || kill -0 -- -$P 2>/dev/null");
    expect(s.kill).toContain("kill -KILL -- -$P");
  });
});

describe("withWslEnv", () => {
  const all = { PYTHONUNBUFFERED: "1", HIVE_GATEWAY: "synapse", HIVE_PROVIDERS_FILE: "/p.yaml", HIVE_SIM_TIME_SCALE: "5", HIVE_EXEC_PYTHON: "/py" };
  it("adds the variables WSL needs without repeating existing ones", () => {
    expect(withWslEnv(undefined, all)).toBe("PYTHONUNBUFFERED:HIVE_GATEWAY:HIVE_PROVIDERS_FILE:HIVE_SIM_TIME_SCALE:HIVE_EXEC_PYTHON");
    expect(withWslEnv("FOO/p:HIVE_GATEWAY", all)).toBe("FOO/p:HIVE_GATEWAY:PYTHONUNBUFFERED:HIVE_PROVIDERS_FILE:HIVE_SIM_TIME_SCALE:HIVE_EXEC_PYTHON");
  });
  it("leaves out unset and empty variables (WSL would pass them as empty strings)", () => {
    const env = { PYTHONUNBUFFERED: "1", HIVE_GATEWAY: "fake", HIVE_SIM_TIME_SCALE: "" };
    expect(withWslEnv(undefined, env)).toBe("PYTHONUNBUFFERED:HIVE_GATEWAY");
    expect(withWslEnv("", {})).toBe("");
  });
  it("carries HIVE_ROS_SETUP so the car backend can run against the mock ROS", () => {
    expect(withWslEnv(undefined, { HIVE_ROS_SETUP: "/mnt/c/hive/backend/test/mock_ros/setup.bash" })).toBe("HIVE_ROS_SETUP");
  });
  it("carries HIVE_SIM_WORLD so the Executive and the fake fleet in WSL see the launcher's world", () => {
    expect(withWslEnv(undefined, { HIVE_SIM_WORLD: "random" })).toBe("HIVE_SIM_WORLD");
  });
});

describe("withWslPaths", () => {
  it("turns a Windows HIVE_ROS_SETUP into its WSL path (Git Bash hands node C:/...)", () => {
    expect(withWslPaths({ HIVE_ROS_SETUP: "C:/dev/hive/backend/test/mock_ros/setup.bash" }).HIVE_ROS_SETUP).toBe("/mnt/c/dev/hive/backend/test/mock_ros/setup.bash");
    expect(withWslPaths({ HIVE_ROS_SETUP: "C:\\dev\\hive\\setup.bash" }).HIVE_ROS_SETUP).toBe("/mnt/c/dev/hive/setup.bash");
  });
  it("leaves WSL, relative and unset values alone and does not touch the input", () => {
    expect(withWslPaths({ HIVE_ROS_SETUP: "/opt/ros/jazzy/setup.bash" }).HIVE_ROS_SETUP).toBe("/opt/ros/jazzy/setup.bash");
    expect(withWslPaths({ HIVE_ROS_SETUP: "backend/test/mock_ros/setup.bash" }).HIVE_ROS_SETUP).toBe("backend/test/mock_ros/setup.bash");
    const env = { HIVE_GATEWAY: "fake" };
    const out = withWslPaths(env);
    expect(out).toEqual({ HIVE_GATEWAY: "fake" });
    expect(out).not.toBe(env);
    const win = { HIVE_ROS_SETUP: "C:/x/setup.bash" };
    withWslPaths(win);
    expect(win.HIVE_ROS_SETUP).toBe("C:/x/setup.bash");
  });
});
