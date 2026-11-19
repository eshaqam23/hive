import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { shutdown, shutdownAll } from "./shutdown.mjs";

function fakeChild() {
  const c = new EventEmitter();
  c.exitCode = null;
  c.signalCode = null;
  c.kill = vi.fn();
  c.exit = (code = 0) => {
    c.exitCode = code;
    c.emit("exit", code);
  };
  return c;
}

beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] }));
afterEach(() => vi.useRealTimers());

describe("shutdown", () => {
  it("asks the backend to stop first and does not kill it when it exits in time", async () => {
    const backend = fakeChild();
    const frontend = fakeChild();
    const requestStop = vi.fn(async () => {
      setTimeout(() => backend.exit(0), 300);
    });
    const retryStop = vi.fn();
    const p = shutdown({ backend, frontend, platform: "win32", requestStop, retryStop });
    await vi.advanceTimersByTimeAsync(500);
    expect(await p).toBe("exited");
    expect(requestStop).toHaveBeenCalledTimes(1);
    expect(retryStop).not.toHaveBeenCalled();
    expect(backend.kill).not.toHaveBeenCalled();
    expect(frontend.kill).toHaveBeenCalledWith("SIGTERM");
  });

  it("asks again after 3 s, before any kill", async () => {
    const backend = fakeChild();
    const order = [];
    backend.kill.mockImplementation(() => order.push("kill"));
    const requestStop = vi.fn(async () => order.push("ask"));
    const retryStop = vi.fn(async () => {
      order.push("retry");
      setTimeout(() => backend.exit(0), 100);
    });
    const p = shutdown({ backend, frontend: null, platform: "win32", requestStop, retryStop });
    await vi.advanceTimersByTimeAsync(2900);
    expect(retryStop).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(400);
    expect(await p).toBe("retried");
    expect(order).toEqual(["ask", "retry"]);
  });

  it("hard-kills only after both waits, then stops the frontend after the backend", async () => {
    const backend = fakeChild();
    const frontend = fakeChild();
    const order = [];
    backend.kill.mockImplementation(() => order.push("backend"));
    frontend.kill.mockImplementation(() => order.push("frontend"));
    const p = shutdown({ backend, frontend, platform: "win32", requestStop: async () => {}, retryStop: async () => {} });
    await vi.advanceTimersByTimeAsync(3000);
    expect(backend.kill).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(3000);
    expect(await p).toBe("killed");
    expect(order).toEqual(["backend", "frontend"]);
  });

  it("still waits and kills if the stop requests fail", async () => {
    const backend = fakeChild();
    const fail = async () => {
      throw new Error("no wsl");
    };
    const p = shutdown({ backend, frontend: null, platform: "win32", requestStop: fail, retryStop: fail });
    await vi.advanceTimersByTimeAsync(6100);
    expect(await p).toBe("killed");
    expect(backend.kill).toHaveBeenCalled();
  });

  it("on Linux asks for a stop, waits up to 3 s, then SIGKILLs", async () => {
    const backend = fakeChild();
    const requestStop = vi.fn();
    const p = shutdown({ backend, frontend: null, platform: "linux", requestStop });
    expect(requestStop).toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(3000);
    expect(await p).toBe("killed");
    expect(backend.kill).toHaveBeenCalledWith("SIGKILL");
  });

  it("on Windows keeps waiting after wsl.exe exits until the backend inside WSL is gone", async () => {
    const backend = fakeChild();
    let alive = true;
    const isGone = vi.fn(async () => !alive);
    const requestStop = vi.fn(async () => {
      backend.exit(0); // the console Ctrl+C killed wsl.exe first
      setTimeout(() => (alive = false), 1500); // the backend finishes its shutdown stop later
    });
    const p = shutdown({ backend, frontend: null, platform: "win32", requestStop, retryStop: vi.fn(), isGone });
    await vi.advanceTimersByTimeAsync(1000);
    expect(isGone).toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(await p).toBe("exited");
    expect(backend.kill).not.toHaveBeenCalled();
  });

  it("skips waiting when the backend already exited", async () => {
    const backend = fakeChild();
    backend.exitCode = 1;
    expect(await shutdown({ backend, frontend: null, platform: "linux" })).toBe("exited");
  });
});

describe("shutdownAll", () => {
  const svc = (name, child, order, extra = {}) => ({
    name,
    child,
    requestStop: vi.fn(async () => {
      order.push(`ask:${name}`);
      setTimeout(() => child.exit(0), 200);
    }),
    ...extra,
  });

  it("stops the car backend first, then each service in order, then Vite", async () => {
    const order = [];
    const backend = fakeChild();
    const vite = fakeChild();
    vite.kill.mockImplementation(() => order.push("kill:vite"));
    const services = ["exec", "bridge", "fleet"].map((n) => svc(n, fakeChild(), order));
    const p = shutdownAll({ backend: svc("backend", backend, order), services, frontend: vite, platform: "win32" });
    await vi.advanceTimersByTimeAsync(2000);
    expect(await p).toEqual({ backend: "exited", exec: "exited", bridge: "exited", fleet: "exited" });
    expect(order).toEqual(["ask:backend", "ask:exec", "ask:bridge", "ask:fleet", "kill:vite"]);
  });

  it("does not ask any service to stop until the backend is gone", async () => {
    const order = [];
    const backend = fakeChild();
    const exec = fakeChild();
    const slowBackend = svc("backend", backend, order, {
      requestStop: vi.fn(async () => {
        order.push("ask:backend");
        setTimeout(() => backend.exit(0), 2500); // stopping every robot takes a while
      }),
    });
    const p = shutdownAll({ backend: slowBackend, services: [svc("exec", exec, order)], frontend: null, platform: "win32" });
    await vi.advanceTimersByTimeAsync(2400);
    expect(order).toEqual(["ask:backend"]);
    await vi.advanceTimersByTimeAsync(1000);
    await p;
    expect(order).toEqual(["ask:backend", "ask:exec"]);
  });

  it("retries, then hard-kills a stuck service with its own hardKill, and still stops the rest", async () => {
    const order = [];
    const stuck = fakeChild();
    const hardKill = vi.fn(async () => order.push("hardkill:fleet"));
    const fleet = { name: "fleet", child: stuck, requestStop: vi.fn(), retryStop: vi.fn(async () => order.push("retry:fleet")), hardKill, waitMs: 1000 };
    const vite = fakeChild();
    vite.kill.mockImplementation(() => order.push("kill:vite"));
    const p = shutdownAll({ backend: null, services: [fleet], frontend: vite, platform: "win32" });
    await vi.advanceTimersByTimeAsync(2500);
    expect(await p).toEqual({ fleet: "killed" });
    expect(order).toEqual(["retry:fleet", "hardkill:fleet", "kill:vite"]);
    expect(stuck.kill).not.toHaveBeenCalled(); // hardKill replaces the default child.kill
  });

  it("does not hang on a hardKill that never settles", async () => {
    const stuck = fakeChild();
    const fleet = { name: "fleet", child: stuck, requestStop: vi.fn(), hardKill: () => new Promise(() => {}), waitMs: 500 };
    const p = shutdownAll({ backend: null, services: [fleet], frontend: null, platform: "linux" });
    await vi.advanceTimersByTimeAsync(1100);
    expect(await p).toEqual({ fleet: "killed" });
  });

  it("skips services that never started", async () => {
    const p = shutdownAll({ backend: null, services: [{ name: "exec", child: null }], frontend: null, platform: "linux" });
    expect(await p).toEqual({ exec: "exited" });
  });
});
