import React, { useEffect, useMemo, useRef, useState } from "react";
import LiveView from "./live/LiveView.jsx";
import { useLiveStream } from "./live/useLiveStream.js";
import { postCamera } from "./live/api.js";
import { describeLink, displayMode, newerLink, stopUnconfirmed } from "./live/link.js";
import { connectPatch, driveBody, gripperEnabled, gripperSliderShown, keepaliveBody, notFoundHint } from "./live/carHelpers.js";
import { currentTab, stageTabs } from "./live/stage.js";
import { ALWAYS_WATCHED, watchSet } from "./live/watch.js";
import { compactValue } from "./viewers/ValuePanel.jsx";
import { StopIcon, SendIcon, CloseIcon, TrashIcon, PencilIcon, ChevronLeftIcon, HiveMark, SignalBars } from "./shell/icons.jsx";
import { DRIVE_CONTROLS, PRESET_KEYS, PRESET_ROBOTS, STATUS, describe, displayStatus } from "./fleet/presets.jsx";
import { ModelImage, ModelThumb, PageFooter, PlayButton, StatusBadge, modelAlt, stageBg } from "./fleet/parts.jsx";
import FleetView from "./fleet/FleetView.jsx";
import { activeMission, isSimulated } from "./fleet/execStatus.js";
import MapView from "./map/MapView.jsx";
import Header from "./shell/Header.jsx";
import { liveStatus } from "./shell/status.js";
import { runStopAll } from "./shell/stopAll.js";
import { execPost } from "./executive/api.js";
import { useExecutive } from "./executive/useExecutive.js";
import ChatView from "./chat/ChatView.jsx";

const API_BASE_URL = import.meta.env.VITE_HIVE_API_URL || "http://127.0.0.1:8787/api";
const FLEET_STORAGE_KEY = "hive:fleet:manual-rc-v1";

/* ================================================================== data */

const KEY_TO_ACTION = {
  w: "forward",
  ArrowUp: "forward",
  s: "backward",
  ArrowDown: "backward",
  a: "left",
  ArrowLeft: "left",
  d: "right",
  ArrowRight: "right",
  " ": "stop",
};

/* ================================================================== fleet logic (unchanged behaviour) */

function robotFromPreset({
  id,
  name,
  preset,
  description,
  host,
  domainId,
  status = "offline",
  mode,
  lastCommand,
  lastError,
}) {
  const presetKey = PRESET_ROBOTS[preset] ? preset : "cameraRcCar";
  const robotPreset = PRESET_ROBOTS[presetKey];
  return {
    id: id || makeId(),
    name: name || robotPreset.label,
    preset: presetKey,
    description: description || robotPreset.summary,
    host: host || "",
    domainId: Number(domainId),
    status,
    mode: mode || (status === "connected" ? "Ready" : "Idle"),
    capabilities: robotPreset.capabilities,
    topics: robotPreset.topics,
    controls: robotPreset.controls,
    lastCommand: lastCommand || "",
    lastError: lastError || "",
  };
}

function nextDomainFromSet(usedDomains) {
  let domainId = 42;
  while (usedDomains.has(domainId)) domainId += 1;
  return domainId;
}

function nextDomainId(robots) {
  const usedDomains = new Set(
    robots.map((robot) => Number(robot.domainId)).filter((domainId) => Number.isInteger(domainId)),
  );
  return nextDomainFromSet(usedDomains);
}

function makeId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `robot-${Date.now()}-${Math.round(Math.random() * 1000)}`;
}

const validDomain = (d) => Number.isInteger(d) && d >= 0 && d <= 232;

function loadFleet() {
  const saved = localStorage.getItem(FLEET_STORAGE_KEY);
  if (!saved) return [];

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed)) return [];

    // only a missing or out-of-range domain is repaired; a robot's chosen domain is never changed
    // behind the operator's back, or Hive would quietly stop reaching that robot
    const usedDomains = new Set(parsed.map((r) => Number(r.domainId)).filter(validDomain));
    return parsed.map((robot) => {
      const presetRobot = robotFromPreset(robot);
      let domainId = Number(robot.domainId);
      if (!validDomain(domainId)) {
        domainId = nextDomainFromSet(usedDomains);
        usedDomains.add(domainId);
      }

      // a reload cannot know whether a robot is still reachable, and the backend's drive streams are
      // gone with the old page: every robot starts disconnected and is connected again on purpose
      return {
        ...presetRobot,
        ...robot,
        domainId,
        status: "offline",
        reconnecting: false,
        mode: "Idle",
        stopping: null,
        lastError: "",
        warnings: [],
        capabilities: presetRobot.capabilities,
        topics: presetRobot.topics,
        controls: presetRobot.controls,
      };
    });
  } catch {
    return [];
  }
}

const BACKEND_ORIGIN = API_BASE_URL.replace(/\/api\/?$/, "");
const target = (robot) => ({ ip: robot.host, domain_id: robot.domainId });

/* Drive and gripper commands are numbered per tab, so the backend can reject one that arrives after
   a newer one (a slow request must never undo a stop). */
const CONTROL_SESSION = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
let controlSeq = 0;
const ordered = (robot, action) => ({ ...target(robot), action, session: CONTROL_SESSION, seq: ++controlSeq });

async function postApi(path, payload) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    const error = new Error(`Can't reach the Hive backend at ${BACKEND_ORIGIN}. Start it with npm run backend.`);
    error.unreachable = true;
    throw error;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    const error = new Error(data.error || `Request failed with status ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

/* Failures that mean the link to the robot is gone, so Hive reconnects by itself, and failures that
   retrying cannot fix (bad input, ROS 2 not installed). The backend names each failure in `code`. */
const LINK_LOST_CODES = new Set(["worker_gone", "worker_failed", "worker_error", "lost_contact", "not_found", "timeout"]);
const linkLost = (error) => Boolean(error.unreachable || LINK_LOST_CODES.has(error.data?.code) || error.status === 504);
const retryable = (error) => ![400, 403, 404, 413].includes(error.status) && error.data?.code !== "no_ros";
const RETRY_DELAYS_MS = [1000, 2000, 3000, 5000]; // then every 5 s, until it works or the operator disconnects
const STATUS_CHECK_MS = 10000;
const LINK_POLL_MS = 300; // after a stop, how often the page asks for the newest link
const LINK_FOLLOW_MS = 6000; // and for how long: the worker resends a stop for 5 s
const REPLACED_MODE = "Reconnected — stop sent";
const ACTIVITY_LIMIT = 200;
let activityId = 0;

const ACTION_TEXT = {
  forward: "forward",
  backward: "backward",
  left: "rotate left",
  right: "rotate right",
  stop: "stop",
  gripper_open: "open gripper",
  gripper_close: "close gripper",
};

function requestLabel(path, payload) {
  const action = payload?.action ? ` ${ACTION_TEXT[payload.action] || payload.action}` : "";
  const seq = payload?.seq ? ` #${payload.seq}` : "";
  return `${path.replace("/robots/", "")}${action}${seq}`;
}

function replySummary(path, data) {
  switch (path) {
    case "/robots/connect":
      return `found ${data.nodes?.length ?? 0} nodes and ${data.topics?.length ?? 0} topics, ${data.cmd_vel_subscribers ?? 0} listening on /cmd_vel`;
    case "/robots/status":
      return `${data.nodes?.length ?? 0} nodes answering`;
    case "/robots/drive":
      return data.streaming ? `robot driving ${ACTION_TEXT[data.action]}, streaming /cmd_vel at 10 Hz` : "stop sent (zero Twist burst), waiting for the car to confirm";
    case "/robots/control":
      return data.topic ? `published on ${data.topic}` : `robot ${data.streaming ? "driving" : "stopped"}`;
    case "/robots/command":
      return `delivered on ${data.topic}`;
    case "/robots/disconnect":
      return data.stopped ? "robot stopped, link closed" : data.warning || "link closed";
    default:
      return "ok";
  }
}

const DRIVE_LABEL = { forward: "forward", backward: "backward", left: "rotating left", right: "rotating right" };

/* The backend's health route, polled so the sidebar can say whether commands will work and whether
   ROS 2 is installed on this machine. */
function useBackendHealth() {
  const [health, setHealth] = useState({ state: "checking", ros: null });
  useEffect(() => {
    let alive = true;
    const check = () =>
      fetch(`${API_BASE_URL}/health`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((d) => alive && setHealth({ state: d.ok ? "online" : "offline", ros: d.ros || null }))
        .catch(() => alive && setHealth({ state: "offline", ros: null }));
    check();
    const timer = window.setInterval(check, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  return health;
}

/* ================================================================== app */

export default function App() {
  const [robots, setRobots] = useState(loadFleet);
  const [view, setView] = useState("fleet");
  const [selectedRobotId, setSelectedRobotId] = useState(robots[0]?.id);
  const [selectedTopic, setSelectedTopic] = useState(null);
  const [addingPreset, setAddingPreset] = useState(null);
  const [removingRobotId, setRemovingRobotId] = useState(null);
  const [editingRobotId, setEditingRobotId] = useState(null);
  const [pressed, setPressed] = useState(null);
  const [power, setPower] = useState(0.5);
  const powerRef = useRef(0.5);
  powerRef.current = power;
  const [links, setLinks] = useState({}); // robot id -> newest link (highest seq) from any route (R28)
  const [messages, setMessages] = useState([{ role: "agent", text: "Hive is ready." }]);
  const pressTimer = useRef(0);
  const driveRef = useRef({ robotId: null, action: null, timer: 0 });
  const retryRef = useRef({}); // robot id -> the connect loop that is still trying
  const followRef = useRef({}); // robot id -> timer of the poll that follows a stop's confirmation
  const [activity, setActivity] = useState({}); // robot id -> newest-first log entries
  const robotsRef = useRef(robots);
  robotsRef.current = robots;
  const backend = useBackendHealth();
  // the mission chat polls the Executive every 500 ms and parks while the browser tab is hidden
  const exec = useExecutive({ stateMs: view === "chat" ? 500 : 2000, pauseHidden: view === "chat" });
  const [stopRun, setStopRun] = useState(null); // the last STOP ALL: { id, at, cars, results }

  const selectedRobot = robots.find((robot) => robot.id === selectedRobotId) || robots[0];

  useEffect(() => {
    localStorage.setItem(FLEET_STORAGE_KEY, JSON.stringify(robots));
  }, [robots]);

  useEffect(() => {
    if (!selectedRobot && robots.length) setSelectedRobotId(robots[0].id);
  }, [robots, selectedRobot]);

  /* a new view starts at its top, not at the last view's scroll position */
  useEffect(() => {
    document.getElementById("main")?.scrollTo({ top: 0 });
  }, [view, selectedRobotId]);

  function openRobot(robot) {
    setSelectedRobotId(robot.id);
    setSelectedTopic(null);
    setView("robot");
  }

  function addRobot(formData) {
    const robot = robotFromPreset({ ...formData, domainId: Number(formData.domainId) || nextDomainId(robots) });
    setRobots((current) => [...current, robot]);
    setAddingPreset(null);
    openRobot(robot);
  }

  /* A patch that sets a new mode ends the stop display (robot.stopping) unless it sets that too. */
  function updateRobot(id, patch) {
    const next = "mode" in patch && !("stopping" in patch) ? { ...patch, stopping: null } : patch;
    setRobots((current) => current.map((robot) => (robot.id === id ? { ...robot, ...next } : robot)));
  }

  /* Keep the newest link per robot, whatever route it came by: a stale one never overwrites a newer one. */
  function noteLink(robotId, link) {
    if (!link) return;
    setLinks((all) => {
      const newest = newerLink(all[robotId], link);
      return newest === all[robotId] ? all : { ...all, [robotId]: newest };
    });
  }

  /* After a stop, ask for the newest link until the car confirms (or the worker's 5 s of resends are
     over), so the mode and the Home badge follow the confirmation even where no live stream is open. */
  function followLink(robot) {
    window.clearTimeout(followRef.current[robot.id]);
    const until = Date.now() + LINK_FOLLOW_MS;
    const poll = async () => {
      const current = robotsRef.current.find((r) => r.id === robot.id);
      if (current?.status !== "connected") return;
      let link = null;
      try {
        link = (await postApi("/robots/link", target(current))).link;
        noteLink(robot.id, link);
      } catch {
        // a lost link is the status check's business
      }
      if (link?.stop !== "confirmed" && Date.now() < until) followRef.current[robot.id] = window.setTimeout(poll, LINK_POLL_MS);
    };
    followRef.current[robot.id] = window.setTimeout(poll, LINK_POLL_MS);
  }

  useEffect(() => () => Object.values(followRef.current).forEach((timer) => window.clearTimeout(timer)), []);

  /* A robot that may be live is told to stop and disconnect as it leaves the fleet. If that request
     fails, the backend's deadman still stops a robot whose keepalives have ended. */
  function removeRobot(robot) {
    setRemovingRobotId(null);
    stopRetrying(robot.id);
    if (driveRef.current.robotId === robot.id) endDriveLocally();
    if (robot.status === "connected" || robot.status === "connecting") {
      postApi("/robots/disconnect", target(robot)).catch(() => {});
    }
    setRobots((current) => current.filter((r) => r.id !== robot.id));
    if (view === "robot" && selectedRobot?.id === robot.id) setView("fleet");
  }

  /* A car's IP changes with the Wi-Fi lease, so its address and domain can be changed in place. The
     backend's worker is keyed by address, so the OLD address is always disconnected first: a live car
     is stopped there, and a leftover worker of an offline one is closed, since it can still reach the
     car (the car's static peer is this machine) and would mix its commands into /cmd_vel. The car
     then waits offline: the operator presses Connect to use the new address. */
  function changeAddress(robot, { host, domainId }) {
    setEditingRobotId(null);
    const current = robotsRef.current.find((r) => r.id === robot.id);
    if (!current || (current.host === host && current.domainId === domainId)) return;
    const live = current.status === "connected" || current.status === "connecting";
    stopRetrying(current.id);
    if (driveRef.current.robotId === current.id) endDriveLocally();
    window.clearTimeout(followRef.current[current.id]);
    if (live) robotApi(current, "/robots/disconnect", target(current)).catch(() => {});
    else if (current.host) postApi("/robots/disconnect", target(current)).catch(() => {});
    setLinks((all) => (all[current.id] ? { ...all, [current.id]: null } : all));
    updateRobot(current.id, { host, domainId, status: "offline", reconnecting: false, mode: "Idle", lastError: "", warnings: [] });
    logActivity(
      current.id,
      "info",
      `Address changed from ${current.host || "no address"} (domain ${current.domainId}) to ${host} (domain ${domainId}).` +
        (live ? " Hive stopped and disconnected it at the old address; press Connect to use the new one." : ""),
    );
  }

  /* ---------------------------------------------------------------- activity log */

  function logActivity(robotId, level, text) {
    const entry = { id: ++activityId, at: Date.now(), level, text };
    setActivity((all) => ({ ...all, [robotId]: [entry, ...(all[robotId] || [])].slice(0, ACTIVITY_LIMIT) }));
  }

  /* Every robot request goes through here, so the robot's activity log shows what was sent, how long
     it took and exactly what came back. Keepalives (four a second) are logged only when they fail. */
  async function robotApi(robot, path, payload, { quiet = false } = {}) {
    const label = requestLabel(path, payload);
    const started = performance.now();
    const took = () => `${Math.round(performance.now() - started)} ms`;
    try {
      const data = await postApi(path, payload);
      if (!quiet) logActivity(robot.id, "ok", `${label}: ${replySummary(path, data)} · ${took()}`);
      return data;
    } catch (error) {
      const status = error.unreachable ? "no response" : `HTTP ${error.status}${error.data?.code ? ` ${error.data.code}` : ""}`;
      logActivity(robot.id, error.data?.superseded ? "info" : "error", `${label}: ${status} · ${error.message} · ${took()}`);
      throw error;
    }
  }

  /* ---------------------------------------------------------------- connection
   * Connecting never gives up on its own: a failed attempt is retried (1, 2, 3, then every 5 s) until
   * the robot answers or the operator presses Disconnect. A connection that is lost later (status
   * check, keepalive or drive failure) goes back into the same loop. Only failures that retrying
   * cannot fix (bad address, ROS 2 not installed) stop it. */

  function connectRobot(robot) {
    setSelectedRobotId(robot.id);
    setSelectedTopic(null);
    logActivity(robot.id, "info", `Connect: looking for ${robot.host} on ROS domain ${robot.domainId}.`);
    keepConnecting(robot);
  }

  function keepConnecting(robot, lostReason = "") {
    stopRetrying(robot.id);
    const run = { attempt: 0, timer: 0 };
    retryRef.current[robot.id] = run;
    if (driveRef.current.robotId === robot.id) endDriveLocally();
    if (lostReason) logActivity(robot.id, "warn", `Connection lost: ${lostReason} Reconnecting until it works; press Disconnect to stop.`);
    updateRobot(robot.id, {
      status: "connecting",
      reconnecting: Boolean(lostReason),
      mode: lostReason ? "Reconnecting…" : "Looking for the robot…",
      lastError: lostReason,
      warnings: [],
    });

    const attempt = async () => {
      const current = robotsRef.current.find((r) => r.id === robot.id);
      if (retryRef.current[robot.id] !== run || !current) return;
      run.attempt += 1;
      if (run.attempt > 1) updateRobot(robot.id, { mode: `${lostReason ? "Reconnecting" : "Connecting"} · attempt ${run.attempt}` });
      try {
        const found = await robotApi(current, "/robots/connect", target(current));
        if (retryRef.current[robot.id] !== run) {
          // the operator disconnected while this attempt was in flight: let the robot go again
          const now = robotsRef.current.find((r) => r.id === robot.id);
          if (now?.status === "offline" && !retryRef.current[robot.id]) postApi("/robots/disconnect", target(current)).catch(() => {});
          return;
        }
        delete retryRef.current[robot.id];
        // a new worker's links start afresh (and if the backend itself restarted, its seq did too)
        setLinks((all) => (all[robot.id] ? { ...all, [robot.id]: null } : all));
        updateRobot(robot.id, {
          status: "connected",
          reconnecting: false,
          mode: found.worker_replaced ? REPLACED_MODE : lostReason ? "Reconnected" : "Ready",
          lastError: "",
          warnings: found.warnings || [],
          discovery: {
            nodes: found.nodes || [],
            topics: found.topics || [],
            cmdVelSubscribers: found.cmd_vel_subscribers ?? 0,
            at: Date.now(),
          },
          ...connectPatch(found),
        });
        logActivity(robot.id, "ok", run.attempt > 1 ? `Connected after ${run.attempt} attempts.` : "Connected.");
        if (found.worker_replaced) workerReplaced(current);
        (found.warnings || []).forEach((w) => logActivity(robot.id, "warn", w));
      } catch (error) {
        if (retryRef.current[robot.id] !== run) return;
        if (!retryable(error)) {
          delete retryRef.current[robot.id];
          updateRobot(robot.id, { status: "failed", reconnecting: false, mode: "Connection failed", lastError: error.message });
          logActivity(robot.id, "error", "Stopped trying: retrying cannot fix this. Fix it, then press Connect.");
          return;
        }
        const delay = RETRY_DELAYS_MS[Math.min(run.attempt - 1, RETRY_DELAYS_MS.length - 1)];
        const message = error.data?.code === "not_found" ? notFoundHint(current.domainId, error.message) : error.message;
        updateRobot(robot.id, { mode: `Attempt ${run.attempt} failed · retrying in ${delay / 1000} s`, lastError: message });
        logActivity(robot.id, "info", `Attempt ${run.attempt} failed. Retrying in ${delay / 1000} s.`);
        run.timer = window.setTimeout(attempt, delay);
      }
    };
    attempt();
  }

  /* R29: the backend replaced a worker that had died (crash, kill); the new one stopped the car first. */
  function workerReplaced(robot) {
    logActivity(robot.id, "warn", "The car's ROS 2 worker had stopped and was restarted; it sent the car a stop before anything else.");
    followLink(robot);
  }

  function stopRetrying(robotId) {
    const run = retryRef.current[robotId];
    if (!run) return false;
    window.clearTimeout(run.timer);
    delete retryRef.current[robotId];
    return true;
  }

  /* Disconnecting stops the robot first; an unreachable robot still disconnects. While Hive is still
     trying to connect, Disconnect is how the operator tells it to stop trying. */
  async function disconnectRobot(robot) {
    const wasTrying = stopRetrying(robot.id);
    if (driveRef.current.robotId === robot.id) endDriveLocally();
    updateRobot(robot.id, { status: "offline", reconnecting: false, mode: "Stopping…", warnings: [] });
    logActivity(robot.id, "info", wasTrying ? "Disconnect: stopped trying to connect." : "Disconnect: stopping the robot and closing the link.");
    try {
      const result = await robotApi(robot, "/robots/disconnect", target(robot));
      updateRobot(robot.id, { mode: result.stopped ? "Stopped and disconnected" : "Disconnected", lastError: result.warning || "" });
    } catch (error) {
      updateRobot(robot.id, { mode: "Disconnected", lastError: error.message });
    }
  }

  /* ---------------------------------------------------------------- driving
   * Hold-to-drive. Pressing a direction starts a /cmd_vel stream on the backend; while it is held the
   * page sends a keepalive every 250 ms; releasing (or Stop, or losing focus) stops the robot. If the
   * page dies mid-drive, the backend's deadman stops the robot within a second. */

  function flash(action) {
    setPressed(action);
    window.clearTimeout(pressTimer.current);
    pressTimer.current = window.setTimeout(() => setPressed(null), 160);
  }

  function endDriveLocally() {
    window.clearInterval(driveRef.current.timer);
    driveRef.current = { robotId: null, action: null, timer: 0 };
    setPressed(null);
  }

  /* a failed drive request either lost the link (reconnect) or was refused (say why, stay connected) */
  function driveFailed(robot, error, mode) {
    if (linkLost(error)) return keepConnecting(robot, error.message);
    updateRobot(robot.id, { mode, lastError: error.message });
  }

  async function keepDriving(robot) {
    if (driveRef.current.robotId !== robot.id) return;
    try {
      noteLink(robot.id, (await robotApi(robot, "/robots/drive/keepalive", keepaliveBody(target(robot), powerRef.current), { quiet: true })).link);
      if (driveRef.current.robotId === robot.id) driveRef.current.keepalives += 1;
    } catch (error) {
      if (driveRef.current.robotId !== robot.id) return;
      endDriveLocally();
      driveFailed(robot, error, "Drive stopped");
    }
  }

  async function pressDrive(robot, action) {
    if (!robot || robot.status !== "connected") return;
    if (action === "stop") return stopDrive(robot);
    const current = driveRef.current;
    if (current.robotId === robot.id && current.action === action) return;
    window.clearInterval(current.timer);
    driveRef.current = {
      robotId: robot.id,
      action,
      startedAt: performance.now(),
      keepalives: 0,
      timer: window.setInterval(() => keepDriving(robot), 250),
    };
    setPressed(action);
    updateRobot(robot.id, { mode: `Driving ${DRIVE_LABEL[action]}`, lastError: "" });
    try {
      noteLink(robot.id, (await robotApi(robot, "/robots/drive", driveBody(ordered(robot, action), powerRef.current))).link);
      if (driveRef.current.action === action) updateRobot(robot.id, { lastCommand: action });
    } catch (error) {
      if (driveRef.current.action !== action) return; // already released or switched
      endDriveLocally();
      driveFailed(robot, error, "Drive failed");
    }
  }

  function releaseDrive(robot, action) {
    if (robot && driveRef.current.robotId === robot.id && driveRef.current.action === action) stopDrive(robot);
  }

  async function stopDrive(robot) {
    if (!robot) return { ok: false, error: "no robot" };
    const held = driveRef.current;
    if (held.robotId === robot.id && held.action) {
      const seconds = ((performance.now() - held.startedAt) / 1000).toFixed(1);
      logActivity(robot.id, "info", `Released ${ACTION_TEXT[held.action]} after ${seconds} s (${held.keepalives} keepalives answered).`);
    }
    endDriveLocally();
    flash("stop");
    // the mode follows the car's confirmation from here (displayMode): never "Stopped" before it
    updateRobot(robot.id, { mode: "Stopping…", stopping: { seq: null } });
    try {
      const link = (await robotApi(robot, "/robots/drive", ordered(robot, "stop"))).link;
      noteLink(robot.id, link);
      // only links at least as new as this reply speak for this stop; a newer command may have taken over
      setRobots((current) =>
        current.map((r) => (r.id === robot.id && r.stopping ? { ...r, stopping: { seq: link?.seq ?? -Infinity }, lastCommand: "stop", lastError: "" } : r)),
      );
      followLink(robot);
      return { ok: true };
    } catch (error) {
      if (error.data?.superseded) return { ok: true, superseded: true }; // a newer command took over
      driveFailed(robot, error, "Stop failed");
      return { ok: false, error: error.message };
    }
  }

  /* STOP ALL: live-data's own stop for every connected car, and the Executive's cancel when a mission
     may be running (or the Executive cannot say). Both go out at once; the cars never wait on it. */
  function stopAll() {
    const cars = robotsRef.current.filter((robot) => robot.status === "connected");
    const id = Date.now();
    setStopRun({ id, at: new Date().toLocaleTimeString(), cars: cars.map((car) => ({ id: car.id, name: car.name })), results: {} });
    const report = (key, result) =>
      setStopRun((run) => (run?.id === id ? { ...run, results: { ...run.results, [key]: { ...run.results[key], ...result } } } : run));
    const cancelMission = () =>
      execPost("/cancel")
        .then(
          () => ({ ok: true }),
          (error) => (/no active mission/i.test(error.message) ? { ok: true, none: true } : { ok: false, error: error.message }),
        )
        .finally(() => exec.refresh());
    runStopAll({ cars, stopCar: stopDrive, cancelMission, missionActive: exec.reachable !== true || Boolean(activeMission(exec.state)), report });
  }

  async function sendGripper(robot, action, value) {
    if (!robot || robot.status !== "connected") return;
    flash(action);
    const position = action === "gripper_position";
    updateRobot(robot.id, { mode: position ? "Moving gripper…" : action === "gripper_open" ? "Opening gripper…" : "Closing gripper…", lastError: "" });
    try {
      await robotApi(robot, "/robots/control", position ? { ...ordered(robot, action), value } : ordered(robot, action));
      updateRobot(robot.id, { mode: position ? `Gripper at ${Math.round(value * 100)}%` : action === "gripper_open" ? "Gripper open" : "Gripper closed", lastCommand: action });
    } catch (error) {
      if (error.data?.superseded) return;
      driveFailed(robot, error, "Gripper command failed");
    }
  }

  async function setCamera(robot, settings) {
    const want = { quality: robot.camera?.quality ?? "low", depth: robot.camera?.depth ?? false, ...settings };
    try {
      const camera = await postCamera((path, payload) => robotApi(robot, path, payload), robot, want);
      updateRobot(robot.id, { camera: camera ?? robot.camera, lastError: "" });
    } catch (error) {
      updateRobot(robot.id, { lastError: error.message });
    }
  }

  /* Stop on anything that takes the operator's hands off the controls. */
  useEffect(() => {
    const robotOf = () => robotsRef.current.find((r) => r.id === driveRef.current.robotId);
    const stopIfDriving = () => {
      const robot = robotOf();
      if (robot) stopDrive(robot);
    };
    const onVisibility = () => document.visibilityState === "hidden" && stopIfDriving();
    const onPageHide = () => {
      const robot = robotOf();
      if (!robot || !navigator.sendBeacon) return;
      navigator.sendBeacon(
        `${API_BASE_URL}/robots/drive`,
        new Blob([JSON.stringify({ ...target(robot), action: "stop" })], { type: "text/plain" }),
      );
    };
    window.addEventListener("blur", stopIfDriving);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("blur", stopIfDriving);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Every 10 s, check that each connected robot still answers. A robot that went quiet is not left
     showing Live, and not dropped either: Hive starts reconnecting to it. */
  useEffect(() => {
    const timer = window.setInterval(() => {
      robotsRef.current
        .filter((r) => r.status === "connected")
        .forEach(async (robot) => {
          try {
            const status = await robotApi(robot, "/robots/status", target(robot));
            noteLink(robot.id, status.link);
            if (status.worker_replaced) {
              // the old worker died and this check started a new one, which stopped the car first
              if (driveRef.current.robotId === robot.id) endDriveLocally();
              updateRobot(robot.id, { mode: REPLACED_MODE });
              workerReplaced(robot);
            }
          } catch (error) {
            const current = robotsRef.current.find((r) => r.id === robot.id);
            if (current?.status !== "connected") return;
            keepConnecting(current, error.message);
          }
        });
    }, STATUS_CHECK_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------------------------------------------------------------- command log */

  async function sendCommand(text) {
    const command = text.trim();
    if (!command) return;

    const connected = robots.filter((robot) => robot.status === "connected");
    const id = `${Date.now()}-${Math.random()}`;
    setMessages((current) => [
      ...current,
      { role: "operator", text: command },
      {
        role: "agent",
        id,
        text: connected.length
          ? `Sending to ${connected.map((r) => r.name).join(", ")}…`
          : "No robots connected, so nothing was sent. The command is saved in this log.",
      },
    ]);
    if (!connected.length) return;

    const lines = await Promise.all(
      connected.map(async (robot) => {
        try {
          const result = await robotApi(robot, "/robots/command", { ...target(robot), text: command });
          return `${robot.name}: delivered on ${result.topic}.`;
        } catch (error) {
          return `${robot.name}: not delivered. ${error.message}`;
        }
      }),
    );
    setMessages((current) => current.map((m) => (m.id === id ? { ...m, text: lines.join("\n") } : m)));
  }

  /* ---------------------------------------------------------------- keyboard
   * Hold W A S D or the arrows to drive the selected robot; release to stop; Space stops at once.
   * Only while it is connected, never while typing, and never on key repeat. */
  useEffect(() => {
    function onKeyDown(event) {
      if (event.defaultPrevented || event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
      if (addingPreset !== null || removingRobotId !== null || editingRobotId !== null) return;
      const robot = selectedRobot;
      if (!robot || robot.status !== "connected") return;
      const el = event.target instanceof Element ? event.target : null;
      if (el?.closest("input, textarea, select, [contenteditable='true']")) return;
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      const action = KEY_TO_ACTION[key];
      if (!action) return;
      if (action === "stop" && el?.closest("button, a, [role='button'], summary")) return; // let Space press the focused control
      event.preventDefault();
      pressDrive(robot, action);
    }
    function onKeyUp(event) {
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      const action = KEY_TO_ACTION[key];
      if (!action || action === "stop" || driveRef.current.action !== action) return;
      event.preventDefault();
      releaseDrive(robotsRef.current.find((r) => r.id === driveRef.current.robotId), action);
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  });

  const robotActions = {
    onConnect: connectRobot,
    onDisconnect: disconnectRobot,
    onRemove: (robot) => setRemovingRobotId(robot.id),
    onEdit: (robot) => setEditingRobotId(robot.id),
  };
  const robotToRemove = robots.find((robot) => robot.id === removingRobotId);
  const robotToEdit = robots.find((robot) => robot.id === editingRobotId);
  const driveActions = (robot) => ({
    onPress: (action) => pressDrive(robot, action),
    onRelease: (action) => releaseDrive(robot, action),
    onStop: () => stopDrive(robot),
    onGripper: (action, value) => sendGripper(robot, action, value),
  });
  /* the dock floats on Fleet only: the robot page has the full pad, and the command log's composer
     owns the bottom of its screen */
  const showDock = view === "fleet" && selectedRobot?.status === "connected";
  const mission = activeMission(exec.state);
  const stopLines = stopRun && stopSummary(stopRun, robots, links);

  return (
    <div className="flex h-dvh flex-col bg-panel">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <Header
        live={liveStatus({ backend, exec })}
        mission={mission}
        models={exec.state ? { defaultPlanner: exec.state.default_planner, llm: exec.state.planners?.llm } : null}
        onCloseStop={() => setStopRun(null)}
        onNavigate={setView}
        onStopAll={stopAll}
        sim={isSimulated(exec.state?.gateway ?? exec.health?.gateway)}
        stop={stopLines && { at: stopRun.at, lines: stopLines }}
        view={view}
      />

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <main
          className={`min-w-0 flex-1 overflow-y-auto overscroll-contain ${showDock ? "pb-28" : ""}`}
          id="main"
          tabIndex={-1}
        >
          {view === "fleet" && (
            <FleetView
              backend={backend}
              exec={exec}
              links={links}
              messages={messages}
              onAdd={setAddingPreset}
              onChat={() => setView("log")}
              onOpen={openRobot}
              robots={robots}
              {...robotActions}
            />
          )}
          {view === "robot" && selectedRobot && (
            <RobotView
              activity={activity[selectedRobot.id] || []}
              key={selectedRobot.id}
              link={links[selectedRobot.id] ?? null}
              onBack={() => setView("fleet")}
              onClearActivity={() => setActivity((all) => ({ ...all, [selectedRobot.id]: [] }))}
              onCamera={(settings) => setCamera(selectedRobot, settings)}
              onLink={(link) => noteLink(selectedRobot.id, link)}
              onPower={setPower}
              onSelectTopic={setSelectedTopic}
              power={power}
              pressed={pressed}
              robot={selectedRobot}
              selectedTopic={selectedTopic}
              {...robotActions}
              {...driveActions(selectedRobot)}
            />
          )}
          {view === "robot" && !selectedRobot && <EmptyFleet onAdd={() => setAddingPreset("cameraRcCar")} />}
          {view === "map" && <MapView execState={exec.state} robots={robots} />}
          {view === "chat" && <ChatView exec={exec} robots={robots} />}
          {view === "log" && <CommandView messages={messages} onSend={sendCommand} robots={robots} />}
        </main>

        {showDock && (
          <DriveDock
            link={links[selectedRobot.id] ?? null}
            onOpen={() => openRobot(selectedRobot)}
            pressed={pressed}
            robot={selectedRobot}
            {...driveActions(selectedRobot)}
          />
        )}
      </div>

      {addingPreset !== null && (
        <AddRobotDialog
          initialPreset={addingPreset}
          onAdd={addRobot}
          onClose={() => setAddingPreset(null)}
          suggestedDomainId={nextDomainId(robots)}
          takenDomains={Object.fromEntries(robots.map((r) => [r.domainId, r.name]))}
        />
      )}

      {robotToRemove && (
        <RemoveRobotDialog onClose={() => setRemovingRobotId(null)} onConfirm={() => removeRobot(robotToRemove)} robot={robotToRemove} />
      )}

      {robotToEdit && (
        <ChangeAddressDialog
          onClose={() => setEditingRobotId(null)}
          onSave={(address) => changeAddress(robotToEdit, address)}
          robot={robotToEdit}
          suggestedDomainId={nextDomainId(robots)}
          takenDomains={Object.fromEntries(robots.filter((r) => r.id !== robotToEdit.id).map((r) => [r.domainId, r.name]))}
        />
      )}
    </div>
  );
}

/* ================================================================== stop all */

const MISSION_LINE = {
  none: { text: "no mission active", level: "muted" },
  sending: { text: "cancel requested…", level: "warn" },
  sent: { text: "cancel accepted", level: "live" },
};

/* The STOP ALL popover's lines: each car's report, then its live stop state once the stop went out. */
function stopSummary(run, robots, links) {
  const cars = run.cars.length
    ? run.cars.map(({ id, name }) => {
        const result = run.results[id] ?? { state: "sending" };
        const robot = robots.find((r) => r.id === id);
        const line = { key: id, label: robot?.name ?? name };
        if (result.state === "sending") return { ...line, text: "sending stop…", level: "warn" };
        if (result.state === "failed") return { ...line, text: `stop failed: ${result.error}`, level: "danger" };
        if (result.state === "superseded") return { ...line, text: "a newer command took over", level: "warn" };
        const mode = displayMode(robot, links[id] ?? null);
        return { ...line, text: mode.text || "stop sent", level: mode.level };
      })
    : [{ key: "cars", label: "Cars", text: "No connected cars", level: "muted" }];
  const mission = run.results.mission ?? { state: "sending" };
  const missionLine =
    mission.state === "failed" ? { text: `cancel failed: ${mission.error}`, level: "danger" } : (MISSION_LINE[mission.state] ?? MISSION_LINE.sending);
  return [...cars, { key: "mission", label: "Executive mission", ...missionLine }];
}

function EmptyFleet({ onAdd }) {
  return (
    <div className="grid min-h-full place-items-center p-8 text-center">
      <div>
        <HiveMark className="mx-auto text-coral" size={40} />
        <h1 className="mt-4 font-display text-4xl tracking-display">No robots yet</h1>
        <p className="mt-2 text-muted">Add one of the RC car presets to start driving.</p>
        <button className="btn btn-primary mt-6" onClick={onAdd} type="button">
          Add a robot
        </button>
      </div>
    </div>
  );
}

/* ================================================================== robot */

function RobotView({
  robot,
  selectedTopic,
  onSelectTopic,
  onConnect,
  onDisconnect,
  onRemove,
  onEdit,
  onPress,
  onRelease,
  onStop,
  onGripper,
  pressed,
  onBack,
  activity,
  onClearActivity,
  power,
  onPower,
  onCamera,
  link: savedLink = null,
  onLink,
}) {
  const preset = PRESET_ROBOTS[robot.preset];
  const listed = robot.status === "connected" ? robot.discovery?.topics ?? [] : [];
  /* R26: the one stream carries only what is viewed: the stage topic, /ros_data and /esp_data, and the
     Topics rows the operator watches. Changing any of these reopens it. */
  const [stage, setStage] = useState(null); // the Live view tab the operator chose (null: the first one)
  const [watched, setWatched] = useState([]);
  const onStage = currentTab(stageTabs(listed, selectedTopic), stage);
  const { topics, images } = watchSet({ listed, stage: onStage, watched });
  const live = useLiveStream({ base: API_BASE_URL, robot, topics, images, enabled: true });
  useEffect(() => {
    if (live.link) onLink?.(live.link);
  }, [live.link]); // eslint-disable-line react-hooks/exhaustive-deps
  const link = newerLink(savedLink, live.link); // R28: the newest from any route
  const mode = displayMode(robot, link);
  const selectTopic = (topic) => {
    onSelectTopic(topic);
    setStage(topic.name);
  };
  const toggleWatch = (name) => setWatched((all) => (all.includes(name) ? all.filter((n) => n !== name) : [...all, name]));
  return (
    <div>
      <div className="flex items-center justify-between gap-4 px-8 pt-5">
        <button
          className="inline-flex min-h-11 items-center gap-1.5 rounded-lg pr-3 text-[14px] font-medium text-muted transition-colors hover:text-ink"
          onClick={onBack}
          type="button"
        >
          <ChevronLeftIcon size={18} />
          Fleet
        </button>
        <div className="flex items-center gap-2">
          <button
            aria-label={`Change address of ${robot.name}`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-[14px] font-medium text-muted transition-colors hover:text-ink"
            onClick={() => onEdit(robot)}
            type="button"
          >
            <PencilIcon size={17} />
            Change address
          </button>
          <button
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg pl-3 text-[14px] font-medium text-muted transition-colors hover:text-danger"
            onClick={() => onRemove(robot)}
            type="button"
          >
            <TrashIcon size={17} />
            Remove robot
          </button>
        </div>
      </div>

      <header className="grid items-center gap-8 px-8 pb-8 pt-2 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <div
          className="relative aspect-[16/9] overflow-hidden rounded-2xl border border-line xl:aspect-[16/11]"
          style={{ background: stageBg(0.14) }}
        >
          <LiveView
            active={stage}
            base={API_BASE_URL}
            error={live.error}
            imageStats={live.imageStats}
            onActive={setStage}
            payloads={live.payloads}
            robot={robot}
            selectedTopic={selectedTopic}
            stale={live.stale}
          />
        </div>

        <div className="min-w-0 animate-enter">
          <p className="text-[14px] font-semibold text-muted">{preset.label}</p>
          <h1 className="mt-1 truncate font-display text-[clamp(44px,5.4vw,84px)] leading-[1.02] tracking-display">
            {robot.name}
          </h1>
          <p className="mt-3 max-w-[52ch] text-[16px] leading-relaxed text-muted">{describe(robot)}</p>
          <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-[14px] text-ink/80">
            <StatusBadge status={displayStatus(robot)} />
            <span aria-hidden="true">·</span>
            <span className="font-mono" translate="no">
              {robot.host || "no address"}
            </span>
            <span aria-hidden="true">·</span>
            <span className="font-mono">domain {robot.domainId}</span>
          </div>

          <div className="mt-6 flex items-center gap-4">
            <PlayButton onConnect={onConnect} onDisconnect={onDisconnect} robot={robot} size={56} />
            <div aria-live="polite" className="min-w-0">
              <p className="text-[15px] font-semibold">
                {robot.status === "connected"
                  ? "Connected"
                  : robot.status === "connecting"
                    ? robot.reconnecting
                      ? "Reconnecting…"
                      : "Connecting…"
                    : "Not connected"}
                <span
                  className={mode.level === "danger" ? "font-semibold text-danger" : mode.level === "warn" ? "font-normal text-warn" : "font-normal text-muted"}
                  data-testid="robot-mode"
                >
                  {" "}
                  · {mode.text}
                </span>
              </p>
              {robot.status === "connected" && robot.discovery && (
                <p className="mt-1 text-[13px] text-muted">
                  Found {robot.discovery.nodes.length} {robot.discovery.nodes.length === 1 ? "node" : "nodes"} and{" "}
                  {robot.discovery.topics.length} {robot.discovery.topics.length === 1 ? "topic" : "topics"} on domain {robot.domainId}
                </p>
              )}
              {robot.lastError && (
                <p className="mt-1 max-w-[60ch] break-words font-mono text-[13px] text-danger">{robot.lastError}</p>
              )}
              {robot.status === "connected" &&
                (robot.warnings || []).map((w) => (
                  <p className="mt-1 max-w-[60ch] text-[13px] text-warn" key={w}>
                    {w}
                  </p>
                ))}
            </div>
          </div>
        </div>
      </header>

      {/* kept as it was: drive controls beside topics and capabilities */}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 px-8 pb-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <section aria-labelledby="drive-title" className="rounded-xl bg-card p-5 sm:p-6">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="font-display text-[26px] tracking-title" id="drive-title">
              Drive controls
            </h2>
            <span className={`text-[13px] font-medium ${robot.status === "connected" ? "text-live" : "text-muted"}`}>
              {robot.status === "connected" ? "Live" : "Connect to drive"}
            </span>
          </div>
          <DrivePad
            link={link}
            onCamera={onCamera}
            onGripper={onGripper}
            onPower={onPower}
            onPress={onPress}
            onRelease={onRelease}
            onStop={onStop}
            power={power}
            pressed={pressed}
            robot={robot}
          />
        </section>

        <section aria-labelledby="topics-title">
          <h2 className="font-display text-[26px] tracking-title" id="topics-title">
            Topics
          </h2>
          <TopicList
            onSelectTopic={selectTopic}
            onToggleWatch={robot.status === "connected" ? toggleWatch : undefined}
            payloads={live.payloads}
            pinned={new Set([...ALWAYS_WATCHED, ...(onStage ? [onStage.name] : [])])}
            selectedTopic={selectedTopic}
            streamed={new Set([...topics, ...images])}
            topicErrors={live.topicErrors}
            topics={listed.length ? listed.filter((t) => !["/rosout", "/parameter_events"].includes(t.name)) : robot.topics}
            watched={watched}
          />

          <h3 className="mt-8 text-[15px] font-bold">Capabilities</h3>
          <ul className="mt-3 flex flex-wrap gap-2">
            {robot.capabilities.map((c) => (
              <li className="chip pointer-events-none" key={c.name}>
                {c.name}
                <span className="font-mono text-[13px] text-muted" translate="no">
                  {c.kind}
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <ActivityLog entries={activity} onClear={onClearActivity} />

      <PageFooter />
    </div>
  );
}

const ACTIVITY_STYLE = {
  ok: { mark: "✓", text: "text-live" },
  info: { mark: "·", text: "text-muted" },
  warn: { mark: "!", text: "text-warn" },
  error: { mark: "✕", text: "text-danger" },
};

const clock = (ms) =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) +
  `.${String(ms % 1000).padStart(3, "0")}`;

/* What Hive did with this robot, newest first: every request, how long it took, what came back, and
   each reconnect attempt. Kept for this page session only. */
function ActivityLog({ entries, onClear }) {
  return (
    <section aria-labelledby="activity-title" className="mx-8 mt-6 rounded-xl bg-card p-5 sm:p-6">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="font-display text-[26px] tracking-title" id="activity-title">
          Activity
        </h2>
        <button
          className="min-h-11 rounded-lg px-3 text-[13px] font-medium text-muted transition-colors hover:text-ink disabled:opacity-40"
          disabled={!entries.length}
          onClick={onClear}
          type="button"
        >
          Clear
        </button>
      </div>
      {entries.length ? (
        <ol aria-live="polite" className="mt-3 max-h-80 overflow-y-auto font-mono text-[13px] leading-relaxed">
          {entries.map((e) => {
            const s = ACTIVITY_STYLE[e.level] || ACTIVITY_STYLE.info;
            return (
              <li className="flex gap-3 border-t border-line/60 py-1.5 first:border-t-0" key={e.id}>
                <span className="flex-none text-faint">{clock(e.at)}</span>
                <span aria-hidden="true" className={`w-3 flex-none text-center ${s.text}`}>
                  {s.mark}
                </span>
                <span className={`min-w-0 break-words ${e.level === "info" ? "text-ink/80" : s.text}`}>{e.text}</span>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="mt-3 text-[14px] text-muted">Connect to see each request Hive sends to this robot and what comes back.</p>
      )}
    </section>
  );
}

/* Hold-to-drive on a button: pointer down starts, pointer up / cancel / lost capture stops; Space or
   Enter held on a focused button does the same. Pointer capture keeps the release coming to this
   button even if the cursor slides off it mid-drive. */
const holdToDrive = (action, onPress, onRelease) => ({
  onPointerDown: (event) => {
    if (event.button !== 0) return;
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // the pointer is already gone (a very quick tap): capture is a nicety, driving is not
    }
    onPress(action);
  },
  onPointerUp: () => onRelease(action),
  onPointerCancel: () => onRelease(action),
  onLostPointerCapture: () => onRelease(action),
  onKeyDown: (event) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      onPress(action);
    }
  },
  onKeyUp: (event) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      onRelease(action);
    }
  },
  onContextMenu: (event) => event.preventDefault(),
});

const QUALITIES = [
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
];

function DrivePad({ robot, onPress, onRelease, onStop, onGripper, pressed, power = 0.5, onPower, onCamera, link }) {
  const disabled = robot.status !== "connected";
  const drive = robot.controls.filter((c) => !c.action.startsWith("gripper"));
  const gripper = robot.controls.filter((c) => c.action.startsWith("gripper"));
  const at = (action) => drive.find((c) => c.action === action);
  const linkText = describeLink(link);
  const [gripperValue, setGripperValue] = useState(0.5);

  /* a plain render function, not a component: a component defined here would remount on every
     render and drop keyboard focus after each press */
  const direction = (action, area) => {
    const c = at(action);
    if (!c) return null;
    return (
      <button
        aria-keyshortcuts={c.key}
        aria-label={c.label}
        className="ctl relative size-[68px] bg-raised hover:bg-hover"
        data-pressed={pressed === action}
        disabled={disabled}
        style={{ gridArea: area, touchAction: "none" }}
        {...holdToDrive(action, onPress, onRelease)}
        title={`${c.label} (${c.key})`}
        type="button"
      >
        <c.Icon size={26} />
        <span className="absolute bottom-1.5 font-mono text-[13px] leading-none text-faint" aria-hidden="true">
          {c.key}
        </span>
      </button>
    );
  };

  return (
    <div className="mt-6">
      <div
        className="mx-auto grid w-fit gap-3"
        style={{ gridTemplateAreas: '". f ." "l s r" ". b ."', gridTemplateColumns: "repeat(3, auto)" }}
      >
        {direction("forward", "f")}
        {direction("left", "l")}
        <button
          aria-keyshortcuts="Space"
          aria-label="Stop"
          className="stop size-[68px] place-self-center"
          data-pressed={pressed === "stop"}
          disabled={disabled}
          onClick={onStop}
          style={{ gridArea: "s" }}
          title="Stop (Space)"
          type="button"
        >
          <StopIcon size={28} />
        </button>
        {direction("right", "r")}
        {direction("backward", "b")}
      </div>

      <label className="mx-auto mt-6 flex max-w-sm items-center gap-3 text-[14px] text-muted">
        <span className="w-14 flex-none">Power</span>
        <input
          aria-valuetext={`${Math.round(power * 100)}%`}
          className="min-w-0 flex-1 accent-coral"
          max={100}
          min={20}
          onChange={(event) => onPower?.(Number(event.target.value) / 100)}
          step={10}
          type="range"
          value={Math.round(power * 100)}
        />
        <span className="w-10 flex-none text-right font-mono text-ink">{Math.round(power * 100)}%</span>
      </label>

      {gripper.length > 0 && (
        <div className="mx-auto mt-6 max-w-sm">
          <div className="flex flex-wrap justify-center gap-3">
            {gripper.map((c) => (
              <button
                className="btn btn-secondary min-w-0 flex-1 basis-36 px-4"
                data-pressed={pressed === c.action}
                disabled={disabled || !gripperEnabled(robot)}
                key={c.action}
                onClick={() => onGripper(c.action)}
                title={robot.gripperInterface ? c.label : "No gripper interface found on this car"}
                type="button"
              >
                <c.Icon size={18} />
                {c.label}
              </button>
            ))}
          </div>
          {gripperSliderShown(robot) && (
            <label className="mt-3 flex items-center gap-3 text-[14px] text-muted">
              <span className="w-14 flex-none">Gripper</span>
              <input
                className="min-w-0 flex-1 accent-coral"
                disabled={disabled}
                max={100}
                min={0}
                onChange={(event) => setGripperValue(Number(event.target.value) / 100)}
                onKeyUp={() => onGripper("gripper_position", gripperValue)}
                onPointerUp={() => onGripper("gripper_position", gripperValue)}
                step={5}
                type="range"
                value={Math.round(gripperValue * 100)}
              />
              <span className="w-10 flex-none text-right font-mono text-ink">{Math.round(gripperValue * 100)}%</span>
            </label>
          )}
        </div>
      )}

      {robot.camera && (
        <div className="mx-auto mt-6 flex max-w-sm flex-wrap items-center gap-x-4 gap-y-2 text-[14px] text-muted">
          <span className="w-14 flex-none">Camera</span>
          <div aria-label="Camera quality" className="flex gap-1 rounded-full bg-raised p-1" role="group">
            {QUALITIES.map(([value, text]) => (
              <button
                aria-pressed={robot.camera.quality === value}
                className={`min-h-8 rounded-full px-3 text-[13px] font-medium transition-colors ${robot.camera.quality === value ? "bg-coral text-coral-ink" : "hover:text-ink"}`}
                disabled={disabled}
                key={value}
                onClick={() => onCamera?.({ quality: value })}
                type="button"
              >
                {text}
              </button>
            ))}
          </div>
          <button
            aria-pressed={Boolean(robot.camera.depth)}
            className={`min-h-8 rounded-full border px-3 text-[13px] font-medium transition-colors ${robot.camera.depth ? "border-coral text-coral" : "border-line hover:text-ink"}`}
            disabled={disabled}
            onClick={() => onCamera?.({ depth: !robot.camera.depth })}
            type="button"
          >
            Depth
          </button>
        </div>
      )}

      {!disabled && linkText.level === "danger" && (
        <p className="mt-6 rounded-lg bg-danger/15 px-4 py-3 text-center text-[14px] font-semibold text-danger" role="alert">
          {linkText.text}
        </p>
      )}
      {!disabled && linkText.level !== "danger" && (
        <p
          className={`mt-6 text-center text-[14px] ${linkText.level === "ok" ? "text-live" : linkText.level === "warn" ? "text-warn" : "text-muted"}`}
          role="status"
        >
          {linkText.text}
        </p>
      )}

      <p className="mt-3 text-center text-[14px] text-muted">
        {disabled ? (
          "Connect this robot to drive it."
        ) : robot.lastCommand ? (
          <>
            Last command <span className="font-mono text-ink">{robot.lastCommand}</span>
          </>
        ) : (
          <>
            Hold <kbd className="kbd">W</kbd> <kbd className="kbd">A</kbd> <kbd className="kbd">S</kbd>{" "}
            <kbd className="kbd">D</kbd> to drive, release to stop. <kbd className="kbd">Space</kbd> stops at once.
          </>
        )}
      </p>
    </div>
  );
}

const TOPIC_ERROR = {
  unknown_topic: "not on the car",
  use_mjpeg: "camera view only",
  watch_failed: "cannot be watched",
  too_many: "too many topics watched",
};
const TOPIC_COLUMNS = "grid-cols-[28px_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)]";

/* Spotify's tracklist: the selected topic plays its bars instead of its number. A row shows live values
   only while it is streamed (R26): the link topics and the one on stage always are; any other row has a
   watch toggle. Clicking a row opens it in the Live view. */
function TopicList({ topics, selectedTopic, onSelectTopic, payloads = {}, topicErrors = {}, streamed = new Set(), pinned = new Set(), watched = [], onToggleWatch }) {
  return (
    <div className="mt-4">
      <div aria-hidden="true" className="grid grid-cols-[minmax(0,1fr)_76px] gap-2 border-b border-line pb-2 pr-2 text-[13px] font-medium text-muted">
        <span className={`grid ${TOPIC_COLUMNS} gap-4 px-4`}>
          <span>#</span>
          <span>Topic</span>
          <span>Type</span>
          <span>Live</span>
        </span>
        <span className="text-center">Watch</span>
      </div>
      <ol className="mt-2 grid gap-0.5">
        {topics.map((topic, i) => {
          const active = selectedTopic?.name === topic.name;
          const live = streamed.has(topic.name);
          const isWatched = watched.includes(topic.name);
          return (
            <li className="grid grid-cols-[minmax(0,1fr)_76px] items-center gap-2 pr-2" key={topic.name}>
              <button
                aria-pressed={active}
                className={`grid w-full ${TOPIC_COLUMNS} items-center gap-4 rounded-md px-4 py-3 text-left transition-colors ${
                  active ? "bg-raised" : "hover:bg-card"
                }`}
                onClick={() => onSelectTopic(topic)}
                type="button"
              >
                <span className="font-mono text-[14px] text-muted">
                  {active ? <SignalBars className="[&>span]:bg-coral" /> : i + 1}
                </span>
                <span className={`truncate font-mono text-[14px] ${active ? "text-coral" : "text-ink"}`} translate="no">
                  {topic.name}
                </span>
                <span className="truncate font-mono text-[13px] text-muted" translate="no">
                  {topic.type}
                </span>
                <span className="truncate font-mono text-[13px] text-muted" translate="no">
                  {topic.kind && <span className="mr-2 text-faint">{topic.kind}</span>}
                  {live && payloads[topic.name]?.hz != null && <span className="mr-2">{Number(payloads[topic.name].hz).toFixed(1)} Hz</span>}
                  {live && topicErrors[topic.name] && <span className="text-warn">{TOPIC_ERROR[topicErrors[topic.name]] || topicErrors[topic.name]}</span>}
                  {live && topic.kind === "value" && payloads[topic.name] && <span className="text-ink">{compactValue(payloads[topic.name].data)}</span>}
                </span>
              </button>
              {topic.kind === "image" ? (
                <span className="text-center text-[12px] text-faint" title="Camera topics stream in the Live view">
                  view
                </span>
              ) : pinned.has(topic.name) ? (
                <span className="text-center text-[12px] font-medium text-live" title="Always streamed while this page is open">
                  live
                </span>
              ) : (
                onToggleWatch && (
                  <button
                    aria-label={`Watch ${topic.name}`}
                    aria-pressed={isWatched}
                    className={`min-h-8 rounded-full border px-2 text-[12px] font-medium transition-colors ${
                      isWatched ? "border-coral text-coral" : "border-line text-muted hover:text-ink"
                    }`}
                    onClick={() => onToggleWatch(topic.name)}
                    title={isWatched ? "Stop streaming this topic" : "Stream this topic's live values"}
                    type="button"
                  >
                    {isWatched ? "watching" : "watch"}
                  </button>
                )
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/* ================================================================== command log (Claude chat) */

function CommandView({ robots, messages, onSend }) {
  const [draft, setDraft] = useState("");
  const endRef = useRef(null);
  const connected = useMemo(() => robots.filter((r) => r.status === "connected"), [robots]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  function submit(event) {
    event?.preventDefault();
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-10 flex min-h-14 flex-wrap items-center justify-between gap-3 border-b border-line bg-panel/90 px-8 py-2 backdrop-blur">
        <h1 className="text-[16px] font-semibold">Command log</h1>
        {robots.length > 0 && (
          <ul aria-label="Robots" className="flex flex-wrap gap-2">
            {robots.map((r) => (
              <li className="inline-flex min-h-8 items-center gap-2 rounded-full border border-line px-3 text-[14px] font-medium text-muted" key={r.id}>
                <span className={`size-2 rounded-full ${r.status === "connected" ? "bg-live" : "bg-faint"}`} />
                {r.name}
                <span className="sr-only">{STATUS[displayStatus(r)]?.label}</span>
              </li>
            ))}
          </ul>
        )}
      </header>

      <div aria-live="polite" className="mx-auto w-full max-w-3xl flex-1 space-y-8 px-8 py-10" role="log">
        <div className="pb-2">
          <HiveMark className="text-coral" size={32} />
          <p className="mt-3 font-display text-[clamp(30px,3.4vw,42px)] leading-tight tracking-display">
            Write an instruction once.
          </p>
          <p className="mt-1 text-[16px] text-muted">Hive sends it to every connected robot on /hive/command.</p>
        </div>
        {messages.map((m, i) => (
          <div className={m.role === "operator" ? "flex justify-end" : "flex gap-3"} key={`${i}-${m.role}`}>
            {m.role === "agent" && <HiveMark className="mt-1 flex-none text-coral" size={22} />}
            <p
              className={
                m.role === "operator"
                  ? "max-w-[85%] whitespace-pre-line rounded-2xl bg-card px-4 py-2.5 text-[16px] leading-relaxed"
                  : "whitespace-pre-line font-display text-[18px] leading-relaxed"
              }
            >
              <span className="sr-only">{m.role === "operator" ? "You: " : "Hive: "}</span>
              {m.text}
            </p>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form className="sticky bottom-0 mx-auto w-full max-w-3xl bg-linear-to-t from-panel from-70% px-8 pb-6 pt-3" onSubmit={submit}>
        <div className="rounded-2xl border border-line-strong bg-card p-3 shadow-float transition-shadow focus-within:border-faint focus-within:ring-2 focus-within:ring-coral/50">
          <label className="sr-only" htmlFor="command-input">
            Command
          </label>
          <textarea
            autoComplete="off"
            className="block max-h-48 min-h-[56px] w-full resize-none bg-transparent px-2 py-1.5 text-[16px] leading-relaxed text-ink placeholder:text-faint focus-visible:outline-none"
            id="command-input"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
            }}
            placeholder="Tell the fleet what to do…"
            rows={2}
            value={draft}
          />
          <div className="mt-1 flex items-center justify-between gap-3 px-1">
            <p className="min-w-0 truncate text-[13px] text-faint">
              {connected.length ? `Acting on ${connected.map((r) => r.name).join(", ")}` : "No robots connected. Commands are logged only."}
            </p>
            {/* Claude's send button: a coral rounded square */}
            <button
              aria-label="Send command"
              className="grid size-9 flex-none place-items-center rounded-lg bg-coral text-coral-ink transition-colors hover:bg-coral-hover active:bg-coral-press disabled:opacity-40"
              disabled={!draft.trim()}
              title="Send command (Enter)"
              type="submit"
            >
              <SendIcon size={18} strokeWidth={2.4} />
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

/* ================================================================== drive dock (the Spotify accent)
 * A floating transport for the connected robot, so it can be driven from any page.
 */

function DriveDock({ robot, link = null, onPress, onRelease, onStop, onGripper, onOpen, pressed }) {
  const gripper = robot.controls.filter((c) => c.action.startsWith("gripper"));
  const order = ["left", "backward", "stop", "forward", "right"];
  /* Home has no banner, so the dock carries the stop's outcome: never "stopped" before the car confirms */
  const stop = stopUnconfirmed(robot, link) ? displayMode({ ...robot, stopping: { seq: -Infinity } }, link) : robot.stopping ? displayMode(robot, link) : null;
  const STOP_TEXT = { danger: "text-danger font-semibold", warn: "text-warn", ok: "text-live", muted: "text-muted" };
  return (
    <section
      aria-label={`Driving ${robot.name}`}
      className="absolute bottom-5 left-1/2 z-20 flex -translate-x-1/2 animate-fade items-center gap-4 rounded-full border border-line-strong bg-card/95 py-2 pl-2 pr-4 shadow-float backdrop-blur"
    >
      <button className="flex min-h-11 items-center gap-3 rounded-full pr-1 text-left" onClick={onOpen} type="button">
        <ModelThumb className="rounded-full" preset={robot.preset} size={44} />
        <span className="min-w-0">
          <span className="block max-w-[14ch] truncate text-[14px] font-semibold text-live">{robot.name}</span>
          <span className={`block whitespace-nowrap text-[13px] ${stop ? STOP_TEXT[stop.level] : "text-muted"}`} role={stop?.level === "danger" ? "alert" : undefined}>
            {stop ? stop.text : robot.lastCommand ? `Last: ${robot.lastCommand.replaceAll("_", " ")}` : "Ready to drive"}
          </span>
        </span>
      </button>
      <span aria-hidden="true" className="h-8 w-px bg-line-strong" />
      <div className="flex items-center gap-2">
        {order.map((action) => {
          const c = DRIVE_CONTROLS.find((d) => d.action === action);
          if (action === "stop") {
            return (
              <button
                aria-keyshortcuts="Space"
                aria-label="Stop"
                className="stop size-11"
                data-pressed={pressed === "stop"}
                key={action}
                onClick={onStop}
                title="Stop (Space)"
                type="button"
              >
                <StopIcon size={20} />
              </button>
            );
          }
          return (
            <button
              aria-keyshortcuts={c.key}
              aria-label={c.label}
              className="ctl size-11 hover:bg-raised"
              data-pressed={pressed === action}
              key={action}
              style={{ touchAction: "none" }}
              title={`${c.label} (${c.key})`}
              {...holdToDrive(action, onPress, onRelease)}
              type="button"
            >
              <c.Icon size={20} />
            </button>
          );
        })}
      </div>
      {gripper.length > 0 && (
        <>
          <span aria-hidden="true" className="h-8 w-px bg-line-strong" />
          {gripper.map((c) => (
            <button
              aria-label={c.label}
              className="ctl size-11 hover:bg-raised"
              data-pressed={pressed === c.action}
              key={c.action}
              onClick={() => onGripper(c.action)}
              title={c.label}
              type="button"
            >
              <c.Icon size={20} />
            </button>
          ))}
        </>
      )}
      <span aria-hidden="true" className="hidden items-center gap-1 xl:flex">
        <kbd className="kbd">W</kbd>
        <kbd className="kbd">A</kbd>
        <kbd className="kbd">S</kbd>
        <kbd className="kbd">D</kbd>
      </span>
    </section>
  );
}

/* ================================================================== add a robot */

function AddRobotDialog({ initialPreset, onAdd, onClose, suggestedDomainId, takenDomains = {} }) {
  const [form, setForm] = useState({
    name: "",
    preset: PRESET_ROBOTS[initialPreset] ? initialPreset : "cameraRcCar",
    description: "",
    host: "",
    domainId: suggestedDomainId,
  });
  const [domainError, setDomainError] = useState("");
  const dialogRef = useRef(null);
  const domainRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const preset = PRESET_ROBOTS[form.preset];

  /* once per open: focus moves into the dialog, Escape closes it, focus returns to the opener.
     onClose is read through a ref so a parent re-render never re-runs this mid-typing. */
  useEffect(() => {
    const opener = document.activeElement;
    dialogRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, []);

  function update(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
    if (name === "domainId") setDomainError("");
  }

  function submit(event) {
    event.preventDefault();
    // one robot per ROS domain: two robots on one domain would share topic names like /cmd_vel
    const owner = takenDomains[Number(form.domainId)];
    if (owner) {
      setDomainError(`Domain ${Number(form.domainId)} is already used by ${owner}. Pick another, such as ${suggestedDomainId}.`);
      domainRef.current?.focus();
      return;
    }
    onAdd({
      ...form,
      name: form.name.trim(),
      description: form.description.trim() || preset.summary,
      host: form.host.trim(),
      domainId: Number(form.domainId),
    });
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center overscroll-contain bg-sidebar/75 p-6 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <section
        aria-labelledby="add-robot-title"
        aria-modal="true"
        className="grid max-h-[94dvh] w-full max-w-[920px] animate-enter overflow-hidden rounded-2xl border border-line-strong bg-card shadow-float outline-none md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]"
        onMouseDown={(e) => e.stopPropagation()}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        {/* the model of the car being added, with its information */}
        <div className="relative hidden flex-col justify-between p-6 md:flex" style={{ background: stageBg(0.2, "var(--color-panel)") }}>
          <div>
            <p className="text-[13px] font-medium text-muted">Preview</p>
            <p className="mt-1 font-display text-[26px] leading-tight tracking-title">{form.name.trim() || preset.label}</p>
          </div>
          <ModelImage alt={modelAlt(form.preset)} className="mx-auto my-4 aspect-[3/2] w-full" key={form.preset} preset={form.preset} />
          <div className="space-y-2 text-[14px]">
            <p className="text-muted">{preset.summary}</p>
            <div className="flex flex-wrap gap-x-3 font-mono text-[13px] text-faint">
              <span translate="no">{form.host.trim() || "no address yet"}</span>
              <span>domain {form.domainId}</span>
            </div>
          </div>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-[14px] font-semibold text-muted">New robot</p>
              <h2 className="font-display text-[34px] leading-tight tracking-display" id="add-robot-title">
                Add a robot
              </h2>
            </div>
            <button aria-label="Close add a robot dialog" className="ctl size-10 hover:bg-raised" onClick={onClose} type="button">
              <CloseIcon size={18} />
            </button>
          </div>

          <form className="mt-5 grid gap-3.5 sm:grid-cols-2" onSubmit={submit}>
            <fieldset className="sm:col-span-2">
              <legend className="mb-2 text-[13px] font-medium text-muted">Preset</legend>
              <div className="grid grid-cols-3 gap-2">
                {PRESET_KEYS.map((key) => {
                  const p = PRESET_ROBOTS[key];
                  return (
                    <label
                      className="cursor-pointer rounded-xl bg-panel p-2 ring-1 ring-line transition hover:bg-raised has-checked:ring-2 has-checked:ring-coral has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-coral"
                      key={key}
                    >
                      <input checked={form.preset === key} className="sr-only" name="preset" onChange={update} type="radio" value={key} />
                      <span className="block aspect-[4/3] overflow-hidden rounded-lg" style={{ background: stageBg(0.14, "var(--color-card)") }}>
                        <ModelImage className="h-full w-full" preset={key} />
                      </span>
                      <span className="mt-2 block text-[13px] font-semibold leading-tight">{p.label}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <label className="grid gap-1.5 text-[13px] font-medium text-muted">
              Name
              <input
                autoComplete="off"
                className="field"
                name="name"
                onChange={update}
                placeholder="Scout 3…"
                required
                spellCheck={false}
                value={form.name}
              />
            </label>
            <label className="grid gap-1.5 text-[13px] font-medium text-muted">
              Robot IP address
              <input
                autoCapitalize="off"
                autoComplete="off"
                className="field font-mono"
                inputMode="url"
                name="host"
                onChange={update}
                pattern="[A-Za-z0-9_.:\-]+"
                placeholder="192.168.1.20…"
                required
                title="An IPv4 address or hostname, such as 192.168.1.20"
                spellCheck={false}
                translate="no"
                value={form.host}
              />
            </label>
            <label className="grid gap-1.5 text-[13px] font-medium text-muted">
              ROS domain ID
              <input
                aria-describedby={domainError ? "domain-error" : undefined}
                aria-invalid={domainError ? true : undefined}
                autoComplete="off"
                className={`field font-mono ${domainError ? "border-danger" : ""}`}
                inputMode="numeric"
                max="232"
                min="0"
                name="domainId"
                onChange={update}
                ref={domainRef}
                required
                type="number"
                value={form.domainId}
              />
              {domainError && (
                <span className="text-[13px] font-medium text-danger" id="domain-error" role="alert">
                  {domainError}
                </span>
              )}
            </label>
            <label className="grid gap-1.5 text-[13px] font-medium text-muted sm:col-span-2">
              Description
              <textarea
                autoComplete="off"
                className="field min-h-16 resize-y"
                name="description"
                onChange={update}
                placeholder="Short notes about this robot and its role…"
                value={form.description}
              />
            </label>

            <div className="mt-2 flex flex-wrap justify-end gap-3 sm:col-span-2">
              <button className="btn btn-secondary" onClick={onClose} type="button">
                Cancel
              </button>
              <button className="btn btn-primary" type="submit">
                Add robot
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}

function RemoveRobotDialog({ robot, onConfirm, onClose }) {
  const cancelRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const live = robot.status === "connected" || robot.status === "connecting";

  /* focus starts on Cancel so a stray Enter never removes a robot */
  useEffect(() => {
    const opener = document.activeElement;
    cancelRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center overscroll-contain bg-sidebar/75 p-6 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <section
        aria-describedby="remove-robot-body"
        aria-labelledby="remove-robot-title"
        aria-modal="true"
        className="w-full max-w-[460px] animate-enter rounded-2xl border border-line-strong bg-card p-6 shadow-float"
        onMouseDown={(e) => e.stopPropagation()}
        role="alertdialog"
      >
        <div className="flex items-center gap-4">
          <ModelThumb preset={robot.preset} size={56} />
          <div className="min-w-0">
            <p className="text-[14px] font-semibold text-muted">Remove robot</p>
            <h2 className="truncate font-display text-[28px] leading-tight tracking-title" id="remove-robot-title">
              {robot.name}
            </h2>
          </div>
        </div>
        <p className="mt-4 text-[15px] leading-relaxed text-muted" id="remove-robot-body">
          {live
            ? "Hive will stop and disconnect this robot, then remove it from your fleet. "
            : "This removes the robot from your fleet. "}
          Its name, address and domain {robot.domainId} are forgotten; add it again to bring it back.
        </p>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button className="btn btn-secondary" onClick={onClose} ref={cancelRef} type="button">
            Cancel
          </button>
          <button className="btn btn-danger" onClick={onConfirm} type="button">
            <TrashIcon size={17} />
            Remove robot
          </button>
        </div>
      </section>
    </div>
  );
}

const HOST_RE = /^[A-Za-z0-9_.:-]+$/;
const HOST_HINT = "Enter an IPv4 address or hostname, such as 192.168.1.20.";

/* Change a car's IP address and ROS domain in place (its IP changes with every Wi-Fi lease). Checked
   here, not by the browser, so every bad value gets the same plain message. */
function ChangeAddressDialog({ robot, onSave, onClose, suggestedDomainId, takenDomains = {} }) {
  const [form, setForm] = useState({ host: robot.host || "", domainId: String(robot.domainId) });
  const [error, setError] = useState(null); // { field: "host" | "domainId", text }
  const hostRef = useRef(null);
  const domainRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const live = robot.status === "connected" || robot.status === "connecting";

  /* focus starts in the IP field with its text selected, so a new address can be typed straight over it */
  useEffect(() => {
    const opener = document.activeElement;
    hostRef.current?.focus();
    hostRef.current?.select();
    const onKey = (e) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  function update(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
    setError(null);
  }

  function fail(field, text) {
    setError({ field, text });
    (field === "host" ? hostRef : domainRef).current?.focus();
  }

  function submit(event) {
    event.preventDefault();
    const host = form.host.trim();
    const domainText = form.domainId.trim();
    if (!HOST_RE.test(host)) return fail("host", HOST_HINT);
    if (!/^\d+$/.test(domainText) || !validDomain(Number(domainText))) {
      return fail("domainId", "The ROS domain ID is a whole number from 0 to 232.");
    }
    const domainId = Number(domainText);
    // one robot per ROS domain: two robots on one domain would share topic names like /cmd_vel
    const owner = takenDomains[domainId];
    if (owner) return fail("domainId", `Domain ${domainId} is already used by ${owner}. Pick another, such as ${suggestedDomainId}.`);
    onSave({ host, domainId });
  }

  const invalid = (field) => (error?.field === field ? { "aria-invalid": true, "aria-describedby": "address-error" } : {});

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center overscroll-contain bg-sidebar/75 p-6 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <section
        aria-labelledby="change-address-title"
        aria-modal="true"
        className="w-full max-w-[460px] animate-enter rounded-2xl border border-line-strong bg-card p-6 shadow-float"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
      >
        <div className="flex items-center gap-4">
          <ModelThumb preset={robot.preset} size={56} />
          <div className="min-w-0">
            <p className="truncate text-[14px] font-semibold text-muted">{robot.name}</p>
            <h2 className="font-display text-[28px] leading-tight tracking-title" id="change-address-title">
              Change address
            </h2>
          </div>
        </div>
        <p className="mt-4 text-[15px] leading-relaxed text-muted">
          {live
            ? `Saving a new address stops and disconnects ${robot.name} at its old address first. Press Connect afterwards to use the new one.`
            : "Use this when the car's IP address changes, for example after it rejoins the Wi-Fi."}
        </p>
        <form className="mt-5 grid gap-3.5 sm:grid-cols-2" noValidate onSubmit={submit}>
          <label className="grid gap-1.5 text-[13px] font-medium text-muted">
            Robot IP address
            <input
              {...invalid("host")}
              autoCapitalize="off"
              autoComplete="off"
              className={`field font-mono ${error?.field === "host" ? "border-danger" : ""}`}
              inputMode="url"
              name="host"
              onChange={update}
              placeholder="192.168.1.20…"
              ref={hostRef}
              spellCheck={false}
              translate="no"
              value={form.host}
            />
          </label>
          <label className="grid gap-1.5 text-[13px] font-medium text-muted">
            ROS domain ID
            <input
              {...invalid("domainId")}
              autoComplete="off"
              className={`field font-mono ${error?.field === "domainId" ? "border-danger" : ""}`}
              inputMode="numeric"
              max="232"
              min="0"
              name="domainId"
              onChange={update}
              ref={domainRef}
              type="number"
              value={form.domainId}
            />
          </label>
          {error && (
            <p className="text-[13px] font-medium text-danger sm:col-span-2" id="address-error" role="alert">
              {error.text}
            </p>
          )}
          <div className="mt-2 flex flex-wrap justify-end gap-3 sm:col-span-2">
            <button className="btn btn-secondary" onClick={onClose} type="button">
              Cancel
            </button>
            <button className="btn btn-primary" type="submit">
              Save address
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
