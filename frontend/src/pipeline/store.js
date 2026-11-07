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
let positionUpdateElapsed = 0;
let positionsDirty = false;

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
      (node) =>
        node.type === "command" ||
        (node.type === "robot" && ROBOT_TYPES[node.data?.robotType]) ||
        (node.type === "objective" &&
          typeof node.data?.name === "string" &&
          Number.isFinite(node.data?.x) &&
          Number.isFinite(node.data?.z)),
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
const initialRobotPositions = Object.fromEntries(
  initial.nodes
    .filter((node) => node.type === "robot")
    .map((node) => {
      const { x, z } = simState[node.id];
      return [node.id, { x, z }];
    }),
);

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
  robotPositions: initialRobotPositions,
  locatedObjectives: {},
  scanHighlights: {},
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
    set({
      robotPositions: Object.fromEntries(
        Object.entries(get().robotPositions).filter(([id]) => !deleted.some((node) => node.id === id)),
      ),
      locatedObjectives: Object.fromEntries(
        Object.entries(get().locatedObjectives).filter(
          ([id]) => !deleted.some((node) => node.id === id),
        ),
      ),
      scanHighlights: Object.fromEntries(
        Object.entries(get().scanHighlights).filter(
          ([id]) => !deleted.some((node) => node.id === id),
        ),
      ),
    });
  },

  suggestedDomainId: () => nextDomainId(get().nodes),

  addRobot: (config) => {
    const robotCount = get().nodes.filter((node) => node.type === "robot").length;
    const node = robotNode(config, robotCount, {
      x: 420 + (robotCount % 2) * 40,
      y: 20 + robotCount * 130,
    });
    const sim = simState[node.id];
    set({
      nodes: [...get().nodes, node],
      robotPositions: { ...get().robotPositions, [node.id]: { x: sim.x, z: sim.z } },
      selectedRobotId: node.id,
    });
  },

  addCommand: () => {
    const count = get().nodes.filter((node) => node.type === "command").length;
    set({ nodes: [...get().nodes, commandNode({ x: 40, y: 120 + count * 200 }, "wave")] });
  },

  addObjective: () => {
    const objectives = get().nodes.filter((node) => node.type === "objective");
    const node = {
      id: nextId("objective"),
      type: "objective",
      position: { x: 520, y: 120 + objectives.length * 150 },
      data: { name: `Objective ${objectives.length + 1}`, x: 0, z: 0 },
    };
    set({ nodes: [...get().nodes, node] });
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
      robotPositions: Object.fromEntries(
        Object.entries(get().robotPositions).filter(([robotId]) => robotId !== id),
      ),
      locatedObjectives: Object.fromEntries(
        Object.entries(get().locatedObjectives).filter(([objectiveId]) => objectiveId !== id),
      ),
      scanHighlights: Object.fromEntries(
        Object.entries(get().scanHighlights).filter(([objectiveId]) => objectiveId !== id),
      ),
    });
  },

  updateNodeData: (id, patch) =>
    set({
      nodes: get().nodes.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...patch } } : node)),
      ...(get().nodes.some(
        (node) =>
          node.id === id &&
          node.type === "objective" &&
          (patch.x !== undefined || patch.z !== undefined),
      )
        ? {
            locatedObjectives: Object.fromEntries(
              Object.entries(get().locatedObjectives).filter(([objectiveId]) => objectiveId !== id),
            ),
            scanHighlights: Object.fromEntries(
              Object.entries(get().scanHighlights).filter(([objectiveId]) => objectiveId !== id),
            ),
          }
        : {}),
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
    const nodes = get().nodes;
    const objectives = nodes.filter((node) => node.type === "objective");
    const locatedObjectives = get().locatedObjectives;
    const busy = { ...get().busy };

    for (const target of targets) {
      const supported = ROBOT_TYPES[target.data.robotType].verbs;
      const accepted = [];
      const rejected = [];

      for (const step of steps) {
        if (step.verb === "seek") {
          const matches = objectives.filter(
            (objective) => objective.data.name.trim().toLowerCase() === step.targetName,
          );
          if (!step.targetName || matches.length !== 1) {
            rejected.push(step.raw);
          } else if (!locatedObjectives[matches[0].id]) {
            rejected.push(`${step.raw} (scan within 15 units first)`);
          } else {
            accepted.push({ verb: "seek", targetId: matches[0].id });
          }
        } else if (step.verb && supported.includes(step.verb)) {
          accepted.push(step);
        } else {
          rejected.push(step.raw);
        }
      }

      const sim = simState[target.id];
      enqueue(sim, accepted);

      let message = rejected.length
        ? `Skipped: ${rejected.join(", ")}`
        : `Running ${accepted.length} step${accepted.length === 1 ? "" : "s"}`;
      if (accepted.length && rejected.length) {
        message += `; running ${accepted.length} step${accepted.length === 1 ? "" : "s"}`;
      }
      if (target.data.host && !sim.live) message += " (not connected, simulation only)";
      logs[target.id] = message;
      busy[target.id] = sim.busy;
    }
    set({
      logs,
      busy,
    });
  },

  // Called once per frame from the 3D stage.
  tick: (dt) => {
    let changed = false;
    let moved = false;
    const busy = { ...get().busy };
    const nodes = get().nodes;
    const objectivesById = Object.fromEntries(
      nodes.filter((node) => node.type === "objective").map((node) => [node.id, node.data]),
    );
    let locatedObjectives;
    let scanHighlights;

    for (const [id, sim] of Object.entries(simState)) {
      const previousX = sim.x;
      const previousZ = sim.z;
      const robot = nodes.find((node) => node.id === id && node.type === "robot");
      const locateObjectives = () => {
        if (robot?.data.robotType !== "camera") return;
        const found = nodes.filter(
          (node) =>
            node.type === "objective" &&
            Math.hypot(node.data.x - sim.x, node.data.z - sim.z) <= 15,
        );
        if (found.length) {
          locatedObjectives ??= { ...get().locatedObjectives };
          scanHighlights ??= { ...get().scanHighlights };
          for (const objective of found) {
            locatedObjectives[objective.id] = true;
            const expiresAt = Date.now() + 2000;
            scanHighlights[objective.id] = expiresAt;
            setTimeout(() => {
              const current = get().scanHighlights[objective.id];
              if (current !== expiresAt) return;
              const nextHighlights = { ...get().scanHighlights };
              delete nextHighlights[objective.id];
              set({ scanHighlights: nextHighlights });
            }, 2000);
          }
        }
      };

      if (stepRobot(sim, dt, locateObjectives, objectivesById)) {
        busy[id] = sim.busy;
        changed = true;
      }
      if (sim.x !== previousX || sim.z !== previousZ) moved = true;
      if (sim.outbox.length) flushOutbox(id);
    }
    if (moved) positionsDirty = true;
    positionUpdateElapsed += dt;
    const updates = {};
    if (positionsDirty && positionUpdateElapsed >= 0.1) {
      const robotPositions = Object.fromEntries(
        Object.entries(simState).map(([id, sim]) => [id, { x: sim.x, z: sim.z }]),
      );
      updates.robotPositions = robotPositions;
      positionsDirty = false;
      positionUpdateElapsed = 0;
    }
    if (changed) updates.busy = busy;
    if (locatedObjectives) updates.locatedObjectives = locatedObjectives;
    if (scanHighlights) updates.scanHighlights = scanHighlights;
    if (Object.keys(updates).length) set(updates);
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
