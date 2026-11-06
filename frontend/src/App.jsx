import React from "react";
import PipelineView from "./pipeline/PipelineView.jsx";

// Hive is a single page: the robot pipeline (board + 3D stage + command bar).
export default function App() {
  return (
    <main className="main solo">
      <PipelineView />
    </main>
  );
}
