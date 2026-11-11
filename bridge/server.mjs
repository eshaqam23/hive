#!/usr/bin/env node
// HIVE <-> AgenticROS (Corebrum Synapse) bridge.
//
// A narrow WebSocket bridge that imports the ACTUAL @corebrum/core package and uses its
// rosbridge transport to reach the HIVE ROS 2 fake action servers. HIVE keeps planning,
// validation, approval and scheduling; this bridge only moves envelopes, events and cancels.
//
//   node bridge/server.mjs
// Env: HIVE_CORE_DIR (~/.hive-bridge/core), ROSBRIDGE_URL (ws://127.0.0.1:9090),
//      HIVE_BRIDGE_PORT (8790; bound to 127.0.0.1)
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const coreDir = process.env.HIVE_CORE_DIR || join(homedir(), ".hive-bridge", "core");
const corePkg = JSON.parse(readFileSync(join(coreDir, "package.json"), "utf8"));
const core = await import(pathToFileURL(join(coreDir, "dist", "index.js")).href);
const { WebSocketServer } = createRequire(join(coreDir, "package.json"))("ws");

const ROSBRIDGE_URL = process.env.ROSBRIDGE_URL || "ws://127.0.0.1:9090";
const PORT = Number(process.env.HIVE_BRIDGE_PORT || 8790);
const ACTION_TYPE = "hive_interfaces/action/ExecuteSkill";
const PATCH = corePkg.hive?.patch || "none";
const ROS_STATUS = { 4: "succeeded", 5: "canceled", 6: "aborted" };

const attempts = new Map(); // attempt_id -> { env, seq, goalId, action, terminal }
const clients = new Set();

const transport = await core.createTransport({
  mode: "rosbridge",
  rosbridge: { url: ROSBRIDGE_URL, reconnect: true, reconnectInterval: 1000 },
});
let rosConnected = false;
transport.onConnection((status) => {
  rosConnected = status === "connected";
  broadcast({ op: "transport", connected: rosConnected, status });
  log(`rosbridge ${status}`);
});
await transport.connect().catch((e) => log(`rosbridge connect failed: ${e.message}`));
rosConnected = transport.getStatus() === "connected";

function log(msg) {
  console.log(`[hive-bridge] ${new Date().toISOString()} ${msg}`);
}

function broadcast(msg) {
  const data = JSON.stringify(msg);
  for (const ws of clients) if (ws.readyState === 1) ws.send(data);
}

function info() {
  return {
    transport: "synapse",
    package: "@corebrum/core",
    core_version: corePkg.version,
    synapse_commit: corePkg.hive?.synapse_commit,
    patch: PATCH,
    rosbridge_url: ROSBRIDGE_URL,
    rosbridge_connected: rosConnected,
  };
}

async function control(command, args = {}) {
  const r = await transport.callService({
    service: "/hive/fleet_control",
    type: "hive_interfaces/srv/FleetControl",
    args: { command, args_json: JSON.stringify(args) },
  });
  const parsed = JSON.parse(r.values?.result_json || "{}");
  if (!r.values?.ok) throw new Error(parsed.error || `fleet_control ${command} failed`);
  return parsed;
}

function emit(a, status, extra = {}) {
  a.seq += 1;
  const event = {
    schema_version: "hive.task-event.v1",
    event_id: `${a.env.attempt_id}:${a.seq}`,
    run_id: a.env.run_id,
    attempt_id: a.env.attempt_id,
    robot_id: a.env.robot_id,
    seq: a.seq,
    status,
    progress: extra.progress ?? 0,
    result: extra.result ?? null,
    error: extra.error ?? null,
    observations: extra.observations ?? [],
    energy_used_pct: extra.energy_used_pct ?? null,
  };
  if (["rejected", "succeeded", "failed", "canceled", "uncertain"].includes(status)) a.terminal = true;
  broadcast({ op: "event", event, transport: { ros_status: extra.ros_status ?? null } });
}

const parseJson = (s, fallback) => {
  try {
    return s ? JSON.parse(s) : fallback;
  } catch {
    return fallback;
  }
};

async function submit(env) {
  if (attempts.has(env.attempt_id)) return { accepted_for_delivery: true, detail: "duplicate submission ignored" };
  if (!rosConnected) return { accepted_for_delivery: false, detail: "rosbridge not connected" };
  const action = `/${env.robot_id}/execute_skill`;
  const a = { env, seq: 0, action, goalId: `hive:${env.attempt_id}`, terminal: false, accepted: false };
  attempts.set(env.attempt_id, a);
  const options = {
    action,
    actionType: ACTION_TYPE,
    args: {
      run_id: env.run_id,
      attempt_id: env.attempt_id,
      robot_id: env.robot_id,
      skill: env.skill,
      envelope_json: JSON.stringify(env),
    },
    goalId: a.goalId, // honoured only with the HIVE v1 patch; ignored by unpatched core
    onFeedback: (fb) => {
      if (a.terminal || fb.attempt_id !== env.attempt_id) return;
      if (fb.phase === "accepted" && !a.accepted) {
        a.accepted = true;
        emit(a, "accepted");
      } else if (fb.phase === "running") {
        emit(a, "running", { progress: fb.progress });
      }
    },
  };
  transport.sendActionGoal(options).then(
    async (r) => {
      if (a.terminal) return;
      if (r.result === false) {
        const text = typeof r.values === "string" ? r.values : JSON.stringify(r.values);
        if (/rejected/i.test(text)) {
          const reason = await control("rejection", { attempt_id: env.attempt_id }).catch(() => ({}));
          emit(a, "rejected", { error: { code: reason.code || "goal_rejected", message: reason.message || text } });
        } else {
          emit(a, "uncertain", { error: { code: "transport_failure", message: text.slice(0, 300) } });
        }
        return;
      }
      const v = r.values || {};
      let status = v.status;
      const ros = r.status;
      // Cross-check the HIVE status with the ROS terminal status when the transport provides it.
      if (ros !== undefined && ROS_STATUS[ros]) {
        const consistent =
          (ros === 4 && status === "succeeded") || (ros === 5 && status === "canceled") ||
          (ros === 6 && ["failed", "uncertain", "canceled"].includes(status));
        if (!consistent) status = "uncertain";
      }
      if (!["succeeded", "failed", "canceled", "uncertain"].includes(status)) status = "uncertain";
      emit(a, status, {
        progress: status === "succeeded" ? 1 : undefined,
        result: parseJson(v.result_json, {}),
        error: v.error_code ? { code: v.error_code, message: v.error_message || "" } : null,
        observations: parseJson(v.observations_json, []),
        energy_used_pct: typeof v.energy_used_pct === "number" ? v.energy_used_pct : null,
        ros_status: ros ?? null,
      });
    },
    (e) => {
      // Timeout or connection loss: the robot may still be executing. Never report success.
      if (!a.terminal) emit(a, "uncertain", { error: { code: "transport_lost", message: String(e?.message || e) } });
    },
  );
  return { accepted_for_delivery: true, detail: "goal sent via @corebrum/core rosbridge transport" };
}

async function cancel(runId, attemptId) {
  const a = attempts.get(attemptId);
  if (!a || a.env.run_id !== runId || a.terminal) return { requested: false };
  await transport.cancelActionGoal(a.action, a.goalId);
  return { requested: true, targeted: PATCH !== "none" };
}

async function handle(msg) {
  switch (msg.op) {
    case "hello":
      return info();
    case "submit":
      return submit(msg.envelope);
    case "cancel":
      return cancel(msg.run_id, msg.attempt_id);
    case "status":
      return control("status");
    case "reset": {
      const r = await control("reset", { run_id: msg.run_id, faults: msg.faults || [] });
      for (const [id, a] of attempts) if (a.env.run_id !== msg.run_id) attempts.delete(id);
      return r;
    }
    case "inject_fault":
      return control("inject_fault", { fault: msg.fault });
    case "truth":
      return control("truth");
    default:
      throw new Error(`unknown op ${msg.op}`);
  }
}

const wss = new WebSocketServer({ host: "127.0.0.1", port: PORT });
wss.on("connection", (ws) => {
  clients.add(ws);
  log("executive connected");
  ws.send(JSON.stringify({ op: "transport", connected: rosConnected, info: info() }));
  ws.on("close", () => clients.delete(ws));
  ws.on("message", async (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      return;
    }
    try {
      const result = await handle(msg);
      ws.send(JSON.stringify({ op: "reply", req_id: msg.req_id, ok: true, result }));
    } catch (e) {
      ws.send(JSON.stringify({ op: "reply", req_id: msg.req_id, ok: false, error: String(e?.message || e) }));
    }
  });
});
log(`listening on ws://127.0.0.1:${PORT} (core ${corePkg.version}, synapse ${corePkg.hive?.synapse_commit}, patch ${PATCH})`);
