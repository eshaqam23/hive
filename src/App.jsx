import React, { useEffect, useMemo, useState } from "react";

const MORPHOLOGIES = {
  quadcopter: {
    label: "Quadcopter",
    summary: "Aerial robot for inspection and scouting.",
    capabilities: [
      { name: "Forward camera", kind: "sensor" },
      { name: "IMU", kind: "sensor" },
      { name: "Altitude hold", kind: "flight" },
      { name: "Velocity control", kind: "control" },
    ],
    topics: [
      { name: "/camera/front/image_raw", type: "sensor_msgs/Image", viewer: "image" },
      { name: "/imu/data", type: "sensor_msgs/Imu", viewer: "readout" },
      { name: "/battery", type: "sensor_msgs/BatteryState", viewer: "readout" },
      { name: "/cmd_vel", type: "geometry_msgs/Twist", viewer: "raw" },
    ],
  },
  manipulator: {
    label: "Manipulator",
    summary: "Stationary arm for grasping and placement tasks.",
    capabilities: [
      { name: "Joint state feedback", kind: "sensor" },
      { name: "Gripper", kind: "actuator" },
      { name: "Trajectory control", kind: "control" },
    ],
    topics: [
      { name: "/joint_states", type: "sensor_msgs/JointState", viewer: "readout" },
      { name: "/gripper/state", type: "hive_msgs/GripperState", viewer: "readout" },
      { name: "/arm_controller/trajectory", type: "trajectory_msgs/JointTrajectory", viewer: "raw" },
    ],
  },
  mobileManipulator: {
    label: "Mobile manipulator",
    summary: "RC platform with an arm for mobile manipulation.",
    capabilities: [
      { name: "Drive base", kind: "mobility" },
      { name: "Manipulator", kind: "actuator" },
      { name: "Wrist camera", kind: "sensor" },
      { name: "Gripper", kind: "actuator" },
    ],
    topics: [
      { name: "/wrist_camera/image_raw", type: "sensor_msgs/Image", viewer: "image" },
      { name: "/joint_states", type: "sensor_msgs/JointState", viewer: "readout" },
      { name: "/battery", type: "sensor_msgs/BatteryState", viewer: "readout" },
      { name: "/cmd_vel", type: "geometry_msgs/Twist", viewer: "raw" },
    ],
  },
  rcCar: {
    label: "RC car",
    summary: "Wheeled ground robot for fast indoor navigation.",
    capabilities: [
      { name: "Throttle and steering", kind: "control" },
      { name: "Forward camera", kind: "sensor" },
      { name: "Odometry", kind: "localization" },
    ],
    topics: [
      { name: "/camera/front/image_raw", type: "sensor_msgs/Image", viewer: "image" },
      { name: "/odom", type: "nav_msgs/Odometry", viewer: "readout" },
      { name: "/cmd_drive", type: "ackermann_msgs/AckermannDrive", viewer: "raw" },
    ],
  },
  treadedCar: {
    label: "Treaded car",
    summary: "Tracked ground robot for rougher terrain.",
    capabilities: [
      { name: "Tracked drive", kind: "mobility" },
      { name: "Depth camera", kind: "sensor" },
      { name: "Battery telemetry", kind: "sensor" },
    ],
    topics: [
      { name: "/depth/image_raw", type: "sensor_msgs/Image", viewer: "image" },
      { name: "/battery", type: "sensor_msgs/BatteryState", viewer: "readout" },
      { name: "/cmd_vel", type: "geometry_msgs/Twist", viewer: "raw" },
    ],
  },
};

const seedRobots = [
  robotFromPreset({
    id: "aero-1",
    name: "Aero One",
    morphology: "quadcopter",
    description: "Demo quadcopter for visual inspection and fleet command previews.",
    repoUrl: "https://github.com/example/aero-one-ros2",
    host: "192.168.1.42",
    status: "connected",
  }),
  robotFromPreset({
    id: "momo-2",
    name: "MoMo",
    morphology: "mobileManipulator",
    description: "Mobile manipulator on an RC base with a wrist camera and gripper.",
    repoUrl: "https://github.com/example/momo-ros2",
    host: "192.168.1.51",
    status: "offline",
  }),
  robotFromPreset({
    id: "track-7",
    name: "Track Seven",
    morphology: "treadedCar",
    description: "Tracked rover demo profile for rough terrain monitoring.",
    repoUrl: "https://github.com/example/track-seven-ros2",
    host: "192.168.1.77",
    status: "offline",
  }),
];

const topicSamples = {
  image: "Simulated camera stream",
  readout: "battery: 87% | signal: strong | latency: 24ms",
  raw: "{ linear: { x: 0.0, y: 0.0 }, angular: { z: 0.0 } }",
};

function robotFromPreset({ id, name, morphology, description, repoUrl, host, status = "offline" }) {
  const presetKey = MORPHOLOGIES[morphology] ? morphology : "quadcopter";
  const preset = MORPHOLOGIES[presetKey];
  return {
    id: id || makeId(),
    name: name || "Unnamed robot",
    morphology: presetKey,
    description: description || preset.summary,
    repoUrl: repoUrl || "",
    host: host || "localhost",
    status,
    mode: status === "connected" ? "Ready" : "Idle",
    capabilities: preset.capabilities,
    topics: preset.topics,
  };
}

function makeId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `robot-${Date.now()}-${Math.round(Math.random() * 1000)}`;
}

function loadFleet() {
  const saved = localStorage.getItem("hive:fleet");
  if (!saved) return seedRobots;

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed) || !parsed.length) return seedRobots;

    return parsed.map((robot) => ({
      ...robotFromPreset(robot),
      ...robot,
      capabilities: Array.isArray(robot.capabilities) ? robot.capabilities : robotFromPreset(robot).capabilities,
      topics: Array.isArray(robot.topics) ? robot.topics : robotFromPreset(robot).topics,
    }));
  } catch {
    return seedRobots;
  }
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
    localStorage.setItem("hive:fleet", JSON.stringify(robots));
  }, [robots]);

  useEffect(() => {
    if (!selectedRobot && robots.length) setSelectedRobotId(robots[0].id);
  }, [robots, selectedRobot]);

  function addRobot(formData) {
    const robot = robotFromPreset(formData);
    setRobots((current) => [...current, robot]);
    setSelectedRobotId(robot.id);
    setSelectedTopic(robot.topics[0]);
    setView("robot");
  }

  function updateRobot(id, patch) {
    setRobots((current) => current.map((robot) => (robot.id === id ? { ...robot, ...patch } : robot)));
  }

  function connectRobot(robot) {
    updateRobot(robot.id, { status: "connecting", mode: "Connecting" });

    window.setTimeout(() => {
      updateRobot(robot.id, { status: "connected", mode: "Ready" });
      setSelectedTopic(robot.topics[0]);
    }, 900);
  }

  function disconnectRobot(robot) {
    updateRobot(robot.id, { status: "offline", mode: "Idle" });
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
                <span>{MORPHOLOGIES[robot.morphology].label}</span>
              </div>
              <h2>{robot.name}</h2>
              <p>{robot.description}</p>
              <div className="capability-row">
                {robot.capabilities.slice(0, 3).map((capability) => (
                  <span key={capability.name}>{capability.name}</span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </section>

      {isAddingRobot && (
        <div className="modal-backdrop" onMouseDown={() => setIsAddingRobot(false)}>
          <AddRobotForm
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

function AddRobotForm({ onAddRobot, onClose }) {
  const [form, setForm] = useState({
    name: "",
    morphology: "quadcopter",
    description: "",
    repoUrl: "",
    host: "",
  });

  const preset = MORPHOLOGIES[form.morphology];

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
      repoUrl: form.repoUrl.trim(),
      host: form.host.trim() || "localhost",
    });
    setForm({ name: "", morphology: "quadcopter", description: "", repoUrl: "", host: "" });
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
          Morphology
          <select name="morphology" value={form.morphology} onChange={updateField}>
            {Object.entries(MORPHOLOGIES).map(([key, value]) => (
              <option key={key} value={key}>
                {value.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          GitHub ROS 2 workspace
          <input name="repoUrl" value={form.repoUrl} onChange={updateField} placeholder="https://github.com/..." />
        </label>
        <label>
          Host
          <input name="host" value={form.host} onChange={updateField} placeholder="192.168.1.20 or robot.local" />
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

function RobotView({ robot, selectedTopic, onSelectTopic, onConnect, onDisconnect }) {
  return (
    <section className="stack robot-page">
      <header className="robot-hero">
        <div>
          <p className="eyebrow">{MORPHOLOGIES[robot.morphology].label}</p>
          <h1>{robot.name}</h1>
          <p>{robot.description}</p>
        </div>
        <div className="connect-card">
          <span className={`status-pill ${robot.status}`}>{robot.status}</span>
          <strong>{robot.mode}</strong>
          <small>{robot.host}</small>
          {robot.status === "connected" ? (
            <button onClick={onDisconnect}>Disconnect</button>
          ) : (
            <button className="primary" onClick={onConnect} disabled={robot.status === "connecting"}>
              {robot.status === "connecting" ? "Connecting..." : "Connect"}
            </button>
          )}
        </div>
      </header>

      <div className="two-column">
        <section className="panel profile-panel">
          <div className="section-title">
            <div>
              <p className="eyebrow">Profile</p>
              <h2>Capabilities</h2>
            </div>
            {robot.repoUrl ? (
              <a href={robot.repoUrl} target="_blank" rel="noreferrer">
                View repo
              </a>
            ) : (
              <span>No repo linked</span>
            )}
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
              <p className="eyebrow">Live ROS 2</p>
              <h2>Topics</h2>
            </div>
            <span>{robot.status === "connected" ? "subscribed" : "offline preview"}</span>
          </div>
          <div className="topic-layout">
            <div className="topic-list">
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
            <TopicViewer topic={selectedTopic || robot.topics[0]} status={robot.status} />
          </div>
        </section>
      </div>
    </section>
  );
}

function TopicViewer({ topic, status }) {
  if (!topic) return null;

  if (topic.viewer === "image") {
    return (
      <div className="viewer camera-view">
        <div className="camera-frame">
          <span>{status === "connected" ? "Live" : "Preview"}</span>
          <strong>{topicSamples.image}</strong>
        </div>
        <small>{topic.name}</small>
      </div>
    );
  }

  return (
    <div className="viewer">
      <p className="eyebrow">{topic.viewer === "readout" ? "Telemetry" : "Message"}</p>
      <h3>{topic.name}</h3>
      <pre>{topicSamples[topic.viewer]}</pre>
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
