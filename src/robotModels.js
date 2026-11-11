/* Robot models built from real ROS / Gazebo CAD (see public/models/CREDITS.md):
 *   car   NXP MR-Buggy3, the Gazebo model by Rudis Laboratories (CC BY 4.0)
 *   d435  Intel RealSense D435 mesh, realsense2_description (Apache-2.0)
 *   lds   LDS-01 2D lidar mesh, turtlebot3_description (Apache-2.0)
 *   arm   OpenMANIPULATOR-X, open_manipulator_description (Apache-2.0)
 *
 * Every GLB is in ROS coordinates: x forward, y left, z up, metres. A preset is the car plus one
 * payload mounted in that frame; the root is turned -90 deg about x so three.js (y up) shows it
 * standing, the way RViz shows a URDF.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { toCreasedNormals } from "three/examples/jsm/utils/BufferGeometryUtils.js";

const MODEL_BASE = "/models";

/* Mounts in the car's frame (metres, roll-pitch-yaw radians, as a URDF joint would give them). */
export const PRESET_MODELS = {
  cameraRcCar: {
    parts: [{ file: "car" }, { file: "d435", xyz: [0.128, 0, 0.118], rpy: [Math.PI / 2, 0, Math.PI / 2] }],
    label: "MR-Buggy3 RC car with an Intel RealSense D435 camera on the front deck",
  },
  lidarRcCar: {
    parts: [{ file: "car" }, { file: "lds", xyz: [0.035, 0, 0.142], rpy: [0, 0, 0] }],
    label: "MR-Buggy3 RC car with an LDS-01 2D lidar on the top deck",
  },
  gripperRcCar: {
    parts: [{ file: "car" }, { file: "arm", xyz: [-0.02, 0, 0.118], rpy: [0, 0, 0] }],
    label: "MR-Buggy3 RC car carrying an OpenMANIPULATOR-X arm and gripper",
  },
};

const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
const fileCache = new Map();

/* One fetch per file; the car is shared by every preset. Normals are rebuilt with a crease angle so
   CAD edges stay sharp and curved faces stay smooth. */
function loadFile(file) {
  if (!fileCache.has(file)) {
    fileCache.set(
      file,
      loader.loadAsync(`${MODEL_BASE}/${file}.glb`).then((gltf) => {
        gltf.scene.traverse((o) => {
          if (!o.isMesh) return;
          o.geometry = toCreasedNormals(o.geometry, THREE.MathUtils.degToRad(32));
          const m = o.material;
          o.material = new THREE.MeshStandardMaterial({
            color: m.color,
            metalness: Math.min(m.metalness ?? 0.15, 0.35),
            roughness: Math.max(m.roughness ?? 0.55, 0.38),
            side: THREE.DoubleSide,
          });
          o.castShadow = true;
          o.receiveShadow = true;
        });
        return gltf.scene;
      }),
    );
  }
  return fileCache.get(file);
}

/* The preset as a three.js group standing on y = 0 (its wheels on the grid). */
export async function buildRobot(preset) {
  const config = PRESET_MODELS[preset] || PRESET_MODELS.cameraRcCar;
  const ros = new THREE.Group(); // ROS frame
  for (const part of config.parts) {
    const scene = (await loadFile(part.file)).clone(true);
    const holder = new THREE.Group();
    holder.add(scene);
    if (part.xyz) holder.position.set(...part.xyz);
    if (part.rpy) holder.rotation.set(part.rpy[0], part.rpy[1], part.rpy[2], "ZYX");
    ros.add(holder);
  }
  const root = new THREE.Group();
  root.add(ros);
  root.rotation.x = -Math.PI / 2; // ROS z-up -> three.js y-up
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  root.position.y -= box.min.y; // stand on the grid
  root.position.x -= (box.min.x + box.max.x) / 2;
  root.position.z -= (box.min.z + box.max.z) / 2;
  root.userData.size = box.getSize(new THREE.Vector3());
  return root;
}

/* Studio lighting shared by the viewer and the snapshots: a sky/ground fill, a warm key that casts
   soft shadows, and a cool rim to pull the silhouette off the background. */
export function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0xf4efe6, 0x2a2826, 1.15));
  const key = new THREE.DirectionalLight(0xfff4e8, 2.2);
  key.position.set(0.6, 1.1, 0.8);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.near = 0.1;
  key.shadow.camera.far = 4;
  Object.assign(key.shadow.camera, { left: -0.5, right: 0.5, top: 0.5, bottom: -0.5 });
  key.shadow.radius = 4;
  key.shadow.bias = -0.0004;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xc9d6ff, 0.9);
  rim.position.set(-0.8, 0.6, -0.9);
  scene.add(rim);
}

/* Frames the robot from a three-quarter angle, the way the cards and the viewer's reset show it. */
export function frameCamera(camera, size, target = new THREE.Vector3(), distance = 1) {
  const radius = Math.max(size.x, size.y, size.z) * 0.62;
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const dist = (radius / Math.sin(fov / 2)) * distance;
  const dir = new THREE.Vector3(0.9, 0.5, 0.85).normalize(); // front-right three-quarter (see RobotViewer VIEWS)
  target.set(0, size.y * 0.42, 0);
  camera.position.copy(target).addScaledVector(dir, dist);
  camera.near = dist / 100;
  camera.far = dist * 20;
  camera.lookAt(target);
  camera.updateProjectionMatrix();
  return target;
}

/* ------------------------------------------------------------------ snapshots
 * One offscreen renderer draws every card image, so a page of robots costs a single WebGL context
 * instead of one per card. Images are transparent PNGs, cached per preset.
 */
let snapRenderer = null;
const snapCache = new Map();

export function snapshot(preset, width = 720, height = 480) {
  const key = `${preset}:${width}x${height}`;
  if (snapCache.has(key)) return snapCache.get(key);
  const job = (async () => {
    const robot = await buildRobot(preset);
    if (!snapRenderer) {
      snapRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
      snapRenderer.setPixelRatio(1);
      snapRenderer.outputColorSpace = THREE.SRGBColorSpace;
      snapRenderer.toneMapping = THREE.ACESFilmicToneMapping;
      snapRenderer.toneMappingExposure = 1.05;
      snapRenderer.shadowMap.enabled = true;
      snapRenderer.shadowMap.type = THREE.PCFSoftShadowMap;
    }
    snapRenderer.setSize(width, height, false);
    snapRenderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    addLights(scene);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.ShadowMaterial({ opacity: 0.38 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground, robot);
    const camera = new THREE.PerspectiveCamera(30, width / height, 0.01, 50);
    frameCamera(camera, robot.userData.size, new THREE.Vector3(), 0.92);
    snapRenderer.render(scene, camera);
    return snapRenderer.domElement.toDataURL("image/png");
  })();
  snapCache.set(key, job);
  job.catch(() => snapCache.delete(key));
  return job;
}
