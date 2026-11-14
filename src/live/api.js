// Pure URL builders and the camera POST wrapper; the caller owns fetch/EventSource.
const query = (robot) => `ip=${encodeURIComponent(robot.host)}&domain_id=${encodeURIComponent(robot.domainId)}`;

// images: image topics shown on the page; the stream sends their stats and stale flag, never frames
export function streamUrl(base, robot, topics, images = []) {
  const shown = images.length ? `&images=${encodeURIComponent(images.join(","))}` : "";
  return `${base}/robots/stream?${query(robot)}&topics=${encodeURIComponent(topics.join(","))}${shown}`;
}

export function cameraUrl(base, robot, topic, { forceFull = false } = {}) {
  return `${base}/robots/camera.mjpg?${query(robot)}&topic=${encodeURIComponent(topic)}${forceFull ? "&force_full=1" : ""}`;
}

export async function postCamera(postApi, robot, { quality, depth }) {
  const response = await postApi("/robots/camera", { ip: robot.host, domain_id: robot.domainId, quality, depth });
  return response && response.camera !== undefined ? response.camera : response;
}
