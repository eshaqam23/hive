// R26: a car page streams only what the operator is looking at. Subscribing to a topic makes the car send
// it over Wi-Fi (and an image_transport topic makes the Pi encode), so nothing is pulled just because a
// page is open.
export const ALWAYS_WATCHED = ["/ros_data", "/esp_data"]; // the motor-command echo and the ESP reply
export const MAX_STREAM_TOPICS = 32; // the backend's cap per stream; it reports any extra as too_many

// The page's one SSE stream: the stage topic (a scan as data, an image as stats only), the link topics,
// then the rows the operator toggled on. Only topics the car has, once each, in that order.
export function watchSet({ listed, stage, watched = [] }) {
  const byName = new Map(listed.map((t) => [t.name, t]));
  const topics = [];
  const images = [];
  const add = (name) => {
    const topic = byName.get(name);
    if (!topic) return;
    const list = topic.kind === "image" ? images : topics;
    if (!list.includes(name)) list.push(name);
  };
  if (stage) add(stage.name);
  ALWAYS_WATCHED.forEach(add);
  watched.forEach(add);
  return { topics: topics.slice(0, MAX_STREAM_TOPICS), images: images.slice(0, MAX_STREAM_TOPICS) };
}
