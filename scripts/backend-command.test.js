import { describe, expect, it } from "vitest";
import { backendCommand, toWslPath } from "./backend-command.mjs";

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
