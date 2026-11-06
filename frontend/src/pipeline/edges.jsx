import React from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from "@xyflow/react";
import { usePipeline } from "./store";

// A wire between two boxes, with a wide click area and a × button to remove it.
export default function WireEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  markerEnd,
  style,
}) {
  const removeEdge = usePipeline((state) => state.removeEdge);
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={28} />
      <EdgeLabelRenderer>
        <button
          className={`wire-delete nodrag nopan ${selected ? "selected" : ""}`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          onClick={() => removeEdge(id)}
          aria-label="Remove connection"
          title="Remove connection"
        >
          ×
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

export const edgeTypes = { wire: WireEdge };
