#!/usr/bin/env node
// Per-goal timeout check for the bridge (:8790) on the fake fleet. Kept out of check.mjs, whose
// 8 results hive-agent's baseline gate counts.
//
// A hang_scan goal is sent with envelope timeout_s 5. The bridge must stop waiting at
// timeout_s + HIVE_BRIDGE_TIMEOUT_MARGIN_S (start the bridge with the margin at 2), send the
// targeted cancel, and report uncertain transport_lost; the fake fleet must then show the attempt
// canceled. On a core without the HIVE v2 patch the wait stays at the core's 120 s default, so
// the goal is still running at 15 s.
//
//   HIVE_BRIDGE_TIMEOUT_MARGIN_S=2 node bridge/server.mjs   # elsewhere
//   node bridge/timeout-check.mjs [--json out.json]
// Env: HIVE_CORE_DIR (~/.hive-bridge/core, for the ws module), HIVE_BRIDGE_URL (ws://127.0.0.1:8790)
// Exit code 0 only when every check passes.
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";

const coreDir = process.env.HIVE_CORE_DIR || join(homedir(), ".hive-bridge", "core");
const WebSocket = createRequire(join(coreDir, "package.json"))("ws");
const BRIDGE = process.env.HIVE_BRIDGE_URL || "ws://127.0.0.1:8790";
const TIMEOUT_S = 5;
const MARGIN_S = 2;
const TOLERANCE_S = 1.5;
const GIVE_UP_S = 15;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ws = new WebSocket(BRIDGE);
await new Promise((resolve, reject) => {
  ws.once("open", resolve);
  ws.once("error", reject);
});
let reqSeq = 0;
const pending = new Map();
const events = [];
ws.on("message", (raw) => {
  const msg = JSON.parse(raw.toString());
  if (msg.op === "reply" && pending.has(msg.req_id)) {
    const { resolve, reject } = pending.get(msg.req_id);
    pending.delete(msg.req_id);
    if (msg.ok) resolve(msg.result);
    else reject(new Error(msg.error));
  } else if (msg.op === "event") {
    events.push({ at: Date.now(), event: msg.event });
  }
});
function call(op, body = {}) {
  const req_id = `tc-${++reqSeq}`;
  return new Promise((resolve, reject) => {
    pending.set(req_id, { resolve, reject });
    ws.send(JSON.stringify({ op, req_id, ...body }));
  });
}

const results = [];
const record = (name, pass, observed) => {
  results.push({ name, pass, observed });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}\n      ${JSON.stringify(observed)}`);
};

const info = await call("hello");
console.log(`bridge ${BRIDGE}: core patch=${info.patch}, timeout_margin_s=${info.timeout_margin_s}\n`);
if (info.timeout_margin_s !== MARGIN_S) {
  console.log(`REFUSED: start the bridge with HIVE_BRIDGE_TIMEOUT_MARGIN_S=${MARGIN_S} (it reports ${info.timeout_margin_s})`);
  ws.close();
  process.exit(2);
}

const runId = `timeout-check-${Date.now()}`;
await call("reset", { run_id: runId, faults: ["hang_scan"], world: "fixed", seed: null });
const attemptId = `${runId}:scan:1`;
const envelope = {
  schema_version: "hive.task-envelope.v1",
  run_id: runId,
  mission_id: "timeout-check",
  revision: 1,
  task_id: "scan",
  attempt_id: attemptId,
  robot_id: "lidar_01",
  skill: "scan_route",
  params: { route_id: "pickup_corridor" },
  timeout_s: TIMEOUT_S,
};
const t0 = Date.now();
const delivery = await call("submit", { envelope });
const terminal = () => events.find((e) => e.event.attempt_id === attemptId &&
  ["rejected", "succeeded", "failed", "canceled", "uncertain"].includes(e.event.status));
while (!terminal() && Date.now() - t0 < GIVE_UP_S * 1000) await sleep(50);
const end = terminal();
const expected = TIMEOUT_S + MARGIN_S;

if (end) {
  const elapsed = (end.at - t0) / 1000;
  record(`wait timeout: uncertain transport_lost at ${expected} ± ${TOLERANCE_S} s`,
    end.event.status === "uncertain" && end.event.error?.code === "transport_lost" &&
      Math.abs(elapsed - expected) <= TOLERANCE_S,
    { delivery: delivery.detail, status: end.event.status, error: end.event.error, elapsed_s: elapsed });
  // The cancel's effect is unconfirmed on the wire; the fake fleet's own record shows it.
  let status = {};
  const until = Date.now() + 3000;
  do {
    status = await call("status");
    if (status.ended?.[attemptId] === "canceled") break;
    await sleep(100);
  } while (Date.now() < until);
  record("targeted cancel: the fake fleet shows the attempt canceled",
    status.ended?.[attemptId] === "canceled" && !Object.values(status.active || {}).includes(attemptId),
    { ended: status.ended?.[attemptId] ?? null, lidar_active: status.active?.lidar_01 ?? null });
} else {
  const status = await call("status");
  record(`wait timeout: uncertain transport_lost at ${expected} ± ${TOLERANCE_S} s`, false,
    { delivery: delivery.detail, terminal_event: null, waited_s: GIVE_UP_S,
      lidar_active: status.active?.lidar_01 ?? null,
      still_running: Object.values(status.active || {}).includes(attemptId) });
  record("targeted cancel: the fake fleet shows the attempt canceled", false,
    { ended: status.ended?.[attemptId] ?? null, note: `no timeout within ${GIVE_UP_S} s, so no cancel was sent` });
  await call("cancel", { run_id: runId, attempt_id: attemptId }).catch(() => null); // stop the hang
}
await call("reset", { run_id: `${runId}-done`, faults: [], world: "fixed", seed: null }).catch(() => null);
ws.close();

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} timeout checks passed (core patch=${info.patch})`);
const outIdx = process.argv.indexOf("--json");
if (outIdx > 0) {
  writeFileSync(process.argv[outIdx + 1], JSON.stringify({
    checked_at: new Date().toISOString(), bridge: BRIDGE, patch: info.patch,
    timeout_margin_s: info.timeout_margin_s, results,
  }, null, 2));
}
process.exit(passed === results.length ? 0 : 1);
