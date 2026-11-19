import React, { useEffect, useMemo, useRef, useState } from "react";
import RobotViewer from "./RobotViewer.jsx";
import { PRESET_MODELS, snapshot } from "./robotModels.js";
import LiveView from "./live/LiveView.jsx";
import { useLiveStream } from "./live/useLiveStream.js";
import { postCamera } from "./live/api.js";
import { describeLink, displayMode, newerLink, stopUnconfirmed } from "./live/link.js";
import { connectPatch, driveBody, gripperEnabled, gripperSliderShown, keepaliveBody, notFoundHint } from "./live/carHelpers.js";
import { currentTab, stageTabs } from "./live/stage.js";
import { ALWAYS_WATCHED, watchSet } from "./live/watch.js";
import { compactValue } from "./viewers/ValuePanel.jsx";

const API_BASE_URL = import.meta.env.VITE_HIVE_API_URL || "http://127.0.0.1:8787/api";
const FLEET_STORAGE_KEY = "hive:fleet:manual-rc-v1";

/* ================================================================== icons
 * Drawn here: one stroke weight, currentColor, so every state recolours them.
 * No emoji and no characters standing in for icons.
 */

function Icon({ children, size = 20, filled = false, strokeWidth = 2, ...rest }) {
  return (
    <svg
      aria-hidden="true"
      fill={filled ? "currentColor" : "none"}
      focusable="false"
      height={size}
      stroke={filled ? "none" : "currentColor"}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
      {...rest}
    >
      {children}
    </svg>
  );
}

const HomeIcon = (p) => (
  <Icon {...p}>
    <path d="M3.5 10.5 12 3.5l8.5 7V20a.5.5 0 0 1-.5.5h-5v-6h-6v6H4a.5.5 0 0 1-.5-.5z" />
  </Icon>
);
const CommandIcon = (p) => (
  <Icon {...p}>
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v10a1.5 1.5 0 0 1-1.5 1.5H9l-5 3.5z" />
    <path d="m8 9 2.5 2L8 13M13 13h3" />
  </Icon>
);
const PlusIcon = (p) => (
  <Icon {...p}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
const PlayIcon = (p) => (
  <Icon filled {...p}>
    <path d="M8 5.3v13.4a1 1 0 0 0 1.5.86l11-6.7a1 1 0 0 0 0-1.72l-11-6.7A1 1 0 0 0 8 5.3z" />
  </Icon>
);
const PauseIcon = (p) => (
  <Icon filled {...p}>
    <rect height="15" rx="1.2" width="4.2" x="6.2" y="4.5" />
    <rect height="15" rx="1.2" width="4.2" x="13.6" y="4.5" />
  </Icon>
);
const StopIcon = (p) => (
  <Icon filled {...p}>
    <rect height="12" rx="2" width="12" x="6" y="6" />
  </Icon>
);
const ArrowUpIcon = (p) => (
  <Icon {...p}>
    <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
  </Icon>
);
const ArrowDownIcon = (p) => (
  <Icon {...p}>
    <path d="M12 5v14M18.5 12.5 12 19l-6.5-6.5" />
  </Icon>
);
const RotateLeftIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5" />
    <path d="M4 3.5v4h4" />
  </Icon>
);
const RotateRightIcon = (p) => (
  <Icon {...p}>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.4-5.5" />
    <path d="M20 3.5v4h-4" />
  </Icon>
);
const GripperOpenIcon = (p) => (
  <Icon {...p}>
    <path d="M6 4v5.5a6 6 0 0 0 12 0V4" />
    <path d="M12 15.5V20" />
  </Icon>
);
const GripperCloseIcon = (p) => (
  <Icon {...p}>
    <path d="M9 4v5.5a3 3 0 0 0 6 0V4" />
    <path d="M12 12.5V20" />
  </Icon>
);
const SendIcon = (p) => (
  <Icon {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Icon>
);
const CloseIcon = (p) => (
  <Icon {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Icon>
);
const TrashIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 6.5h15M9.5 6.5V4.5h5v2M6.5 6.5l.8 12.6a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4l.8-12.6M10 10.5v6M14 10.5v6" />
  </Icon>
);
const PencilIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 19.5h4l10.3-10.3a2 2 0 0 0 0-2.8l-1.2-1.2a2 2 0 0 0-2.8 0L4.5 15.5v4ZM13.5 6.5l4 4" />
  </Icon>
);
const ChevronLeftIcon = (p) => (
  <Icon {...p}>
    <path d="M14.5 6 8.5 12l6 6" />
  </Icon>
);
const Spinner = (p) => (
  <Icon className="animate-spin-slow" {...p}>
    <path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5" />
  </Icon>
);

/* The Hive mark: a hexagon cell with a cell inside it. */
function HiveMark({ size = 28, className = "" }) {
  return (
    <svg aria-hidden="true" className={className} height={size} viewBox="0 0 32 32" width={size}>
      <path
        d="M16 1.8 28.3 8.9v14.2L16 30.2 3.7 23.1V8.9zM16 7.4 8.6 11.7v8.6l7.4 4.3 7.4-4.3v-8.6zM16 12.2l3.3 1.9v3.8L16 19.8l-3.3-1.9v-3.8z"
        fill="currentColor"
        fillRule="evenodd"
      />
    </svg>
  );
}

/* Spotify's "now playing" bars, here meaning "connected". Static under reduced motion. */
function SignalBars({ className = "" }) {
  return (
    <span aria-hidden="true" className={`flex h-3.5 items-end gap-[2px] ${className}`}>
      {[0, 0.35, 0.7].map((delay) => (
        <span
          className="h-full w-[3px] origin-bottom animate-signal rounded-full bg-live"
          key={delay}
          style={{ animationDelay: `-${delay}s` }}
        />
      ))}
    </span>
  );
}

/* ================================================================== data */

const DRIVE_CONTROLS = [
  { action: "forward", label: "Forward", key: "W", Icon: ArrowUpIcon },
  { action: "left", label: "Rotate left", key: "A", Icon: RotateLeftIcon },
  { action: "stop", label: "Stop", key: "Space", Icon: StopIcon },
  { action: "right", label: "Rotate right", key: "D", Icon: RotateRightIcon },
  { action: "backward", label: "Backward", key: "S", Icon: ArrowDownIcon },
];

const GRIPPER_CONTROLS = [
  { action: "gripper_open", label: "Open gripper", Icon: GripperOpenIcon },
  { action: "gripper_close", label: "Close gripper", Icon: GripperCloseIcon },
];

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

/* Each preset's 3D model is real ROS / Gazebo CAD, assembled in src/robotModels.js. */
const PRESET_ROBOTS = {
  cameraRcCar: {
    label: "Camera RC Car",
    summary: "Drives on /cmd_vel and streams a Realsense camera.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "Realsense camera", kind: "sensor" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/camera/camera/color/image_raw", type: "sensor_msgs/msg/Image", viewer: "raw" },
    ],
    controls: DRIVE_CONTROLS,
  },
  lidarRcCar: {
    label: "LiDAR RC Car",
    summary: "Drives on /cmd_vel and scans the room with an RP LiDAR.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "RP LiDAR", kind: "2D lidar" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/scan", type: "sensor_msgs/msg/LaserScan", viewer: "raw" },
    ],
    controls: DRIVE_CONTROLS,
  },
  gripperRcCar: {
    label: "Gripper RC Car",
    summary: "Drives on /cmd_vel and opens or closes a gripper on command.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "Gripper", kind: "actuator" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/gripper_open", type: "std_msgs/msg/Bool", viewer: "raw" },
    ],
    controls: [...DRIVE_CONTROLS, ...GRIPPER_CONTROLS],
  },
};

const PRESET_KEYS = Object.keys(PRESET_ROBOTS);

const STATUS = {
  connected: { label: "Live", dot: "bg-live", text: "text-live" },
  offline: { label: "Offline", dot: "bg-faint", text: "text-muted" },
  connecting: { label: "Connecting…", dot: "bg-warn", text: "text-warn" },
  reconnecting: { label: "Reconnecting…", dot: "bg-warn", text: "text-warn" },
  failed: { label: "Connection failed", dot: "bg-danger", text: "text-danger" },
};

/* "connecting" covers both a first connect and winning back a lost connection; only the label differs */
const displayStatus = (robot) => (robot.status === "connecting" && robot.reconnecting ? "reconnecting" : robot.status);

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

function greeting(date = new Date()) {
  const hour = date.getHours();
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

const describe = (robot) => robot.description || PRESET_ROBOTS[robot.preset].summary;

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
  const [view, setView] = useState("home");
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
    if (view === "robot" && selectedRobot?.id === robot.id) setView("home");
  }

  /* A car's IP changes with the Wi-Fi lease, so its address and domain can be changed in place. A car
     that may be live is stopped and disconnected at its OLD address first (the backend's worker is
     keyed by address), then waits offline: the operator presses Connect to use the new one. */
  function changeAddress(robot, { host, domainId }) {
    setEditingRobotId(null);
    const current = robotsRef.current.find((r) => r.id === robot.id);
    if (!current || (current.host === host && current.domainId === domainId)) return;
    const live = current.status === "connected" || current.status === "connecting";
    stopRetrying(current.id);
    if (driveRef.current.robotId === current.id) endDriveLocally();
    window.clearTimeout(followRef.current[current.id]);
    if (live) robotApi(current, "/robots/disconnect", target(current)).catch(() => {});
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
    if (!robot) return;
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
    } catch (error) {
      if (error.data?.superseded) return; // a newer command took over
      driveFailed(robot, error, "Stop failed");
    }
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
  /* the dock floats on Home only: the robot page has the full pad, and the command log's composer
     owns the bottom of its screen */
  const showDock = view === "home" && selectedRobot?.status === "connected";

  return (
    <div className="flex h-dvh bg-panel">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <Sidebar
        backend={backend}
        onAdd={() => setAddingPreset("cameraRcCar")}
        onOpen={openRobot}
        robots={robots}
        selectedRobotId={view === "robot" ? selectedRobot?.id : null}
        setView={setView}
        view={view}
      />

      <div className="relative flex min-w-0 flex-1 flex-col">
        <main
          className={`min-w-0 flex-1 overflow-y-auto overscroll-contain ${showDock ? "pb-28" : ""}`}
          id="main"
          tabIndex={-1}
        >
          {view === "home" && (
            <HomeView
              links={links}
              messages={messages}
              onAdd={setAddingPreset}
              onOpen={openRobot}
              robots={robots}
              setView={setView}
              {...robotActions}
            />
          )}
          {view === "robot" && selectedRobot && (
            <RobotView
              activity={activity[selectedRobot.id] || []}
              key={selectedRobot.id}
              link={links[selectedRobot.id] ?? null}
              onBack={() => setView("home")}
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
          {view === "command" && <CommandView messages={messages} onSend={sendCommand} robots={robots} />}
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

/* ================================================================== CAD models */

/* A snapshot of the preset's real CAD model, drawn by the one shared offscreen renderer
   (robotModels.js). 720x480, so it sits in every frame without layout shift. */
function useSnapshot(preset) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let alive = true;
    setSrc(null);
    snapshot(preset).then((url) => alive && setSrc(url)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [preset]);
  return src;
}

function ModelImage({ preset, alt = "", className = "" }) {
  const src = useSnapshot(preset);
  if (!src) {
    return <span aria-hidden="true" className={`block animate-pulse rounded-lg bg-raised/40 ${className}`} />;
  }
  return (
    <img
      alt={alt}
      className={`animate-fade object-contain ${className}`}
      decoding="async"
      draggable={false}
      height={480}
      src={src}
      width={720}
    />
  );
}

function ModelThumb({ preset, size = 48, className = "" }) {
  return (
    <span
      className={`grid flex-none place-items-center overflow-hidden rounded-md ${className}`}
      style={{ height: size, width: size, background: stageBg(0.12, "var(--color-raised)") }}
    >
      <ModelImage className="h-full w-full scale-125" preset={preset} />
    </span>
  );
}

/* the floor light behind a model: a faint coral glow on the card */
const stageBg = (alpha = 0.16, base = "var(--color-card)") =>
  `radial-gradient(ellipse 70% 55% at 50% 66%, rgb(217 119 87 / ${alpha}), transparent 72%), ${base}`;

const modelAlt = (preset) => `CAD model: ${PRESET_MODELS[preset]?.label}`;

/* ================================================================== shared pieces */

function StatusBadge({ status, compact = false }) {
  const s = STATUS[status] || STATUS.offline;
  const label = compact && status === "failed" ? "Failed" : s.label;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[14px] font-semibold ${s.text}`}>
      {status === "connected" ? <SignalBars /> : <span className={`size-2 flex-none rounded-full ${s.dot}`} />}
      {label}
    </span>
  );
}

/* Spotify's play/pause, meaning connect/disconnect. While Hive keeps trying to connect, pressing it
   stops trying. */
function PlayButton({ robot, onConnect, onDisconnect, size = 48, className = "" }) {
  const connecting = robot.status === "connecting";
  const connected = robot.status === "connected";
  const iconSize = Math.round(size * 0.42);
  const label = connecting
    ? `Stop ${robot.reconnecting ? "reconnecting" : "connecting"} to ${robot.name}`
    : connected
      ? `Disconnect ${robot.name}`
      : `Connect ${robot.name}`;
  return (
    <button
      aria-label={label}
      className={`play ${className}`}
      onClick={(event) => {
        event.stopPropagation();
        connected || connecting ? onDisconnect(robot) : onConnect(robot);
      }}
      style={{ height: size, width: size }}
      title={label}
      type="button"
    >
      {connecting ? (
        <Spinner size={iconSize} />
      ) : connected ? (
        <PauseIcon size={iconSize} />
      ) : (
        <PlayIcon size={iconSize} style={{ marginLeft: size * 0.04 }} />
      )}
    </button>
  );
}

function SectionHead({ title, children }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <h2 className="font-display text-[28px] leading-tight tracking-title">{title}</h2>
      {children}
    </div>
  );
}

function PageFooter() {
  return (
    <footer className="mx-8 mt-10 border-t border-line py-8 text-[13px] text-faint">
      <p>
        Hive drives ROS 2 Jazzy robots over your local network. CAD: MR-Buggy3 by Rudis Laboratories (CC&nbsp;BY&nbsp;4.0, via Gazebo Fuel); Intel RealSense D435, ROBOTIS TurtleBot3 LDS-01 and OpenMANIPULATOR-X meshes (Apache&nbsp;2.0). Details in /models/CREDITS.md.
      </p>
    </footer>
  );
}

/* ================================================================== sidebar (Claude) */

function Sidebar({ robots, view, setView, selectedRobotId, onOpen, onAdd, backend }) {
  const items = [
    { id: "home", label: "Home", Icon: HomeIcon },
    { id: "command", label: "Command log", Icon: CommandIcon },
  ];
  return (
    <aside aria-label="Library" className="flex w-[296px] flex-none flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2.5 px-6 pb-4 pt-5">
        <HiveMark className="text-coral" size={28} />
        <span className="font-display text-[24px] leading-none tracking-title">Hive</span>
      </div>

      <nav aria-label="Sections" className="grid gap-0.5 px-3">
        {/* Claude's "New chat" row */}
        <button
          className="flex min-h-11 items-center gap-3 rounded-lg px-3 text-left text-[15px] font-semibold transition-colors hover:bg-card"
          onClick={onAdd}
          type="button"
        >
          <span className="grid size-7 place-items-center rounded-full bg-coral text-coral-ink">
            <PlusIcon size={16} strokeWidth={2.4} />
          </span>
          Add robot
        </button>
        {items.map(({ id, label, Icon: NavIcon }) => {
          const active = view === id;
          return (
            <button
              aria-current={active ? "page" : undefined}
              className={`flex min-h-11 items-center gap-3 rounded-lg px-3 text-left text-[15px] font-medium transition-colors ${
                active ? "bg-card text-ink" : "text-muted hover:bg-card hover:text-ink"
              }`}
              key={id}
              onClick={() => setView(id)}
              type="button"
            >
              <span className="grid size-7 place-items-center">
                <NavIcon size={20} />
              </span>
              {label}
            </button>
          );
        })}
      </nav>

      <div className="mt-5 flex min-h-0 flex-1 flex-col border-t border-line pt-1">
        <div className="flex items-center justify-between px-4 pt-4">
          <h2 className="text-[15px] font-bold text-muted">Your robots</h2>
          <button
            aria-label="Add a robot"
            className="ctl size-9 hover:bg-raised"
            onClick={onAdd}
            title="Add a robot"
            type="button"
          >
            <PlusIcon size={18} />
          </button>
        </div>
        <LibraryList className="min-h-0 flex-1" onAdd={onAdd} onOpen={onOpen} robots={robots} selectedRobotId={selectedRobotId} />
      </div>

      <BackendStatus backend={backend} />
    </aside>
  );
}

/* The sidebar footer: whether the backend answers, and whether ROS 2 is installed where it runs. */
function BackendStatus({ backend }) {
  const noRos = backend.state === "online" && backend.ros && !backend.ros.found;
  const dot = backend.state === "offline" ? "bg-danger" : noRos || backend.state === "checking" ? "bg-warn" : "bg-live";
  const title =
    backend.state === "offline"
      ? "Backend offline"
      : backend.state === "checking"
        ? "Checking backend…"
        : noRos
          ? "ROS 2 not found"
          : "Backend online · ROS 2 ready";
  const detail = backend.state === "offline" ? "npm run backend" : noRos ? backend.ros.setup : BACKEND_ORIGIN.replace(/^https?:\/\//, "");
  return (
    <div aria-live="polite" className="flex items-center gap-3 border-t border-line px-5 py-3.5">
      <span className={`size-2 flex-none rounded-full ${dot}`} />
      <span className="min-w-0">
        <span className="block text-[14px] font-medium">{title}</span>
        <span className="block truncate font-mono text-[13px] text-faint" title={detail} translate="no">
          {detail}
        </span>
      </span>
    </div>
  );
}

function LibraryList({ robots, selectedRobotId, onOpen, onAdd, className = "" }) {
  const [filter, setFilter] = useState("all");
  const shown = robots.filter((robot) =>
    filter === "all" ? true : filter === "live" ? robot.status === "connected" : robot.status !== "connected",
  );
  const filters = [
    ["all", "All"],
    ["live", "Live"],
    ["offline", "Offline"],
  ];

  return (
    <div className={`flex flex-col ${className}`}>
      {robots.length > 0 && (
        <div aria-label="Filter robots" className="flex gap-2 px-4 pt-3" role="group">
          {filters.map(([id, label]) => (
            <button aria-pressed={filter === id} className="chip" key={id} onClick={() => setFilter(id)} type="button">
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="mt-2 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {!robots.length && (
          <div className="m-2 rounded-xl bg-card p-5">
            <p className="font-bold">Add your first robot</p>
            <p className="mt-1 text-[14px] text-muted">Pick a preset, enter its IP address, and Hive gives it a ROS domain.</p>
            <button className="btn btn-primary mt-4 min-h-10 px-5 text-[14px]" onClick={onAdd} type="button">
              Add a robot
            </button>
          </div>
        )}

        {robots.length > 0 && !shown.length && (
          <p className="px-3 py-6 text-[14px] text-muted">{filter === "live" ? "No live robots." : "Every robot is live."}</p>
        )}

        <ul className="grid gap-0.5">
          {shown.map((robot) => {
            const active = robot.id === selectedRobotId;
            const live = robot.status === "connected";
            return (
              <li key={robot.id}>
                <button
                  aria-current={active ? "page" : undefined}
                  className={`flex w-full items-center gap-3 rounded-lg p-2 text-left transition-colors ${
                    active ? "bg-raised" : "hover:bg-card"
                  }`}
                  onClick={() => onOpen(robot)}
                  type="button"
                >
                  <ModelThumb preset={robot.preset} size={48} />
                  <span className="min-w-0 flex-1">
                    <span className={`block truncate text-[15px] font-semibold ${live ? "text-live" : "text-ink"}`}>
                      {robot.name}
                    </span>
                    <span className="block truncate text-[13px] text-muted">
                      {PRESET_ROBOTS[robot.preset].label} · domain {robot.domainId}
                    </span>
                  </span>
                  {live && <SignalBars className="mr-1" />}
                  {robot.status === "failed" && <span className="mr-1.5 size-2 rounded-full bg-danger" />}
                  {robot.status === "connecting" && <Spinner className="mr-1 animate-spin-slow text-warn" size={16} />}
                  <span className="sr-only">{STATUS[displayStatus(robot)]?.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/* ================================================================== home */

function HomeView({ robots, links = {}, messages, onAdd, onOpen, setView, onConnect, onDisconnect, onRemove, onEdit }) {
  const live = robots.filter((robot) => robot.status === "connected").length;
  const domains = robots.map((robot) => robot.domainId).filter(Number.isInteger);
  const domainRange = domains.length
    ? Math.min(...domains) === Math.max(...domains)
      ? `${Math.min(...domains)}`
      : `${Math.min(...domains)}–${Math.max(...domains)}`
    : "from 42";

  return (
    <div>
      <section
        className="relative overflow-hidden px-8 pb-12 pt-14"
        style={{
          background:
            "radial-gradient(ellipse 70% 90% at 90% 0%, rgb(217 119 87 / 0.20), transparent 70%), linear-gradient(180deg, rgb(217 119 87 / 0.10) 0%, transparent 90%)",
        }}
      >
        <div className="animate-enter">
          <p className="text-[15px] font-medium text-muted">{greeting()}</p>
          <h1 className="mt-3 max-w-[14ch] font-display text-[clamp(44px,5vw,80px)] leading-[1.02] tracking-display">
            Fleet control for ROS&nbsp;2 robots
          </h1>
          <p className="mt-5 max-w-[62ch] text-[17px] leading-relaxed text-muted">
            Add a robot by its IP address, connect, and drive it from this laptop.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <button className="btn btn-primary" onClick={() => onAdd("cameraRcCar")} type="button">
              <PlusIcon size={18} />
              Add a robot
            </button>
            <button className="btn btn-secondary" onClick={() => setView("command")} type="button">
              Send a command
            </button>
          </div>
          <dl className="mt-9 flex flex-wrap gap-x-10 gap-y-4">
            {[
              ["Robots", robots.length],
              ["Live now", live],
              ["ROS domains", domainRange],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-[13px] font-medium text-muted">{label}</dt>
                <dd className="mt-1 font-display text-[34px] leading-none tracking-title tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <div className="space-y-14 px-8">
        <section>
          <SectionHead title="Your fleet">
            {robots.length > 0 && (
              <span className="text-[14px] text-muted">
                {robots.length} {robots.length === 1 ? "robot" : "robots"} · {live} live
              </span>
            )}
          </SectionHead>
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(272px,1fr))]">
            {robots.map((robot) => (
              <RobotCard
                key={robot.id}
                link={links[robot.id] ?? null}
                onConnect={onConnect}
                onDisconnect={onDisconnect}
                onEdit={onEdit}
                onOpen={onOpen}
                onRemove={onRemove}
                robot={robot}
              />
            ))}
            <AddCard onAdd={() => onAdd("cameraRcCar")} />
          </div>
        </section>

        <section>
          <SectionHead title="Start from a preset" />
          <div className="grid gap-4 sm:grid-cols-3">
            {PRESET_KEYS.map((key) => (
              <PresetCard key={key} onAdd={() => onAdd(key)} preset={key} />
            ))}
          </div>
        </section>

        <CommandCallout messages={messages} onOpen={() => setView("command")} />
      </div>

      <PageFooter />
    </div>
  );
}

/* A robot's card: its car's 3D model with its information. The card opens the robot; the play button
   (Spotify) connects it. */
function RobotCard({ robot, link = null, onOpen, onConnect, onDisconnect, onRemove, onEdit }) {
  const preset = PRESET_ROBOTS[robot.preset];
  const live = robot.status === "connected";
  const danger = stopUnconfirmed(robot, link);
  return (
    <article
      className={`group relative overflow-hidden rounded-2xl border bg-card transition-colors ${danger ? "border-danger" : "border-line hover:border-line-strong"}`}
    >
      <button aria-label={`Open ${robot.name}`} className="absolute inset-0 z-0 rounded-2xl" onClick={() => onOpen(robot)} type="button" />
      <div className="model pointer-events-none relative aspect-[16/10]" style={{ background: stageBg(0.16) }}>
        <ModelImage alt={modelAlt(robot.preset)} className="absolute inset-[4%] h-[92%] w-[92%]" preset={robot.preset} />
        <span className="absolute left-3 top-3 flex flex-col items-start gap-1.5">
          <span className="rounded-full bg-panel/80 px-2.5 py-1 backdrop-blur">
            <StatusBadge compact status={displayStatus(robot)} />
          </span>
          {danger && (
            <span
              className="rounded-full bg-danger px-2.5 py-1 text-[13px] font-semibold text-panel"
              role="status"
              title="The car has not confirmed the last stop: it may still be moving"
            >
              Stop not confirmed
            </span>
          )}
        </span>
        <button
          aria-label={`Remove ${robot.name}`}
          className="ctl reveal pointer-events-auto absolute right-3 top-3 z-10 size-9 bg-panel/80 backdrop-blur hover:text-danger"
          onClick={(event) => {
            event.stopPropagation();
            onRemove(robot);
          }}
          title={`Remove ${robot.name}`}
          type="button"
        >
          <TrashIcon size={17} />
        </button>
        <button
          aria-label={`Change address of ${robot.name}`}
          className="ctl reveal pointer-events-auto absolute right-14 top-3 z-10 size-9 bg-panel/80 backdrop-blur hover:text-ink"
          onClick={(event) => {
            event.stopPropagation();
            onEdit(robot);
          }}
          title="Change IP address or domain"
          type="button"
        >
          <PencilIcon size={17} />
        </button>
        <PlayButton
          className="reveal pointer-events-auto absolute bottom-3 right-3 z-10"
          onConnect={onConnect}
          onDisconnect={onDisconnect}
          robot={robot}
          size={44}
        />
      </div>
      <div className="pointer-events-none relative border-t border-line p-4">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className={`truncate text-[17px] font-semibold ${live ? "text-live" : "text-ink"}`}>{robot.name}</h3>
          <span className="flex-none text-[13px] text-faint">{preset.label}</span>
        </div>
        <p className="mt-1 truncate text-[14px] text-muted">{describe(robot)}</p>
        <div className="mt-3 flex flex-wrap gap-x-3 font-mono text-[13px] text-faint">
          <span translate="no">{robot.host || "no address"}</span>
          <span>domain {robot.domainId}</span>
        </div>
      </div>
    </article>
  );
}

function AddCard({ onAdd }) {
  return (
    <button
      className="group grid min-h-[260px] place-items-center rounded-2xl border border-dashed border-line-strong text-center transition-colors hover:border-faint hover:bg-card/50"
      onClick={onAdd}
      type="button"
    >
      <span>
        <span className="mx-auto grid size-12 place-items-center rounded-full bg-raised text-ink transition-colors group-hover:bg-coral group-hover:text-coral-ink">
          <PlusIcon size={22} />
        </span>
        <span className="mt-3 block text-[16px] font-semibold">Add a robot</span>
        <span className="mt-1 block text-[14px] text-muted">Its CAD model appears here</span>
      </span>
    </button>
  );
}

/* A preset's card: the model it adds, and what it carries. */
function PresetCard({ preset, onAdd }) {
  const p = PRESET_ROBOTS[preset];
  return (
    <button
      aria-label={`Add ${p.label}`}
      className="group overflow-hidden rounded-2xl border border-line bg-card text-left transition-colors hover:border-line-strong"
      onClick={onAdd}
      type="button"
    >
      <span className="model relative block aspect-[16/11]" style={{ background: stageBg(0.18) }}>
        <ModelImage alt={modelAlt(preset)} className="absolute inset-[4%] h-[92%] w-[92%]" preset={preset} />
        <span className="play reveal absolute bottom-3 right-3 size-11" aria-hidden="true">
          <PlusIcon size={20} />
        </span>
      </span>
      <span className="block border-t border-line p-4">
        <span className="block text-[17px] font-semibold">{p.label}</span>
        <span className="mt-1 line-clamp-2 block text-[14px] leading-snug text-muted">{p.summary}</span>
      </span>
    </button>
  );
}

/* Claude's full-bleed coral callout, showing the real log rather than a picture of one. */
function CommandCallout({ messages, onOpen }) {
  const recent = messages.slice(-2);
  return (
    <section className="grid items-center gap-8 overflow-hidden rounded-2xl bg-coral p-7 text-coral-ink md:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)] md:p-10">
      <div>
        <h2 className="font-display text-[clamp(30px,3.4vw,46px)] leading-[1.05] tracking-display">
          A command log for the whole fleet
        </h2>
        <p className="mt-3 max-w-[42ch] text-[17px] font-medium leading-relaxed">
          Write an instruction once and Hive sends it to every connected robot.
        </p>
        <button className="btn mt-6 bg-sidebar text-ink hover:bg-raised" onClick={onOpen} type="button">
          Send a command
        </button>
      </div>
      <div className="rounded-xl bg-cream p-5 text-cream-ink shadow-paper">
        <p className="text-[13px] font-medium text-cream-muted">Latest in the log</p>
        <div className="mt-3 space-y-3">
          {recent.map((m, i) => (
            <div className={m.role === "operator" ? "flex justify-end" : "flex gap-2.5"} key={`${i}-${m.text}`}>
              {m.role === "agent" && <HiveMark className="mt-0.5 flex-none text-coral-press" size={18} />}
              <p
                className={
                  m.role === "operator"
                    ? "max-w-[85%] rounded-2xl rounded-br-md bg-cream-3 px-3.5 py-2 text-[15px]"
                    : "line-clamp-3 whitespace-pre-line text-[15px] leading-relaxed"
                }
              >
                {m.text}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
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
          Home
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
