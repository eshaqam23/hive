// The RC car presets, their controls and the link-status labels (moved from App.jsx).
import { ArrowDownIcon, ArrowUpIcon, GripperCloseIcon, GripperOpenIcon, RotateLeftIcon, RotateRightIcon, StopIcon } from "../shell/icons.jsx";

export const DRIVE_CONTROLS = [
  { action: "forward", label: "Forward", key: "W", Icon: ArrowUpIcon },
  { action: "left", label: "Rotate left", key: "A", Icon: RotateLeftIcon },
  { action: "stop", label: "Stop", key: "Space", Icon: StopIcon },
  { action: "right", label: "Rotate right", key: "D", Icon: RotateRightIcon },
  { action: "backward", label: "Backward", key: "S", Icon: ArrowDownIcon },
];

export const GRIPPER_CONTROLS = [
  { action: "gripper_open", label: "Open gripper", Icon: GripperOpenIcon },
  { action: "gripper_close", label: "Close gripper", Icon: GripperCloseIcon },
];

/* Each preset's 3D model is real ROS / Gazebo CAD, assembled in src/robotModels.js. */
export const PRESET_ROBOTS = {
  cameraRcCar: {
    label: "Camera RC Car",
    summary: "Drives on /cmd_vel and streams a Realsense camera.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "Realsense camera", kind: "sensor" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/camera/camera/color/image_raw", type: "sensor_msgs/msg/Image", viewer: "raw" },
    ],
    controls: DRIVE_CONTROLS,
  },
  lidarRcCar: {
    label: "LiDAR RC Car",
    summary: "Drives on /cmd_vel and scans the room with an RP LiDAR.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "RP LiDAR", kind: "2D lidar" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/scan", type: "sensor_msgs/msg/LaserScan", viewer: "raw" },
    ],
    controls: DRIVE_CONTROLS,
  },
  gripperRcCar: {
    label: "Gripper RC Car",
    summary: "Drives on /cmd_vel and opens or closes a gripper on command.",
    capabilities: [
      { name: "Manual drive", kind: "/cmd_vel" },
      { name: "Gripper", kind: "actuator" },
    ],
    topics: [
      { name: "/cmd_vel", type: "geometry_msgs/msg/Twist", viewer: "raw" },
      { name: "/gripper_open", type: "std_msgs/msg/Bool", viewer: "raw" },
    ],
    controls: [...DRIVE_CONTROLS, ...GRIPPER_CONTROLS],
  },
};

export const PRESET_KEYS = Object.keys(PRESET_ROBOTS);

export const STATUS = {
  connected: { label: "Live", dot: "bg-live", text: "text-live" },
  offline: { label: "Offline", dot: "bg-faint", text: "text-muted" },
  connecting: { label: "Connecting…", dot: "bg-warn", text: "text-warn" },
  reconnecting: { label: "Reconnecting…", dot: "bg-warn", text: "text-warn" },
  failed: { label: "Connection failed", dot: "bg-danger", text: "text-danger" },
};

/* "connecting" covers both a first connect and winning back a lost connection; only the label differs */
export const displayStatus = (robot) => (robot.status === "connecting" && robot.reconnecting ? "reconnecting" : robot.status);

export const describe = (robot) => robot.description || PRESET_ROBOTS[robot.preset].summary;

/* The Fleet panel's machine-readable link state, from live-data's own status. "lost": the connect loop
   gave up (a failure retrying cannot fix); "reconnecting": a connection that was lost is being won back. */
export const LINK_STATE = { connected: "online", offline: "offline", connecting: "connecting", reconnecting: "reconnecting", failed: "lost" };
export const linkState = (robot) => LINK_STATE[displayStatus(robot)] ?? "offline";
export const LINK_TONE = { online: "text-live", offline: "text-faint", connecting: "text-warn", reconnecting: "text-warn", lost: "text-danger" };

/* What each preset carries, for the panel's sensor chips and the map's sources list. */
export const PRESET_SENSORS = { cameraRcCar: ["camera"], lidarRcCar: ["LiDAR"], gripperRcCar: ["gripper"] };
