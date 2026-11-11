#!/usr/bin/env node
// Synapse/AgenticROS compatibility check (spec section 10, "Early check").
// Uses the ACTUAL @corebrum/core rosbridge transport against the HIVE ROS 2 fake action servers.
//
//   node bridge/check.mjs [--json out.json]
// Env: HIVE_CORE_DIR (default ~/.hive-bridge/core), ROSBRIDGE_URL (default ws://127.0.0.1:9090)
import { readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const coreDir = process.env.HIVE_CORE_DIR || join(homedir(), ".hive-bridge", "core");
const corePkg = JSON.parse(readFileSync(join(coreDir, "package.json"), "utf8"));
const core = await import(pathToFileURL(join(coreDir, "dist", "index.js")).href);
const url = process.env.ROSBRIDGE_URL || "ws://127.0.0.1:9090";
const ACTION_TYPE = "hive_interfaces/action/ExecuteSkill";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const transport = await core.createTransport({ mode: "rosbridge", rosbridge: { url, reconnect: false } });
await transport.connect();
const results = [];
const record = (name, pass, observed) => {
  results.push({ name, pass, observed });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}\n      ${JSON.stringify(observed)}`);
};

async function control(command, args = {}) {
  const r = await transport.callService({
    service: "/hive/fleet_control",
    type: "hive_interfaces/srv/FleetControl",
    args: { command, args_json: JSON.stringify(args) },
  });
  return JSON.parse(r.values?.result_json || "{}");
}

let runId = "";
let seq = 0;
async function reset(faults = []) {
  runId = `check-${Date.now()}`;
  return control("reset", { run_id: runId, faults });
}

function goal(robot, skill, params, opts = {}) {
  const attempt = `chk-${++seq}`;
  const envelope = {
    schema_version: "hive.task-envelope.v1", run_id: opts.runId || runId, mission_id: "check", revision: 1,
    task_id: `t${seq}`, attempt_id: attempt, robot_id: robot, skill, params, timeout_s: 60,
  };
  const feedback = [];
  const started = Date.now();
  const options = {
    action: `/${robot}/execute_skill`, actionType: ACTION_TYPE,
    args: { run_id: envelope.run_id, attempt_id: attempt, robot_id: robot, skill, envelope_json: JSON.stringify(envelope) },
    onFeedback: (fb) => feedback.push(fb),
  };
  if (corePkg.hive?.patch && corePkg.hive.patch !== "none") options.goalId = `hive:${attempt}`;
  const promise = transport.sendActionGoal(options).then(
    (r) => ({ ok: true, r, ms: Date.now() - started }),
    (e) => ({ ok: false, error: String(e?.message || e), ms: Date.now() - started }),
  );
  return { attempt, feedback, promise, options };
}

const summary = (o) => (o.ok ? { result: o.r.result, rosStatus: o.r.status, hiveStatus: o.r.values?.status,
  error: o.r.values?.error_code || (typeof o.r.values === "string" ? o.r.values : undefined), ms: o.ms }
  : { threw: o.error, ms: o.ms });

console.log(`@corebrum/core ${corePkg.version} synapse=${corePkg.hive?.synapse_commit} patch=${corePkg.hive?.patch}`);
console.log(`rosbridge ${url}\n`);

// 1. success + feedback
await reset();
let g = goal("lidar_01", "scan_route", { route_id: "pickup_corridor" });
let o = await g.promise;
record("success: scan_route completes", o.ok && o.r.values?.status === "succeeded", summary(o));
record("feedback: progress messages reach the client", g.feedback.length > 0, { feedbackCount: g.feedback.length, sample: g.feedback[0] });
record("terminal status: rosbridge GoalStatus exposed to caller", o.ok && o.r.status !== undefined,
  { status: o.ok ? o.r.status : null, note: "HIVE status is also in values.status" });

// 2. rejection (stale run id)
g = goal("camera_01", "capture_image", { area_id: "work_area" }, { runId: "stale-run" });
o = await g.promise;
const rejected = o.ok ? o.r.result === false && /rejected/i.test(String(o.r.values)) : /rejected/i.test(o.error);
const reason = await control("rejection", { attempt_id: g.attempt });
record("rejection: goal rejected by server is distinguishable", rejected, { ...summary(o), serverReason: reason });

// 3. abort (navigation into a blocked corridor)
await reset(["route_blocked"]);
g = goal("gripper_01", "navigate_to", { location_id: "pickup_zone", route_id: "pickup_corridor" });
o = await g.promise;
record("abort: failed skill returns aborted with HIVE error", o.ok && o.r.values?.status === "failed", summary(o));

// 4. targeted cancellation (lidar hangs; camera runs concurrently and must be unaffected)
await reset(["hang_scan"]);
const lidar = goal("lidar_01", "scan_route", { route_id: "pickup_corridor" });
const camera = goal("camera_01", "capture_image", { area_id: "work_area" });
await sleep(600);
if (transport.cancelActionGoal.length >= 2 && lidar.options.goalId) {
  await transport.cancelActionGoal(lidar.options.action, lidar.options.goalId);
} else {
  await transport.cancelActionGoal(lidar.options.action);
}
const lidarOut = await Promise.race([lidar.promise, sleep(4000).then(() => ({ ok: false, error: "no terminal result within 4s" }))]);
const camOut = await camera.promise;
const active = (await control("status")).active;
record("targeted cancel: only the addressed goal is canceled", lidarOut.ok && lidarOut.r.values?.status === "canceled",
  { lidar: summary(lidarOut), lidarStillActive: Object.values(active).includes(lidar.attempt) });
record("cancel isolation: concurrent goal on another robot unaffected", camOut.ok && camOut.r.values?.status === "succeeded", summary(camOut));
await reset(); // coordinated reset stops any goal the cancel failed to stop

// 5. concurrent routing to two robots
const t0 = Date.now();
const a = goal("camera_01", "inspect_area", { area_id: "work_area", object_query: { label: "red_block" } });
const b = goal("lidar_01", "scan_route", { route_id: "pickup_corridor" });
const [oa, ob] = await Promise.all([a.promise, b.promise]);
const wall = Date.now() - t0;
record("concurrency: two robots execute in parallel", oa.ok && ob.ok && wall < oa.ms + ob.ms - 200,
  { camera: summary(oa), lidar: summary(ob), wallMs: wall, observations: JSON.parse(oa.r?.values?.observations_json || "[]").length });

await transport.disconnect();
const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} checks passed (core patch=${corePkg.hive?.patch})`);
const outIdx = process.argv.indexOf("--json");
if (outIdx > 0) {
  writeFileSync(process.argv[outIdx + 1], JSON.stringify({
    checked_at: new Date().toISOString(), core_version: corePkg.version, synapse_commit: corePkg.hive?.synapse_commit,
    patch: corePkg.hive?.patch, rosbridge: url, results,
  }, null, 2));
}
process.exit(0);
