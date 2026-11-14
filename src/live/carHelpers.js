import { ddsPorts } from "./link.js";

// Drive and keepalive bodies always carry the operator's power; a stop never does.
export const driveBody = (orderedBody, power) => ({ ...orderedBody, power });
export const keepaliveBody = (targetBody, power) => ({ ...targetBody, power });

// Gripper buttons work only when discovery found a gripper interface on the car.
export const gripperEnabled = (robot) => robot.status === "connected" && Boolean(robot.gripperInterface);
export const gripperSliderShown = (robot) => robot.gripperInterface === "position";

export function notFoundHint(domainId, message) {
  const [a, b] = ddsPorts(domainId);
  return `${message} Expected ROS domain ${domainId}; the Pi and this laptop must allow UDP ${a}–${b}. See docs/robot-setup.md.`;
}

// What the connect reply adds to a robot, as a patch (never mutates).
export function connectPatch(found) {
  return {
    services: found.services || [],
    gripperInterface: found.gripper_interface ?? null,
    otherPublishers: found.other_publishers ?? 0,
    camera: found.camera ?? null,
  };
}
