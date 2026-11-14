export const VIEWERS = [
  { match: { kind: "image" }, panel: "CameraPanel" },
  { match: { kind: "scan" }, panel: "LidarPanel" },
  { match: { kind: "value" }, panel: "ValuePanel" },
  { match: { kind: "generic" }, panel: "GenericPanel" },
];

// Specificity: topic match beats type match beats kind match.
const FIELDS = [["topic", 3], ["type", 2], ["kind", 1]];

export function resolvePanel({ kind, type, topic } = {}) {
  const subject = { kind, type, topic };
  let best = null;
  let bestScore = 0;
  for (const viewer of VIEWERS) {
    const entries = Object.entries(viewer.match);
    if (!entries.length || !entries.every(([key, value]) => subject[key] === value)) continue;
    const score = Math.max(...FIELDS.filter(([key]) => key in viewer.match).map(([, weight]) => weight));
    if (score > bestScore) {
      best = viewer;
      bestScore = score;
    }
  }
  return best ? best.panel : "GenericPanel";
}

export function stageTopics(topics) {
  const names = new Set(topics.map((t) => t.name));
  const staged = topics.filter(
    (t) => (t.kind === "image" || t.kind === "scan") && !(names.has(`${t.name}/compressed`) && t.kind === "image"),
  );
  return [...staged.filter((t) => t.kind === "image"), ...staged.filter((t) => t.kind !== "image")];
}

const HIDDEN = new Set(["/rosout", "/parameter_events"]);

export function liveListTopics(topics) {
  return topics.filter((t) => t.kind !== "image" && !HIDDEN.has(t.name));
}
