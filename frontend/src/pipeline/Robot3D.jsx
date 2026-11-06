import React, { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import { ROBOT_TYPES } from "./sim";
import { simState, usePipeline } from "./store";

const WHEEL_POSITIONS = [
  [-0.55, 0.22, -0.5],
  [0.55, 0.22, -0.5],
  [-0.55, 0.22, 0.5],
  [0.55, 0.22, 0.5],
];

// A primitive RC car. All motion is driven by reading simState[id] each frame,
// so the component never re-renders while animating.
export default function Robot3D({ id, name, robotType, selected, onSelect }) {
  const sim = simState[id];
  const busy = usePipeline((state) => state.busy[id]);
  const color = ROBOT_TYPES[robotType].color;

  const root = useRef();
  const wheels = useRef([]);
  const head = useRef(); // camera pan / lidar spin
  const pulse = useRef(); // lidar scan ring
  const arm = useRef();
  const clawL = useRef();
  const clawR = useRef();

  useFrame((state, dt) => {
    if (!sim || !root.current) return;
    root.current.position.set(sim.x, 0, sim.z);
    root.current.rotation.y = sim.heading;
    // Tiny body bob while moving gives the car some weight.
    root.current.position.y = sim.busy ? Math.sin(state.clock.elapsedTime * 18) * 0.008 : 0;

    for (const wheel of wheels.current) if (wheel) wheel.rotation.x = sim.wheelSpin;

    if (robotType === "camera" && head.current) {
      head.current.rotation.y = sim.scanT > 0 ? Math.sin(sim.scanT * Math.PI * 2) * 1.1 : 0;
    }

    if (robotType === "lidar" && head.current) {
      head.current.rotation.y += dt * (sim.scanT > 0 ? 14 : 2);
      if (pulse.current) {
        const ring = sim.scanT > 0 ? (sim.scanT * 4) % 1 : 0;
        pulse.current.visible = sim.scanT > 0;
        pulse.current.scale.setScalar(0.3 + ring * 3.2);
        pulse.current.material.opacity = 0.55 * (1 - ring);
      }
    }

    if (robotType === "gripper" && arm.current) {
      // Wave: arm swings up and oscillates; otherwise it rests slightly raised.
      const wave = sim.waveT > 0 ? Math.sin(state.clock.elapsedTime * 14) * 0.35 : 0;
      const lift = sim.waveT > 0 ? Math.sin(sim.waveT * Math.PI) * 1.1 : 0;
      arm.current.rotation.x = -0.35 - lift;
      arm.current.rotation.z = wave;
      const gap = 0.05 + sim.grip * 0.13;
      if (clawL.current) clawL.current.position.x = -gap;
      if (clawR.current) clawR.current.position.x = gap;
    }
  });

  return (
    <group
      ref={root}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(id);
      }}
    >
      {/* chassis */}
      <mesh position={[0, 0.34, 0]} castShadow>
        <boxGeometry args={[0.9, 0.26, 1.5]} />
        <meshStandardMaterial color={color} roughness={0.45} />
      </mesh>
      <mesh position={[0, 0.2, 0]} castShadow>
        <boxGeometry args={[0.8, 0.12, 1.3]} />
        <meshStandardMaterial color="#2b2418" roughness={0.8} />
      </mesh>

      {/* front marker so heading is readable */}
      <mesh position={[0, 0.36, -0.77]}>
        <boxGeometry args={[0.5, 0.08, 0.04]} />
        <meshStandardMaterial color="#fff7e6" emissive="#fff7e6" emissiveIntensity={0.4} />
      </mesh>

      {/* wheels */}
      {WHEEL_POSITIONS.map((position, index) => (
        <mesh
          key={index}
          ref={(node) => (wheels.current[index] = node)}
          position={position}
          rotation={[0, 0, Math.PI / 2]}
          castShadow
        >
          <cylinderGeometry args={[0.22, 0.22, 0.18, 20]} />
          <meshStandardMaterial color="#1c1710" roughness={0.9} />
          {/* hub stripe makes the spin visible */}
          <mesh position={[0, 0.095, 0.1]}>
            <boxGeometry args={[0.04, 0.02, 0.1]} />
            <meshStandardMaterial color="#f5b301" />
          </mesh>
        </mesh>
      ))}

      {/* status light */}
      <mesh position={[0.3, 0.52, 0.55]}>
        <sphereGeometry args={[0.06, 12, 12]} />
        <meshStandardMaterial
          color={busy ? "#3ecf6e" : "#8a8a8a"}
          emissive={busy ? "#3ecf6e" : "#000000"}
          emissiveIntensity={busy ? 1.6 : 0}
        />
      </mesh>

      {robotType === "camera" && (
        <group position={[0, 0.47, -0.1]}>
          <mesh position={[0, 0.2, 0]} castShadow>
            <cylinderGeometry args={[0.05, 0.05, 0.4, 12]} />
            <meshStandardMaterial color="#44392a" />
          </mesh>
          <group ref={head} position={[0, 0.45, 0]}>
            <mesh castShadow>
              <boxGeometry args={[0.38, 0.24, 0.3]} />
              <meshStandardMaterial color="#3a3226" />
            </mesh>
            <mesh position={[0, 0, -0.17]} rotation={[Math.PI / 2, 0, 0]}>
              <cylinderGeometry args={[0.08, 0.08, 0.08, 16]} />
              <meshStandardMaterial color="#8fe3f0" emissive="#2f8f9d" emissiveIntensity={0.8} />
            </mesh>
          </group>
        </group>
      )}

      {robotType === "lidar" && (
        <group position={[0, 0.47, 0.1]}>
          <mesh position={[0, 0.08, 0]}>
            <cylinderGeometry args={[0.16, 0.2, 0.16, 20]} />
            <meshStandardMaterial color="#44392a" />
          </mesh>
          <group ref={head} position={[0, 0.25, 0]}>
            <mesh castShadow>
              <cylinderGeometry args={[0.2, 0.2, 0.16, 24]} />
              <meshStandardMaterial color="#cfc6f2" metalness={0.3} roughness={0.35} />
            </mesh>
            <mesh position={[0.2, 0, 0]}>
              <boxGeometry args={[0.05, 0.05, 0.05]} />
              <meshStandardMaterial color="#ff5252" emissive="#ff5252" emissiveIntensity={1.2} />
            </mesh>
          </group>
          <mesh ref={pulse} position={[0, 0.25, 0]} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
            <ringGeometry args={[0.9, 1, 48]} />
            <meshBasicMaterial color={color} transparent opacity={0.5} depthWrite={false} />
          </mesh>
        </group>
      )}

      {robotType === "gripper" && (
        <group position={[0, 0.5, -0.45]}>
          <mesh>
            <sphereGeometry args={[0.12, 14, 14]} />
            <meshStandardMaterial color="#44392a" />
          </mesh>
          <group ref={arm}>
            <mesh position={[0, 0, -0.35]} castShadow>
              <boxGeometry args={[0.12, 0.12, 0.7]} />
              <meshStandardMaterial color="#e8b04a" />
            </mesh>
            <group position={[0, 0, -0.72]}>
              <mesh ref={clawL} position={[-0.18, 0, 0]} castShadow>
                <boxGeometry args={[0.05, 0.1, 0.24]} />
                <meshStandardMaterial color="#2b2418" />
              </mesh>
              <mesh ref={clawR} position={[0.18, 0, 0]} castShadow>
                <boxGeometry args={[0.05, 0.1, 0.24]} />
                <meshStandardMaterial color="#2b2418" />
              </mesh>
            </group>
          </group>
        </group>
      )}

      {selected && (
        <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[1.05, 1.15, 40]} />
          <meshBasicMaterial color="#f5b301" />
        </mesh>
      )}

      <Html position={[0, 1.5, 0]} center distanceFactor={9} style={{ pointerEvents: "none" }}>
        <div className={`robot-tag ${busy ? "busy" : ""}`}>{name}</div>
      </Html>
    </group>
  );
}
