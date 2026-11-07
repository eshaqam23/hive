import React from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Grid, OrbitControls } from "@react-three/drei";
import { useShallow } from "zustand/react/shallow";
import Robot3D from "./Robot3D";
import { usePipeline } from "./store";

// Single place that advances the simulation, so robots stay in lockstep.
function SimDriver() {
  const tick = usePipeline((state) => state.tick);
  useFrame((_, dt) => tick(Math.min(dt, 0.05)));
  return null;
}

function Stage3D() {
  const robotsById = usePipeline(
    useShallow((state) =>
      Object.fromEntries(
        state.nodes
          .filter((node) => node.type === "robot")
          .map((node) => [node.id, node.data]),
      ),
    ),
  );
  const objectivesById = usePipeline(
    useShallow((state) =>
      Object.fromEntries(
        state.nodes
          .filter((node) => node.type === "objective")
          .map((node) => [node.id, node.data]),
      ),
    ),
  );
  const locatedObjectives = usePipeline(
    useShallow((state) =>
      Object.fromEntries(Object.entries(state.locatedObjectives).map(([id, located]) => [id, located])),
    ),
  );
  const scanHighlights = usePipeline(
    useShallow((state) => Object.fromEntries(Object.entries(state.scanHighlights))),
  );
  const selectedRobotId = usePipeline((state) => state.selectedRobotId);
  const selectRobot = usePipeline((state) => state.selectRobot);

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

      {Object.entries(robotsById).map(([id, data]) => (
        <Robot3D
          key={id}
          id={id}
          name={data.name}
          robotType={data.robotType}
          selected={selectedRobotId === id}
          onSelect={selectRobot}
        />
      ))}

      {Object.entries(objectivesById).map(([id, data]) => {
        const located = Boolean(locatedObjectives[id]);
        const highlighted = Boolean(scanHighlights[id]);
        return (
          <group key={id} position={[data.x, 0, data.z]}>
            <mesh position={[0, 0.38, 0]} castShadow receiveShadow>
              <sphereGeometry args={[0.38, 24, 16]} />
              <meshStandardMaterial
                color={highlighted ? "#f5b301" : "#8a7a55"}
                emissive={highlighted ? "#f5b301" : "#000000"}
                emissiveIntensity={highlighted ? 0.7 : 0}
                roughness={0.45}
                metalness={0.08}
              />
            </mesh>
            {located && (
              <mesh position={[0, 0.025, 0]} rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.55, 0.7, 40]} />
                <meshBasicMaterial color="#ffd84d" />
              </mesh>
            )}
          </group>
        );
      })}

      <SimDriver />
      <OrbitControls makeDefault maxPolarAngle={Math.PI / 2.1} minDistance={4} maxDistance={26} />
    </Canvas>
  );
}

export default React.memo(Stage3D);
