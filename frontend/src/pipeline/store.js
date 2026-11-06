import { create } from "zustand";
import { addEdge, applyEdgeChanges, applyNodeChanges, reconnectEdge } from "@xyflow/react";
import { postApi } from "./api";
import { ROBOT_TYPES, createRobotSim, enqueue, parseCommand, setLive, stepRobot } from "./sim";

const STORAGE_KEY = "hive:pipeline:v1";

let counter = 0;
const nextId = (prefix) => `${prefix}-${++counter}`;

// Mutable sim state lives outside React state so the 3D loop can update it
// every frame without re-rendering. `busy` is mirrored into the store on change.
export const simState = {};

// Per-robot promise chain so backend calls (e.g. "forward" then "stop") stay ordered.
const chains = {};

function nextDomainId(nodes) {
  const used = new Set(
    nodes.filter((node) => node.type === "robot").map((node) => Number(node.data.domainId)),
  );
  let domainId = 42;
  while (used.has(domainId)) domainId += 1;
  return domainId;
}

function robotNode(config, index, position) {
  const id = nextId("robot");
  simState[id] = createRobotSim(index);
  return {
    id,
    type: "robot",
    position,
    data: {
      name: config.name || `${ROBOT_TYPES[config.robotType].label.split(" ")[0]} ${index + 1}`,
      robotType: config.robotType,
      host: config.host || "",
      domainId: config.domainId ?? null,
      description: config.description || "",
    },
  };
}

function commandNode(position, text = "forward 3, left 90, forward 2") {
  return { id: nextId("command"), type: "command", position, data: { text, lastRun: null } };
}

function initialGraph() {
  const cmd = commandNode({ x: 40, y: 120 });
  const a = robotNode({ robotType: "camera" }, 0, { x: 420, y: 20 });
  const b = robotNode({ robotType: "gripper" }, 1, { x: 420, y: 250 });
  return {
    nodes: [cmd, a, b],
    edges: [
      { id: "e-a", source: cmd.id, target: a.id },
      { id: "e-b", source: cmd.id, target: b.id },
    ],
  };
}

// Restore robots + wires saved in localStorage (like the old fleet did).
function loadSaved() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (!saved || !Array.isArray(saved.nodes) || !Array.isArray(saved.edges)) return null;
    const valid = saved.nodes.every(
      (node) => node.type === "command" || (node.type === "robot" && ROBOT_TYPES[node.data?.robotType]),
    );
    if (!valid) return null;

    let robotIndex = 0;
    for (const node of saved.nodes) {
      const suffix = Number(String(node.id).split("-")[1]);
      if (suffix > counter) counter = suffix;
      if (node.type === "robot") simState[node.id] = createRobotSim(robotIndex++);
    }
    return saved;
  } catch {
    return null;
  }
}

const initial = loadSaved() ?? initialGraph();

function saveGraph({ nodes, edges }) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        nodes: nodes.map(({ id, type, position, data }) => ({
          id,
          type,
          position,
          data: type === "command" ? { ...data, lastRun: null } : data,
        })),
        edges: edges.map(({ id, source, target, sourceHandle, targetHandle }) => ({
          id,
          source,
          target,
          sourceHandle,
          targetHandle,
        })),
      }),
    );
  } catch {
    // Storage unavailable: the pipeline still works, it just won't persist.
  }
}

export const usePipeline = create((set, get) => ({
  nodes: initial.nodes,
  edges: initial.edges,
  busy: {}, // robotId -> boolean
  logs: {}, // robotId -> string
  conn: {}, // robotId -> { status: "offline"|"connecting"|"connected"|"failed", error? }
  selectedRobotId: null,

  onNodesChange: (changes) => set({ nodes: applyNodeChanges(changes, get().nodes) }),
  onEdgesChange: (changes) => set({ edges: applyEdgeChanges(changes, get().edges) }),
  onConnect: (connection) => set({ edges: addEdge(connection, get().edges) }),

  // Drag a wire's end onto another box to re-route it.
  onReconnect: (oldEdge, connection) => set({ edges: reconnectEdge(oldEdge, connection, get().edges) }),

  removeEdge: (id) => set({ edges: get().edges.filter((edge) => edge.id !== id) }),

  // Boxes deleted with the keyboard: stop live robots and free their sim state.
  disposeNodes: (deleted) => {
    for (const node of deleted) {
      const sim = simState[node.id];
      if (sim?.live) {
        enqueue(sim, [{ verb: "stop" }]);
        flushOutbox(node.id, node);
      }
      delete simState[node.id];
    }
  },

  suggestedDomainId: () => nextDomainId(get().nodes),

  addRobot: (config) => {
    const robotCount = get().nodes.filter((node) => node.type === "robot").length;
    const node = robotNode(config, robotCount, {
      x: 420 + (robotCount % 2) * 40,
      y: 20 + robotCount * 130,
    });
    set({ nodes: [...get().nodes, node], selectedRobotId: node.id });
  },

  addCommand: () => {
    const count = get().nodes.filter((node) => node.type === "command").length;
    set({ nodes: [...get().nodes, commandNode({ x: 40, y: 120 + count * 200 }, "wave")] });
  },

  removeNode: (id) => {
    const sim = simState[id];
    if (sim?.live) {
      enqueue(sim, [{ verb: "stop" }]);
      flushOutbox(id); // best-effort stop before the node disappears
    }
    delete simState[id];
    set({
      nodes: get().nodes.filter((node) => node.id !== id),
      edges: get().edges.filter((edge) => edge.source !== id && edge.target !== id),
    });
  },

  updateNodeData: (id, patch) =>
    set({
      nodes: get().nodes.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...patch } } : node)),
    }),

  selectRobot: (id) => set({ selectedRobotId: id }),

  // ── Backend connection (same /robots/connect call the old Robot page used) ──
  connectRobot: async (id) => {
    const node = get().nodes.find((item) => item.id === id);
    if (!node?.data.host) return;
    set({ conn: { ...get().conn, [id]: { status: "connecting" } } });
    try {
      await postApi("/robots/connect", { ip: node.data.host, domain_id: node.data.domainId });
      if (simState[id]) setLive(simState[id], true);
      set({ conn: { ...get().conn, [id]: { status: "connected" } } });
    } catch (error) {
      set({ conn: { ...get().conn, [id]: { status: "failed", error: error.message } } });
    }
  },

  disconnectRobot: (id) => {
    const sim = simState[id];
    if (sim?.live) {
      enqueue(sim, [{ verb: "stop" }]);
      flushOutbox(id);
    }
    if (sim) setLive(sim, false);
    set({ conn: { ...get().conn, [id]: { status: "offline" } } });
  },

  reportError: (id, message) => set({ logs: { ...get().logs, [id]: `Backend: ${message}` } }),

  // Send a command node's text to every robot wired to it.
  runCommand: (commandId) => {
    const { nodes, edges } = get();
    const command = nodes.find((node) => node.id === commandId);
    if (!command) return;

    const targets = edges
      .filter((edge) => edge.source === commandId)
      .map((edge) => nodes.find((node) => node.id === edge.target))
      .filter((node) => node?.type === "robot");

    get().dispatch(command.data.text, targets);
    get().updateNodeData(commandId, { lastRun: targets.length ? Date.now() : "no-target" });
  },

  // Command bar: send to the selected robot, or to every robot if none is selected.
  broadcast: (text) => {
    const { nodes, selectedRobotId } = get();
    const robots = nodes.filter((node) => node.type === "robot");
    const targets = selectedRobotId ? robots.filter((node) => node.id === selectedRobotId) : robots;
    get().dispatch(text, targets);
    // One feedback line per robot so the command bar can show what happened.
    return targets.map((node) => ({ name: node.data.name, message: get().logs[node.id] }));
  },

  // Parse text and queue the steps the target robots support.
  dispatch: (text, targets) => {
    const steps = parseCommand(text);
    const logs = { ...get().logs };
    for (const target of targets) {
      const supported = ROBOT_TYPES[target.data.robotType].verbs;
      const accepted = steps.filter((step) => step.verb && supported.includes(step.verb));
      const rejected = steps.filter((step) => !step.verb || !supported.includes(step.verb));
      const sim = simState[target.id];
      enqueue(sim, accepted);

      let message = rejected.length
        ? `Skipped: ${rejected.map((step) => step.raw).join(", ")}`
        : `Running ${accepted.length} step${accepted.length === 1 ? "" : "s"}`;
      if (target.data.host && !sim.live) message += " (not connected, simulation only)";
      logs[target.id] = message;
    }
    set({
      logs,
      busy: { ...get().busy, ...Object.fromEntries(targets.map((target) => [target.id, true])) },
    });
  },

  // Called once per frame from the 3D stage.
  tick: (dt) => {
    let changed = false;
    const busy = { ...get().busy };
    for (const [id, sim] of Object.entries(simState)) {
      if (stepRobot(sim, dt)) {
        busy[id] = sim.busy;
        changed = true;
      }
      if (sim.outbox.length) flushOutbox(id);
    }
    if (changed) set({ busy });
  },
}));

// Send queued control actions to the backend for connected robots; drop them otherwise.
function flushOutbox(id, knownNode) {
  const sim = simState[id];
  if (!sim) return;
  const actions = sim.outbox.splice(0);
  if (!sim.live) return;

  const node = knownNode ?? usePipeline.getState().nodes.find((item) => item.id === id);
  if (!node?.data.host) return;

  for (const action of actions) {
    chains[id] = (chains[id] ?? Promise.resolve())
      .then(() => postApi("/robots/control", { ip: node.data.host, domain_id: node.data.domainId, action }))
      .catch((error) => usePipeline.getState().reportError(id, error.message));
  }
}

// Persist robots + wires (debounced) so a refresh keeps the pipeline.
let saveTimer;
usePipeline.subscribe((state, previous) => {
  if (state.nodes === previous.nodes && state.edges === previous.edges) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveGraph(state), 300);
});
