import React, { useEffect, useMemo, useRef, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_HIVE_API_URL || "http://127.0.0.1:8787/api";
const FLEET_STORAGE_KEY = "hive:fleet:manual-rc-v1";

const DRIVE_CONTROLS = [
  { action: "forward", label: "Forward" },
  { action: "left", label: "Left rotation" },
  { action: "right", label: "Right rotation" },
  { action: "backward", label: "Backward" },
  { action: "stop", label: "Stop" },
];

const GRIPPER_CONTROLS = [
  { action: "gripper_open", label: "Open gripper" },
  { action: "gripper_close", label: "Close gripper" },
];

const PRESET_ROBOTS = {
  cameraRcCar: {
    label: "Camera RC Car",
    summary: "RC car with /cmd_vel control and a Realsense camera node.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "Realsense camera", kind: "sensor" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/camera/raw", type: "sensor_msgs/msg/Image", viewer: "raw" },
    ],
    controls: DRIVE_CONTROLS,
  },
  lidarRcCar: {
    label: "LiDAR RC Car",
    summary: "RC car with /cmd_vel control and an RP LiDAR node.",
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
    summary: "RC car with /cmd_vel control and an open/close gripper.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "Gripper", kind: "actuator" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/gripper_command", type: "std_msgs/msg/String", viewer: "raw" },
    ],
    controls: [...DRIVE_CONTROLS, ...GRIPPER_CONTROLS],
  },
};

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

function loadFleet() {
  const saved = localStorage.getItem(FLEET_STORAGE_KEY);
  if (!saved) return [];

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed)) return [];

    const usedDomains = new Set();
    return parsed.map((robot) => {
      const presetRobot = robotFromPreset(robot);
      const domainId = Number(robot.domainId);
      const safeDomainId =
        Number.isInteger(domainId) && domainId >= 0 && domainId <= 232 && !usedDomains.has(domainId)
          ? domainId
          : nextDomainFromSet(usedDomains);
      usedDomains.add(safeDomainId);

      return {
        ...presetRobot,
        ...robot,
        domainId: safeDomainId,
        capabilities: presetRobot.capabilities,
        topics: presetRobot.topics,
        controls: presetRobot.controls,
      };
    });
  } catch {
    return [];
  }
}

async function postApi(path, payload) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.stderr || `Request failed with status ${response.status}`);
  }
  return data;
}

export default function App() {
  const [robots, setRobots] = useState(loadFleet);
  const [view, setView] = useState("fleet");
  const [selectedRobotId, setSelectedRobotId] = useState(robots[0]?.id);
  const [selectedTopic, setSelectedTopic] = useState(null);
  const [messages, setMessages] = useState([
    {
      role: "agent",
      text: "Hive is ready.",
    },
  ]);

  const selectedRobot = robots.find((robot) => robot.id === selectedRobotId) || robots[0];

  useEffect(() => {
    localStorage.setItem(FLEET_STORAGE_KEY, JSON.stringify(robots));
  }, [robots]);

  useEffect(() => {
    if (!selectedRobot && robots.length) setSelectedRobotId(robots[0].id);
  }, [robots, selectedRobot]);

  function addRobot(formData) {
    const robot = robotFromPreset({ ...formData, domainId: Number(formData.domainId) || nextDomainId(robots) });
    setRobots((current) => [...current, robot]);
    setSelectedRobotId(robot.id);
    setSelectedTopic(robot.topics[0]);
    setView("robot");
  }

  function updateRobot(id, patch) {
    setRobots((current) => current.map((robot) => (robot.id === id ? { ...robot, ...patch } : robot)));
  }

  async function connectRobot(robot) {
    updateRobot(robot.id, { status: "connecting", mode: "Connecting" });

    try {
      await postApi("/robots/connect", { ip: robot.host, domain_id: robot.domainId });
      updateRobot(robot.id, { status: "connected", mode: "Ready", lastError: "" });
      setSelectedTopic(robot.topics[0]);
    } catch (error) {
      updateRobot(robot.id, { status: "failed", mode: "Connection failed", lastError: error.message });
    }
  }

  async function disconnectRobot(robot) {
    try {
      await postApi("/robots/disconnect", { ip: robot.host, domain_id: robot.domainId });
    } catch {
      // The UI should still let the operator drop a stale local connection state.
    }
    updateRobot(robot.id, { status: "offline", mode: "Idle" });
  }

  async function sendRobotControl(robot, action) {
    updateRobot(robot.id, { mode: `Sending ${action.replaceAll("_", " ")}`, lastError: "" });

    try {
      await postApi("/robots/control", { ip: robot.host, domain_id: robot.domainId, action });
      updateRobot(robot.id, {
        mode: action === "stop" ? "Stopped" : action.replaceAll("_", " "),
        lastCommand: action,
        lastError: "",
      });
    } catch (error) {
      updateRobot(robot.id, { mode: "Command failed", lastError: error.message });
    }
  }

  function sendCommand(text) {
    const command = text.trim();
    if (!command) return;

    const connected = robots.filter((robot) => robot.status === "connected");
    const actionText = connected.length
      ? connected.map((robot) => `${robot.name}: executing "${command}".`).join("\n")
      : "No robots are connected. I recorded the command, but nothing was executed.";

    setMessages((current) => [
      ...current,
      { role: "operator", text: command },
      { role: "agent", text: actionText },
    ]);

    if (connected.length) {
      setRobots((current) =>
        current.map((robot) =>
          robot.status === "connected" ? { ...robot, mode: command.slice(0, 28) || "Active" } : robot,
        ),
      );
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">H</div>
          <div>
            <strong>Hive</strong>
          </div>
        </div>

        <nav className="nav">
          <button className={view === "fleet" ? "active" : ""} onClick={() => setView("fleet")}>
            Fleet
          </button>
          <button className={view === "command" ? "active" : ""} onClick={() => setView("command")}>
            Command
          </button>
        </nav>

        <div className="robot-list">
          <div className="eyebrow">Robots</div>
          {robots.map((robot) => (
            <button
              className={`robot-tab ${selectedRobotId === robot.id && view === "robot" ? "active" : ""}`}
              key={robot.id}
              onClick={() => {
                setSelectedRobotId(robot.id);
                setSelectedTopic(robot.topics[0]);
                setView("robot");
              }}
            >
              <span className={`status-dot ${robot.status}`} />
              <span>{robot.name}</span>
            </button>
          ))}
        </div>
      </aside>

      <main className="main">
        {view === "fleet" && (
          <FleetView
            robots={robots}
            onAddRobot={addRobot}
            onSelect={(robot) => {
              setSelectedRobotId(robot.id);
              setSelectedTopic(robot.topics[0]);
              setView("robot");
            }}
          />
        )}

        {view === "robot" && selectedRobot && (
          <RobotView
            robot={selectedRobot}
            selectedTopic={selectedTopic || selectedRobot.topics[0]}
            onSelectTopic={setSelectedTopic}
            onConnect={() => connectRobot(selectedRobot)}
            onDisconnect={() => disconnectRobot(selectedRobot)}
            onControl={(action) => sendRobotControl(selectedRobot, action)}
          />
        )}

        {view === "command" && <CommandView robots={robots} messages={messages} onSend={sendCommand} />}
      </main>
    </div>
  );
}

function FleetView({ robots, onAddRobot, onSelect }) {
  const [isAddingRobot, setIsAddingRobot] = useState(false);

  return (
    <section className="stack fleet-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Fleet overview</p>
          <h1>Fleet</h1>
        </div>
        <button className="add-robot-button" onClick={() => setIsAddingRobot(true)}>
          <span>+</span>
          Add Robot
        </button>
      </header>

      <section className="fleet-panel">
        <div className="grid fleet-grid">
          {robots.map((robot) => (
            <button className="robot-card" key={robot.id} onClick={() => onSelect(robot)}>
              <div className="card-header">
                <span className={`status-pill ${robot.status}`}>{robot.status}</span>
                <span>{PRESET_ROBOTS[robot.preset].label}</span>
              </div>
              <h2>{robot.name}</h2>
              <p>{robot.description}</p>
              <small>ROS domain {robot.domainId}</small>
              <div className="capability-row">
                {robot.capabilities.slice(0, 3).map((capability) => (
                  <span key={capability.name}>{capability.name}</span>
                ))}
              </div>
            </button>
          ))}
          {!robots.length && (
            <div className="empty-fleet">
              <strong>No robots yet</strong>
              <span>Add one of the RC car presets to start manual control.</span>
            </div>
          )}
        </div>
      </section>

      {isAddingRobot && (
        <div className="modal-backdrop" onMouseDown={() => setIsAddingRobot(false)}>
          <AddRobotForm
            suggestedDomainId={nextDomainId(robots)}
            onAddRobot={(robot) => {
              onAddRobot(robot);
              setIsAddingRobot(false);
            }}
            onClose={() => setIsAddingRobot(false)}
          />
        </div>
      )}
    </section>
  );
}

function AddRobotForm({ onAddRobot, onClose, suggestedDomainId }) {
  const [form, setForm] = useState({
    name: "",
    preset: "cameraRcCar",
    description: "",
    host: "",
    domainId: suggestedDomainId,
  });

  const preset = PRESET_ROBOTS[form.preset];

  function updateField(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  function submit(event) {
    event.preventDefault();
    onAddRobot({
      ...form,
      name: form.name.trim(),
      description: form.description.trim() || preset.summary,
      host: form.host.trim(),
      domainId: Number(form.domainId),
    });
    setForm({ name: "", preset: "cameraRcCar", description: "", host: "", domainId: suggestedDomainId });
  }

  return (
    <section className="modal-card" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
      <div className="section-title">
        <div>
          <p className="eyebrow">Add robot</p>
          <h2>Create a profile</h2>
        </div>
        <button className="close-button" type="button" onClick={onClose} aria-label="Close add robot dialog">
          x
        </button>
      </div>

      <form className="form-grid" onSubmit={submit}>
        <label>
          Name
          <input name="name" value={form.name} onChange={updateField} placeholder="Scout 3" required />
        </label>
        <label>
          Preset robot
          <select name="preset" value={form.preset} onChange={updateField}>
            {Object.entries(PRESET_ROBOTS).map(([key, value]) => (
              <option key={key} value={key}>
                {value.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Robot IP address
          <input name="host" value={form.host} onChange={updateField} placeholder="192.168.1.20" required />
        </label>
        <label>
          ROS domain ID
          <input
            min="0"
            max="232"
            name="domainId"
            type="number"
            value={form.domainId}
            onChange={updateField}
            required
          />
        </label>
        <label className="wide">
          Description
          <textarea
            name="description"
            value={form.description}
            onChange={updateField}
            placeholder="Short notes about this robot and its role."
          />
        </label>
        <button className="primary" type="submit">
          Add to fleet
        </button>
      </form>
    </section>
  );
}

function RobotView({ robot, selectedTopic, onSelectTopic, onConnect, onDisconnect, onControl }) {
  return (
    <section className="stack robot-page">
      <header className="robot-hero">
        <div>
          <p className="eyebrow">{PRESET_ROBOTS[robot.preset].label}</p>
          <h1>{robot.name}</h1>
          <p>{robot.description}</p>
        </div>
        <div className="connect-card">
          <span className={`status-pill ${robot.status}`}>{robot.status}</span>
          <strong>{robot.mode}</strong>
          <small>{robot.host}</small>
          <small>ROS domain {robot.domainId}</small>
          {robot.status === "connected" ? (
            <button onClick={onDisconnect}>Disconnect</button>
          ) : (
            <button className="primary" onClick={onConnect} disabled={robot.status === "connecting"}>
              {robot.status === "connecting" ? "Connecting..." : "Connect"}
            </button>
          )}
          {robot.lastError && <small className="error-text">{robot.lastError}</small>}
        </div>
      </header>

      <div className="two-column">
        <section className="panel profile-panel">
          <div className="section-title">
            <div>
              <p className="eyebrow">Profile</p>
              <h2>Capabilities</h2>
            </div>
            <span>
              {robot.host} · domain {robot.domainId}
            </span>
          </div>
          <div className="capability-list">
            {robot.capabilities.map((capability) => (
              <div key={capability.name}>
                <strong>{capability.name}</strong>
                <span>{capability.kind}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel topics-panel">
          <div className="section-title">
            <div>
              <p className="eyebrow">Manual control</p>
              <h2>Drive</h2>
            </div>
            <span>{robot.status === "connected" ? "connected" : "connect first"}</span>
          </div>
          <ManualControlPanel robot={robot} onControl={onControl} />
          <div className="section-title compact-title">
            <div>
              <p className="eyebrow">ROS 2</p>
              <h2>Topics</h2>
            </div>
          </div>
          <div className="topic-list">
            <div className="topic-list-inner">
              {robot.topics.map((topic) => (
                <button
                  className={selectedTopic?.name === topic.name ? "active" : ""}
                  key={topic.name}
                  onClick={() => onSelectTopic(topic)}
                >
                  <strong>{topic.name}</strong>
                  <span>{topic.type}</span>
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}

function ManualControlPanel({ robot, onControl }) {
  const repeatTimer = useRef(null);
  const gripperControls = robot.controls.filter((control) => control.action.startsWith("gripper"));
  const isDisabled = robot.status !== "connected";

  function clearRepeat() {
    if (repeatTimer.current) {
      window.clearInterval(repeatTimer.current);
      repeatTimer.current = null;
    }
  }

  function startDrive(action) {
    if (isDisabled) return;
    clearRepeat();
    onControl(action);
    if (action !== "stop") {
      repeatTimer.current = window.setInterval(() => onControl(action), 100);
    }
  }

  function stopDrive() {
    if (isDisabled) return;
    clearRepeat();
    onControl("stop");
  }

  function sendOneShot(action) {
    if (isDisabled) return;
    clearRepeat();
    onControl(action);
  }

  useEffect(() => clearRepeat, []);

  function driveButton(action, label, symbol) {
    const isStop = action === "stop";

    return (
      <button
        className={`control-button drive-button ${isStop ? "stop-button" : ""} ${action}`}
        disabled={isDisabled}
        key={action}
        onClick={() => isStop && sendOneShot("stop")}
        onPointerCancel={!isStop ? stopDrive : undefined}
        onPointerDown={() => (isStop ? sendOneShot("stop") : startDrive(action))}
        onPointerLeave={!isStop ? stopDrive : undefined}
        onPointerUp={!isStop ? stopDrive : undefined}
      >
        <span className="control-symbol">{symbol}</span>
        <span>{label}</span>
      </button>
    );
  }

  return (
    <div className="manual-control">
      <div className="drive-pad" aria-label="Manual drive controls">
        {driveButton("forward", "Forward", "↑")}
        {driveButton("left", "Rotate left", "↺")}
        {driveButton("stop", "Stop", "■")}
        {driveButton("right", "Rotate right", "↻")}
        {driveButton("backward", "Backward", "↓")}
      </div>

      {gripperControls.length > 0 && (
        <div className="gripper-controls">
          {gripperControls.map((control) => (
            <button
              className="control-button"
              disabled={isDisabled}
              key={control.action}
              onClick={() => sendOneShot(control.action)}
            >
              {control.label}
            </button>
          ))}
        </div>
      )}

      <small>
        {robot.lastCommand
          ? `Last command: ${robot.lastCommand.replaceAll("_", " ")}`
          : "Connect before sending manual commands."}
      </small>
    </div>
  );
}

function CommandView({ robots, messages, onSend }) {
  const [draft, setDraft] = useState("");
  const connectedNames = useMemo(
    () => robots.filter((robot) => robot.status === "connected").map((robot) => robot.name),
    [robots],
  );

  function submit(event) {
    event.preventDefault();
    onSend(draft);
    setDraft("");
  }

  return (
    <section className="command-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Fleet command</p>
          <h1>Command</h1>
        </div>
        <div className="chips">
          {robots.map((robot) => (
            <span className={robot.status === "connected" ? "chip connected" : "chip"} key={robot.id}>
              {robot.name}
            </span>
          ))}
        </div>
      </header>

      <div className="chat-panel">
        <div className="chat-log">
          {messages.map((message, index) => (
            <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
              <span>{message.role === "operator" ? "You" : "Hive"}</span>
              <p>{message.text}</p>
            </div>
          ))}
        </div>

        <form className="command-box" onSubmit={submit}>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Example: Inspect the lab entrance and keep the rover near the workbench."
          />
          <div>
            <small>
              {connectedNames.length
                ? `Acting immediately on: ${connectedNames.join(", ")}`
                : "No connected robots. Commands will be logged only."}
            </small>
            <button className="primary" type="submit">
              Send command
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
