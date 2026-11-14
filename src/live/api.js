// Pure URL builders and the camera POST wrapper; the caller owns fetch/EventSource.
const query = (robot) => `ip=${encodeURIComponent(robot.host)}&domain_id=${encodeURIComponent(robot.domainId)}`;

export function streamUrl(base, robot, topics) {
  return `${base}/robots/stream?${query(robot)}&topics=${encodeURIComponent(topics.join(","))}`;
}

export function cameraUrl(base, robot, topic, { forceFull = false } = {}) {
  return `${base}/robots/camera.mjpg?${query(robot)}&topic=${encodeURIComponent(topic)}${forceFull ? "&force_full=1" : ""}`;
}

export async function postCamera(postApi, robot, { quality, depth }) {
  const response = await postApi("/robots/camera", { ip: robot.host, domain_id: robot.domainId, quality, depth });
  return response && response.camera !== undefined ? response.camera : response;
}
