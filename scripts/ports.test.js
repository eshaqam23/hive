import net from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { busyPorts, portInUseMessage, probePort, waitForPort } from "./ports.mjs";

const servers = [];
function listen(port = 0) {
  const s = net.createServer((c) => c.destroy());
  servers.push(s);
  return new Promise((resolve) => s.listen(port, "127.0.0.1", () => resolve(s.address().port)));
}
async function freePort() {
  const s = net.createServer();
  await new Promise((r) => s.listen(0, "127.0.0.1", r));
  const { port } = s.address();
  await new Promise((r) => s.close(r));
  return port;
}
afterEach(async () => {
  await Promise.all(servers.splice(0).map((s) => new Promise((r) => s.close(r))));
});

describe("probePort", () => {
  it("is true while something listens and false once nothing does", async () => {
    const port = await listen();
    expect(await probePort(port)).toBe(true);
    await Promise.all(servers.splice(0).map((s) => new Promise((r) => s.close(r))));
    expect(await probePort(port)).toBe(false);
  });
  it("gives up after its timeout when a connect hangs", async () => {
    const hang = () => {
      const s = new net.Socket();
      return s; // never connects, never errors
    };
    const t0 = Date.now();
    expect(await probePort(9, { timeoutMs: 50, connect: hang })).toBe(false);
    expect(Date.now() - t0).toBeLessThan(1000);
  });
});

describe("waitForPort", () => {
  it("resolves open once the port starts listening", async () => {
    const port = await freePort();
    setTimeout(() => listen(port), 150);
    expect(await waitForPort(port, { timeoutMs: 3000, intervalMs: 25 })).toBe("open");
  });
  it("resolves timeout when nothing listens in time", async () => {
    expect(await waitForPort(await freePort(), { timeoutMs: 120, intervalMs: 25 })).toBe("timeout");
  });
  it("resolves aborted as soon as the launcher starts stopping", async () => {
    const ac = new AbortController();
    setTimeout(() => ac.abort(), 50);
    const t0 = Date.now();
    expect(await waitForPort(await freePort(), { timeoutMs: 5000, intervalMs: 25, signal: ac.signal })).toBe("aborted");
    expect(Date.now() - t0).toBeLessThan(1000);
  });
});

describe("busyPorts", () => {
  it("lists only the ports something already listens on", async () => {
    const used = await listen();
    const free = await freePort();
    expect(await busyPorts([used, free])).toEqual([used]);
  });
});

describe("portInUseMessage", () => {
  it("names what owns each port and how to stop a leftover one", () => {
    expect(portInUseMessage(8787, "win32")).toMatch(/car backend.*wsl pkill -f backend\/server\.py/);
    expect(portInUseMessage(8787, "linux")).toContain("pkill -f backend/server.py");
    expect(portInUseMessage(8788, "win32")).toMatch(/Executive.*wsl pkill -f hive_exec/);
    expect(portInUseMessage(8790, "win32")).toMatch(/bridge.*wsl pkill -f bridge\/server\.mjs/);
    expect(portInUseMessage(9090, "win32")).toMatch(/rosbridge.*wsl pkill -f fake_fleet\.launch\.py/);
    expect(portInUseMessage(5173, "win32")).toMatch(/Vite/);
  });
  it("never tells Ubuntu users to run wsl", () => {
    for (const p of [8787, 8788, 8790, 9090]) expect(portInUseMessage(p, "linux")).not.toMatch(/wsl /);
  });
});
