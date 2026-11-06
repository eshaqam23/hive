import React, { Suspense, useMemo, useState } from "react";
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./pipeline.css";
import { nodeTypes } from "./nodes";
import AddRobotModal from "./AddRobotModal";
import Brand from "./Brand";
import { edgeTypes } from "./edges";
import { usePipeline } from "./store";

const Stage3D = React.lazy(() => import("./Stage3D"));

// Quick commands without wiring a node: goes to the selected robot, or all robots.
function CommandBar() {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const nodes = usePipeline((state) => state.nodes);
  const selectedRobotId = usePipeline((state) => state.selectedRobotId);
  const broadcast = usePipeline((state) => state.broadcast);
  const selected = nodes.find((node) => node.id === selectedRobotId);

  function submit(event) {
    event.preventDefault();
    if (!text.trim()) return;
    const results = broadcast(text);
    if (!results.length) {
      setNote({ kind: "warn", text: "No robots to command. Add one first." });
      return;
    }
    const skipped = results.some((result) => result.message.startsWith("Skipped"));
    setNote({
      kind: skipped ? "warn" : "ok",
      text: results.map((result) => `${result.name}: ${result.message}`).join(" · "),
    });
    setText("");
  }

  return (
    <form className="command-bar" onSubmit={submit}>
      <span className="command-target">{selected ? selected.data.name : "All robots"}</span>
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Command, e.g. forward 3, left 90, wave"
        spellCheck={false}
      />
      <button type="submit">Send</button>
      {note && <span className={`command-note ${note.kind}`}>{note.text}</span>}
    </form>
  );
}

export default function PipelineView() {
  const nodes = usePipeline((state) => state.nodes);
  const edges = usePipeline((state) => state.edges);
  const busy = usePipeline((state) => state.busy);
  const onNodesChange = usePipeline((state) => state.onNodesChange);
  const onEdgesChange = usePipeline((state) => state.onEdgesChange);
  const onConnect = usePipeline((state) => state.onConnect);
  const onReconnect = usePipeline((state) => state.onReconnect);
  const disposeNodes = usePipeline((state) => state.disposeNodes);
  const addRobot = usePipeline((state) => state.addRobot);
  const addCommand = usePipeline((state) => state.addCommand);
  const suggestedDomainId = usePipeline((state) => state.suggestedDomainId);
  const [isAddingRobot, setIsAddingRobot] = useState(false);

  // A wire flows (animates) while the robot it feeds is busy.
  const displayEdges = useMemo(
    () =>
      edges.map((edge) => ({
        ...edge,
        type: "wire",
        animated: Boolean(busy[edge.target]),
        className: busy[edge.target] ? "edge-live" : "",
      })),
    [edges, busy],
  );

  return (
    <section className="pipeline-page">
      <header className="pipeline-toolbar">
        <Brand />
        <div className="toolbar-actions">
          <button className="tool-button" onClick={addCommand}>
            + Command
          </button>
          <button className="tool-button primary" onClick={() => setIsAddingRobot(true)}>
            + Robot
          </button>
        </div>
      </header>

      <div className="pipeline-split">
        <div className="board">
          <ReactFlow
            nodes={nodes}
            edges={displayEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onReconnect={onReconnect}
            onNodesDelete={disposeNodes}
            reconnectRadius={12}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            deleteKeyCode={["Backspace", "Delete"]}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1.6} color="#e2cf93" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(node) => (node.type === "command" ? "#2b2418" : "#f5b301")} />
          </ReactFlow>
          <div className="board-hint">Drag a Command's right dot to a robot · click a wire's × to remove it · drag a wire's end to re-route</div>
        </div>

        <div className="stage">
          <Suspense fallback={<div className="stage-loading">Loading 3D stage…</div>}>
            <Stage3D />
          </Suspense>
          <div className="stage-hint">Drag to orbit · scroll to zoom · click a robot to select</div>
        </div>
      </div>

      <CommandBar />

      {isAddingRobot && (
        <AddRobotModal
          suggestedDomainId={suggestedDomainId()}
          existingRobots={nodes
            .filter((node) => node.type === "robot")
            .map((node) => ({ name: node.data.name, domainId: node.data.domainId }))}
          onAdd={addRobot}
          onClose={() => setIsAddingRobot(false)}
        />
      )}
    </section>
  );
}
