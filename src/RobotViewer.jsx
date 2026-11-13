import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { addLights, buildRobot, frameCamera, PRESET_MODELS } from "./robotModels.js";

/* An RViz / Gazebo style viewer for a robot's CAD model.
 *   left-drag orbit · right-drag or shift-drag pan · wheel or pinch zoom
 * A 10 cm grid on the floor, a ROS axis triad (x red, y green, z blue) at the base frame, and view
 * presets. It renders only while something moves, so an idle viewer costs nothing. */

/* The model root turns ROS into three.js: ROS x (forward) -> +x, ROS y (left) -> -z, ROS z (up) -> +y.
   So "front" looks back along -x from +x, and "side" looks from the robot's right (+z). */
const VIEWS = {
  reset: { dir: [0.9, 0.5, 0.85], label: "Reset view" },
  front: { dir: [1, 0.16, 0.0001], label: "Front view" },
  side: { dir: [0.0001, 0.16, 1], label: "Side view" },
  top: { dir: [0.0001, 1, 0.0002], label: "Top view" },
};

export default function RobotViewer({ preset, className = "" }) {
  const mountRef = useRef(null);
  const api = useRef({});
  const [state, setState] = useState("loading");
  const [grid, setGrid] = useState(true);
  const [axes, setAxes] = useState(true);

  useEffect(() => {
    const mount = mountRef.current;
    let disposed = false;
    let frame = 0;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.className = "block h-full w-full touch-none outline-none";
    renderer.domElement.setAttribute("role", "img");
    renderer.domElement.setAttribute("aria-label", `${PRESET_MODELS[preset]?.label}. Drag to orbit, right-drag to pan, scroll to zoom.`);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    addLights(scene);

    // floor: a shadow catcher plus a 10 cm grid, 2 m across, like RViz's default grid display
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(6, 6), new THREE.ShadowMaterial({ opacity: 0.32 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    const gridHelper = new THREE.GridHelper(2, 20, 0x8a877f, 0x55534e);
    gridHelper.material.transparent = true;
    gridHelper.material.opacity = 0.55;
    gridHelper.position.y = 0.0005;
    scene.add(gridHelper);

    // ROS axis triad at the base frame: x forward (red), y left (green), z up (blue)
    const triad = new THREE.Group();
    const arrow = (dir, color) => new THREE.ArrowHelper(new THREE.Vector3(...dir), new THREE.Vector3(), 0.12, color, 0.025, 0.014);
    triad.add(arrow([1, 0, 0], 0xe5484d), arrow([0, 0, -1], 0x46a758), arrow([0, 1, 0], 0x3e63dd));
    triad.position.y = 0.002;
    scene.add(triad);

    const camera = new THREE.PerspectiveCamera(32, 1, 0.01, 50);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.09;
    controls.screenSpacePanning = true;
    controls.maxPolarAngle = Math.PI * 0.495; // never below the floor
    controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
    // no listenToKeyEvents(): the arrow keys belong to driving

    let size = new THREE.Vector3(0.35, 0.24, 0.2);
    const render = () => renderer.render(scene, camera);
    const loop = () => {
      frame = 0;
      if (controls.update()) frame = requestAnimationFrame(loop); // keep going while damping settles
      render();
    };
    const kick = () => {
      if (!frame) frame = requestAnimationFrame(loop);
    };
    controls.addEventListener("change", kick);

    const resize = () => {
      const w = mount.clientWidth || 1;
      const h = mount.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      kick();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(mount);
    resize();

    const setView = (name) => {
      const v = VIEWS[name] || VIEWS.reset;
      const target = new THREE.Vector3(0, size.y * 0.42, 0);
      const radius = Math.max(size.x, size.y, size.z) * 0.62;
      const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov) / 2);
      camera.position.copy(target).addScaledVector(new THREE.Vector3(...v.dir).normalize(), dist);
      controls.target.copy(target);
      controls.update();
      kick();
    };
    const zoom = (factor) => {
      const offset = camera.position.clone().sub(controls.target).multiplyScalar(factor);
      const len = THREE.MathUtils.clamp(offset.length(), controls.minDistance, controls.maxDistance);
      camera.position.copy(controls.target).add(offset.setLength(len));
      controls.update();
      kick();
    };

    buildRobot(preset)
      .then((robot) => {
        if (disposed) return;
        scene.add(robot);
        size = robot.userData.size;
        controls.minDistance = Math.max(size.x, size.z) * 0.6;
        controls.maxDistance = Math.max(size.x, size.z) * 8;
        frameCamera(camera, size, controls.target);
        controls.update();
        setState("ready");
        kick();
      })
      .catch(() => !disposed && setState("error"));

    api.current = {
      setView,
      zoom,
      setGrid: (on) => {
        gridHelper.visible = on;
        kick();
      },
      setAxes: (on) => {
        triad.visible = on;
        kick();
      },
    };

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      ro.disconnect();
      controls.dispose();
      // the robot's geometry is shared with the cached models the cards use, so only what this
      // viewer created itself is disposed
      [ground, gridHelper, triad].forEach((obj) =>
        obj.traverse((o) => {
          o.geometry?.dispose?.();
          [].concat(o.material || []).forEach((m) => m.dispose?.());
        }),
      );
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, [preset]);

  useEffect(() => api.current.setGrid?.(grid), [grid]);
  useEffect(() => api.current.setAxes?.(axes), [axes]);

  const tool = "grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-raised hover:text-ink";

  /* the caller positions the viewer (often absolute inset-0); default to relative so the overlays
     still have a positioning context. Adding "relative" on top would override "absolute" and
     collapse the viewer to zero height. */
  return (
    <div className={className || "relative h-full w-full"}>
      <div className="absolute inset-0 cursor-grab active:cursor-grabbing" onContextMenu={(e) => e.preventDefault()} ref={mountRef} />

      {state !== "ready" && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-[14px] text-muted">
          {state === "loading" ? "Loading CAD model…" : "The CAD model could not load."}
        </div>
      )}

      {/* toolbar */}
      <div aria-label="View controls" className="absolute right-3 top-3 flex items-center gap-0.5 rounded-xl border border-line bg-panel/85 p-1 backdrop-blur" role="toolbar">
        {[
          ["reset", <ResetIcon key="i" />],
          ["front", <span className="text-[13px] font-semibold" key="i">F</span>],
          ["side", <span className="text-[13px] font-semibold" key="i">S</span>],
          ["top", <span className="text-[13px] font-semibold" key="i">T</span>],
        ].map(([name, icon]) => (
          <button aria-label={VIEWS[name].label} className={tool} key={name} onClick={() => api.current.setView?.(name)} title={VIEWS[name].label} type="button">
            {icon}
          </button>
        ))}
        <span aria-hidden="true" className="mx-0.5 h-5 w-px bg-line-strong" />
        <button aria-label="Zoom in" className={tool} onClick={() => api.current.zoom?.(0.8)} title="Zoom in" type="button">
          <ZoomIcon plus />
        </button>
        <button aria-label="Zoom out" className={tool} onClick={() => api.current.zoom?.(1.25)} title="Zoom out" type="button">
          <ZoomIcon />
        </button>
        <span aria-hidden="true" className="mx-0.5 h-5 w-px bg-line-strong" />
        <button aria-label="Show grid" aria-pressed={grid} className={`${tool} ${grid ? "text-ink" : ""}`} onClick={() => setGrid((v) => !v)} title="Grid" type="button">
          <GridIcon />
        </button>
        <button aria-label="Show axes" aria-pressed={axes} className={`${tool} ${axes ? "text-ink" : ""}`} onClick={() => setAxes((v) => !v)} title="Axes (x red, y green, z blue)" type="button">
          <AxesIcon />
        </button>
      </div>

      <p className="pointer-events-none absolute bottom-3 left-4 text-[13px] text-faint">
        Drag to orbit · right-drag to pan · scroll to zoom
      </p>
    </div>
  );
}

const svg = { fill: "none", height: 18, stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, viewBox: "0 0 24 24", width: 18, "aria-hidden": true };
const ResetIcon = () => (
  <svg {...svg}>
    <path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5" />
    <path d="M4 3.5v4h4" />
  </svg>
);
const ZoomIcon = ({ plus }) => (
  <svg {...svg}>
    <circle cx="10.5" cy="10.5" r="6" />
    <path d="m15 15 5 5M8 10.5h5" />
    {plus && <path d="M10.5 8v5" />}
  </svg>
);
const GridIcon = () => (
  <svg {...svg}>
    <path d="M4 4h16v16H4zM4 9.33h16M4 14.67h16M9.33 4v16M14.67 4v16" />
  </svg>
);
const AxesIcon = () => (
  <svg {...svg}>
    <path d="M6 18V5M6 18h13M6 18l-3.5 3" />
  </svg>
);
