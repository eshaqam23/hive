import React from "react";
import { Handle, Position } from "@xyflow/react";
import { ROBOT_TYPES } from "./sim";
import { usePipeline } from "./store";

export function CommandNode({ id, data, selected }) {
  const updateNodeData = usePipeline((state) => state.updateNodeData);
  const runCommand = usePipeline((state) => state.runCommand);
  const removeNode = usePipeline((state) => state.removeNode);

  return (
    <div className={`flow-node command-node ${selected ? "selected" : ""}`}>
      <header>
        <span className="node-icon">&gt;_</span>
        <strong>Command</strong>
        <button className="node-close nodrag" onClick={() => removeNode(id)} aria-label="Remove node">
          ×
        </button>
      </header>
      <textarea
        className="nodrag nowheel"
        value={data.text}
        rows={3}
        spellCheck={false}
        placeholder="forward 3, left 90, wave"
        onChange={(event) => updateNodeData(id, { text: event.target.value })}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") runCommand(id);
        }}
      />
      <footer>
        <button className="run-button nodrag" onClick={() => runCommand(id)}>
          ▶ Run
        </button>
        {data.lastRun === "no-target" && <span className="node-hint warn">Connect a robot</span>}
        {data.lastRun && data.lastRun !== "no-target" && <span className="node-hint">Sent</span>}
      </footer>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const CONNECTION_LABEL = {
  offline: "Offline",
  connecting: "Connecting…",
  connected: "Connected",
  failed: "Connection failed",
};

export function RobotNode({ id, data, selected }) {
  const busy = usePipeline((state) => state.busy[id]);
  const log = usePipeline((state) => state.logs[id]);
  const conn = usePipeline((state) => state.conn[id]);
  const selectRobot = usePipeline((state) => state.selectRobot);
  const updateNodeData = usePipeline((state) => state.updateNodeData);
  const removeNode = usePipeline((state) => state.removeNode);
  const connectRobot = usePipeline((state) => state.connectRobot);
  const disconnectRobot = usePipeline((state) => state.disconnectRobot);
  const type = ROBOT_TYPES[data.robotType];

  const status = conn?.status ?? "offline";
  const hasHost = Boolean(data.host);

  return (
    <div
      className={`flow-node robot-node ${selected ? "selected" : ""} ${busy ? "busy" : ""}`}
      onClick={() => selectRobot(id)}
    >
      <Handle type="target" position={Position.Left} />
      <header>
        <span className="node-icon" style={{ background: type.color }} />
        <input
          className="node-name nodrag"
          value={data.name}
          onChange={(event) => updateNodeData(id, { name: event.target.value })}
          aria-label="Robot name"
        />
        <button className="node-close nodrag" onClick={() => removeNode(id)} aria-label="Remove node">
          ×
        </button>
      </header>
      <div className="robot-meta">{type.label}</div>
      {hasHost && (
        <div className="robot-meta mono">
          {data.host} · domain {data.domainId}
        </div>
      )}

      <div className="robot-status">
        <span className={`status-pip ${busy ? "on" : ""}`} />
        {busy ? "Working" : "Idle"}
        <span className={`conn-badge ${hasHost ? status : "sim"}`}>
          {hasHost ? CONNECTION_LABEL[status] : "Simulated"}
        </span>
      </div>

      {hasHost && (
        <div className="conn-actions">
          {status === "connected" ? (
            <button className="node-button nodrag" onClick={() => disconnectRobot(id)}>
              Disconnect
            </button>
          ) : (
            <button
              className="node-button primary nodrag"
              disabled={status === "connecting"}
              onClick={() => connectRobot(id)}
            >
              {status === "failed" ? "Retry connect" : "Connect"}
            </button>
          )}
        </div>
      )}
      {status === "failed" && conn?.error && <div className="robot-log error">{conn.error}</div>}

      {log && <div className="robot-log">{log}</div>}
      <div className="verbs">
        {type.verbs
          .filter((verb) => verb !== "stop")
          .map((verb) => (
            <span key={verb}>{verb}</span>
          ))}
      </div>
    </div>
  );
}

export const nodeTypes = { command: CommandNode, robot: RobotNode };
