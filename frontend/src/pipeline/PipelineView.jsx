import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Controls, MiniMap, ReactFlow, useViewport } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./pipeline.css";
import { nodeTypes } from "./nodes";
import AddRobotModal from "./AddRobotModal";
import Brand from "./Brand";
import { edgeTypes } from "./edges";
import { usePipeline } from "./store";

const Stage3D = React.lazy(() => import("./Stage3D"));

function HexagonBackground() {
  const { x, y, zoom } = useViewport();
  const svgRef = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;

    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);

  const path = useMemo(() => {
    const side = 23;
    const halfWidth = (Math.sqrt(3) * side) / 2;
    const columnStep = halfWidth * 2;
    const rowStep = side * 1.5;
    const worldLeft = -x / zoom - side;
    const worldRight = (size.width - x) / zoom + side;
    const worldTop = -y / zoom - side;
    const worldBottom = (size.height - y) / zoom + side;
    let segments = "";

    if (!size.width || !size.height) return segments;

    const firstRow = Math.ceil((worldTop - side) / rowStep);
    const lastRow = Math.floor((worldBottom + side) / rowStep);

    for (let row = firstRow; row <= lastRow; row += 1) {
      const centerY = row * rowStep;
      const rowOffset = row * halfWidth;
      const firstColumn = Math.ceil((worldLeft - halfWidth - rowOffset) / columnStep);
      const lastColumn = Math.floor((worldRight + halfWidth - rowOffset) / columnStep);

      for (let column = firstColumn; column <= lastColumn; column += 1) {
        const centerX = rowOffset + column * columnStep;
        const points = [
          [0, -side],
          [halfWidth, -side / 2],
          [halfWidth, side / 2],
          [0, side],
          [-halfWidth, side / 2],
          [-halfWidth, -side / 2],
        ];

        for (const [pointX, pointY] of points) {
          const screenX = x + (centerX + pointX) * zoom;
          const screenY = y + (centerY + pointY) * zoom;
          segments += `${segments.length === 0 || segments.endsWith("Z") ? "M" : "L"}${screenX.toFixed(3)} ${screenY.toFixed(3)}`;
        }
        segments += "Z";
      }
    }

    return segments;
  }, [size.height, size.width, x, y, zoom]);

  return (
    <svg
      ref={svgRef}
      aria-hidden="true"
      className="hexagon-background"
      viewBox={size.width && size.height ? `0 0 ${size.width} ${size.height}` : undefined}
    >
      <path d={path} fill="none" stroke="#e2cf93" strokeOpacity="0.35" strokeWidth="1" />
    </svg>
  );
}

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
            <HexagonBackground />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              bgColor="#fffdf7"
              maskColor="rgba(226, 207, 147, 0.3)"
              nodeColor={(node) => (node.type === "command" ? "#70500f" : "#f1e2bf")}
              nodeStrokeColor={(node) => (node.type === "command" ? "#70500f" : "#e8d79f")}
            />
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
