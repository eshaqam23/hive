#!/usr/bin/env node
// Localhost WebSocket hardening check for the bridge (:8790) and the fake fleet's rosbridge (:9090).
//
// Browsers send an Origin header on every WebSocket handshake and no CORS applies, so any web
// page the user opens could otherwise drive the fake fleet through these localhost ports.
// The Executive gateway and @corebrum/core are not browsers and send no Origin.
//
//   node bridge/origin-check.mjs
// Env: HIVE_CORE_DIR (~/.hive-bridge/core), HIVE_BRIDGE_URL (ws://127.0.0.1:8790),
//      ROSBRIDGE_URL (ws://127.0.0.1:9090)
// Exit code 0 only when every check passes.
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";

const coreDir = process.env.HIVE_CORE_DIR || join(homedir(), ".hive-bridge", "core");
const WebSocket = createRequire(join(coreDir, "package.json"))("ws");
const BRIDGE = process.env.HIVE_BRIDGE_URL || "ws://127.0.0.1:8790";
const ROSBRIDGE = process.env.ROSBRIDGE_URL || "ws://127.0.0.1:9090";
const EVIL = "http://evil.example";

// Resolves "open" or "rejected:<reason>"; never throws.
function connect(url, origin) {
  return new Promise((resolve) => {
    const ws = new WebSocket(url, origin ? { origin } : {});
    const timer = setTimeout(() => { ws.terminate(); resolve({ state: "rejected:timeout" }); }, 5000);
    ws.once("open", () => { clearTimeout(timer); resolve({ state: "open", ws }); });
    ws.once("unexpected-response", (_req, res) => {
      clearTimeout(timer); ws.terminate(); resolve({ state: `rejected:http ${res.statusCode}` });
    });
    ws.once("error", (e) => { clearTimeout(timer); resolve({ state: `rejected:${e.code || e.message}` }); });
  });
}

// Sends one JSON message and resolves the first reply that satisfies `match`.
function request(ws, msg, match) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => { ws.off("message", onMsg); resolve({ timeout: true }); }, 10000);
    function onMsg(raw) {
      const m = JSON.parse(raw.toString());
      if (match(m)) { clearTimeout(timer); ws.off("message", onMsg); resolve(m); }
    }
    ws.on("message", onMsg);
    ws.send(JSON.stringify(msg));
  });
}

const results = [];
const record = (name, pass, observed) => {
  results.push(pass);
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}\n      ${JSON.stringify(observed)}`);
};

// Bridge: a browser-style handshake must be refused.
const evilBridge = await connect(BRIDGE, EVIL);
evilBridge.ws?.close();
record("bridge refuses a handshake with an Origin header", evilBridge.state !== "open", evilBridge.state);

// Bridge: the Executive's no-Origin handshake still works.
const bridge = await connect(BRIDGE);
if (bridge.state === "open") {
  const hello = await request(bridge.ws, { op: "hello", req_id: "oc-hello" }, (m) => m.req_id === "oc-hello");
  record("bridge accepts a no-Origin client (hello ok)", hello.ok === true, { ok: hello.ok, transport: hello.result?.transport });

  // Bridge: robot_id must be a catalog robot before it becomes part of a ROS action name.
  const env = {
    run_id: "origin-check", attempt_id: `origin-check-${Date.now()}`, robot_id: "x/../hive",
    skill: "capture_image",
  };
  const sub = await request(bridge.ws, { op: "submit", req_id: "oc-submit", envelope: env }, (m) => m.req_id === "oc-submit");
  const refused = sub.ok === false || sub.result?.accepted_for_delivery === false;
  record("bridge refuses a robot_id outside the allowlist", refused, { ok: sub.ok, result: sub.result, error: sub.error });
  bridge.ws.close();
} else {
  record("bridge accepts a no-Origin client (hello ok)", false, bridge.state);
}

// rosbridge: a browser-style handshake must be refused.
const evilRos = await connect(ROSBRIDGE, EVIL);
evilRos.ws?.close();
record("rosbridge refuses a handshake with an Origin header", evilRos.state !== "open", evilRos.state);

// rosbridge: no-Origin clients (@corebrum/core) still work, and rosapi is still up.
const ros = await connect(ROSBRIDGE);
if (ros.state === "open") {
  const r = await request(
    ros.ws, { op: "call_service", id: "oc-topics", service: "/rosapi/topics", args: {} },
    (m) => m.id === "oc-topics",
  );
  const n = r.values?.topics?.length ?? 0;
  record("rosbridge accepts a no-Origin client (rosapi topics)", r.result === true && n > 0, { result: r.result, topics: n });
  ros.ws.close();
} else {
  record("rosbridge accepts a no-Origin client (rosapi topics)", false, ros.state);
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} passed`);
process.exit(passed === results.length ? 0 : 1);
