// Pure simulation helpers: command parsing + per-robot motion state.
// Kept free of React so the same logic can drive both the 3D twin and real robots.

export const ROBOT_TYPES = {
  camera: {
    label: "Camera RC Car",
    summary: "RC car with /cmd_vel control and a Realsense camera node.",
    color: "#2f8f9d",
    verbs: ["forward", "back", "left", "right", "spin", "scan", "stop"],
  },
  lidar: {
    label: "LiDAR RC Car",
    summary: "RC car with /cmd_vel control and an RP LiDAR node.",
    color: "#7a5cc2",
    verbs: ["forward", "back", "left", "right", "spin", "scan", "stop"],
  },
  gripper: {
    label: "Gripper RC Car",
    summary: "RC car with /cmd_vel control and an open/close gripper.",
    color: "#d9822b",
    verbs: ["forward", "back", "left", "right", "spin", "open", "close", "wave", "stop"],
  },
};

// Simulated robots move fast so demos feel snappy. Connected robots use the real
// speeds the backend publishes on /cmd_vel (0.5 m/s, 1.0 rad/s) so the 3D twin
// stays in step with the physical robot.
const SIM_SPEEDS = { linSpeed: 1.6, turnSpeed: 140 }; // units/s, deg/s
const LIVE_SPEEDS = { linSpeed: 0.5, turnSpeed: 180 / Math.PI }; // 1 rad/s

// Verb -> backend control action (see CONTROL_COMMANDS in backend/server.py).
// scan / wave have no backend action, so they only play in the 3D twin.
const BACKEND_ACTION = {
  forward: "forward",
  back: "backward",
  left: "left",
  right: "right",
  spin: "left",
  open: "gripper_open",
  close: "gripper_close",
};
const MOVING_VERBS = new Set(["forward", "back", "left", "right", "spin"]);

const ALIASES = {
  forward: "forward",
  fwd: "forward",
  ahead: "forward",
  go: "forward",
  back: "back",
  backward: "back",
  reverse: "back",
  left: "left",
  right: "right",
  spin: "spin",
  rotate: "spin",
  scan: "scan",
  look: "scan",
  open: "open",
  close: "close",
  grab: "close",
  grip: "close",
  release: "open",
  wave: "wave",
  stop: "stop",
  halt: "stop",
};

const DEFAULT_AMOUNT = { forward: 2, back: 2, left: 90, right: 90, spin: 360 };

// "forward 3, left 90, wave, then open" -> [{ raw, verb, amount }]
export function parseCommand(text) {
  const parts = text
    .toLowerCase()
    .split(/,|;|\n|\bthen\b|\band\b/)
    .map((part) => part.trim())
    .filter(Boolean);

  return parts.map((part) => {
    const words = part.split(/\s+/);
    // "go" is only a fallback so "go left 90" means left, not forward.
    const verbWord = words.find((word) => ALIASES[word] && word !== "go") ?? words.find((word) => ALIASES[word]);
    const verb = verbWord ? ALIASES[verbWord] : null;
    const numberMatch = part.match(/-?\d+(\.\d+)?/);
    const requested = numberMatch ? Math.abs(parseFloat(numberMatch[0])) : DEFAULT_AMOUNT[verb];
    // Keep robots on the stage: cap distance and rotation.
    const limit = verb === "forward" || verb === "back" ? 12 : 720;
    const amount = requested === undefined ? requested : Math.min(requested, limit);
    return { raw: part, verb, amount };
  });
}

function durationOf(step, sim) {
  switch (step.verb) {
    case "forward":
    case "back":
      return step.amount / sim.linSpeed;
    case "left":
    case "right":
    case "spin":
      return step.amount / sim.turnSpeed;
    case "scan":
      return 2.4;
    case "wave":
      return 1.8;
    case "open":
    case "close":
      return 0.7;
    default:
      return 0.2;
  }
}

export function createRobotSim(index = 0) {
  const col = index % 3;
  const row = Math.floor(index / 3);
  return {
    x: (col - 1) * 3,
    z: row * 3 - 1.5,
    heading: 0, // radians, 0 faces -z
    wheelSpin: 0,
    scanT: 0, // 0..1 progress of the scan animation
    waveT: 0, // 0..1 progress of the wave animation
    grip: 1, // 1 open, 0 closed
    queue: [],
    current: null,
    elapsed: 0,
    busy: false,
    live: false, // true when mirrored to a real robot through the backend
    outbox: [], // backend control actions waiting to be sent
    ...SIM_SPEEDS,
  };
}

// Switch a robot between simulation speeds and real-robot speeds.
export function setLive(sim, live) {
  sim.live = live;
  Object.assign(sim, live ? LIVE_SPEEDS : SIM_SPEEDS);
}

export function enqueue(sim, steps) {
  for (const step of steps) {
    if (step.verb === "stop") {
      sim.queue.length = 0;
      sim.current = null;
      sim.outbox.push("stop");
      continue;
    }
    sim.queue.push({ ...step, duration: durationOf(step, sim) });
  }
}

// Advances one robot by dt seconds. Returns true if its busy flag changed.
export function stepRobot(sim, dt) {
  const wasBusy = sim.busy;

  if (!sim.current && sim.queue.length) {
    sim.current = sim.queue.shift();
    sim.elapsed = 0;
    const action = BACKEND_ACTION[sim.current.verb];
    if (action) sim.outbox.push(action);
  }

  const step = sim.current;
  if (step) {
    const slice = Math.min(dt, step.duration - sim.elapsed);
    sim.elapsed += slice;
    const progress = step.duration > 0 ? sim.elapsed / step.duration : 1;

    switch (step.verb) {
      case "forward":
      case "back": {
        const dir = step.verb === "forward" ? 1 : -1;
        sim.x += -Math.sin(sim.heading) * sim.linSpeed * slice * dir;
        sim.z += -Math.cos(sim.heading) * sim.linSpeed * slice * dir;
        sim.wheelSpin += sim.linSpeed * slice * dir * 3;
        break;
      }
      case "left":
      case "right":
      case "spin": {
        const dir = step.verb === "right" ? -1 : 1;
        sim.heading += ((sim.turnSpeed * Math.PI) / 180) * slice * dir;
        sim.wheelSpin += slice * 4 * dir;
        break;
      }
      case "scan":
        sim.scanT = progress;
        break;
      case "wave":
        sim.waveT = progress;
        break;
      case "open":
        sim.grip = Math.min(1, sim.grip + slice / step.duration);
        break;
      case "close":
        sim.grip = Math.max(0, sim.grip - slice / step.duration);
        break;
      default:
        break;
    }

    if (sim.elapsed >= step.duration - 1e-6) {
      if (step.verb === "scan") sim.scanT = 0;
      if (step.verb === "wave") sim.waveT = 0;
      // Real robots keep driving until told to stop.
      if (MOVING_VERBS.has(step.verb)) sim.outbox.push("stop");
      sim.current = null;
    }
  }

  sim.busy = Boolean(sim.current || sim.queue.length);
  return wasBusy !== sim.busy;
}
