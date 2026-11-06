import React from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Grid, OrbitControls } from "@react-three/drei";
import Robot3D from "./Robot3D";
import { usePipeline } from "./store";

// Single place that advances the simulation, so robots stay in lockstep.
function SimDriver() {
  const tick = usePipeline((state) => state.tick);
  useFrame((_, dt) => tick(Math.min(dt, 0.05)));
  return null;
}

export default function Stage3D() {
  const nodes = usePipeline((state) => state.nodes);
  const selectedRobotId = usePipeline((state) => state.selectedRobotId);
  const selectRobot = usePipeline((state) => state.selectRobot);
  const robots = nodes.filter((node) => node.type === "robot");

  return (
    <Canvas
      shadows
      camera={{ position: [7, 6.5, 8], fov: 42 }}
      onPointerMissed={() => selectRobot(null)}
    >
      <color attach="background" args={["#fbf5e6"]} />
      <fog attach="fog" args={["#fbf5e6", 18, 40]} />
      <ambientLight intensity={0.8} />
      <directionalLight
        position={[6, 10, 4]}
        intensity={1.6}
        castShadow
        shadow-mapSize={[1024, 1024]}
        shadow-camera-left={-12}
        shadow-camera-right={12}
        shadow-camera-top={12}
        shadow-camera-bottom={-12}
      />

      <Grid
        args={[40, 40]}
        cellSize={1}
        cellColor="#e6d7ac"
        sectionSize={5}
        sectionColor="#d4b968"
        fadeDistance={28}
        infiniteGrid
      />
      <ContactShadows position={[0, 0.001, 0]} opacity={0.35} scale={30} blur={2.2} far={3} />

      {robots.map((node) => (
        <Robot3D
          key={node.id}
          id={node.id}
          name={node.data.name}
          robotType={node.data.robotType}
          selected={selectedRobotId === node.id}
          onSelect={selectRobot}
        />
      ))}

      <SimDriver />
      <OrbitControls makeDefault maxPolarAngle={Math.PI / 2.1} minDistance={4} maxDistance={26} />
    </Canvas>
  );
}
