const OTHERS = " · Someone else is also driving this car";
export const NOT_REPORTING = "Car is not reporting motor commands: is motor_commands running?";
const FRESH_MS = 2000; // a confirmation younger than this is shown green

const seqOf = (link) => (Number.isFinite(link?.seq) ? link.seq : -Infinity);

// R28: links arrive by three routes (drive and keepalive replies, the live stream, polls). The worker
// stamps each with a growing seq; keep whichever is newest, so a stale one never overwrites a newer one.
export function newerLink(current, incoming) {
  if (!incoming) return current ?? null;
  if (!current) return incoming;
  return seqOf(incoming) >= seqOf(current) ? incoming : current;
}

// The drive pad's line about the car. While driving (stop "idle") it shows the last line the car
// confirmed and its age; whenever ros_data is missing it says so plainly (final audit #6).
export function describeLink(link) {
  if (!link) return { level: "muted", text: "No link data yet" };
  const line = String(link.last_line ?? "").trimEnd();
  const age = Math.round(link.last_line_age_ms ?? 0);
  const confirmed = `Car confirmed ${line} · ${age} ms ago`;
  let level = "muted";
  let text = "Ready";
  switch (link.stop) {
    case "confirmed":
      text = confirmed;
      level = "ok";
      break;
    case "pending":
      text = "Stopping…";
      break;
    case "unconfirmed":
      text = "Stop not confirmed: car may still be moving";
      level = "danger";
      break;
    case "unreported":
      text = NOT_REPORTING;
      level = "warn";
      break;
    default:
      if (link.last_line != null) {
        text = confirmed;
        level = age < FRESH_MS ? "ok" : "muted";
      }
  }
  if (link.reporting === false && level !== "danger") {
    text = NOT_REPORTING;
    level = "warn";
  }
  if (link.other_publishers > 0) text += OTHERS;
  return { level, text };
}

const STOP_MODES = {
  confirmed: { text: "Stopped", level: "ok" },
  unconfirmed: { text: "Stop not confirmed: car may still be moving", level: "danger" },
  unreported: { text: "Stop sent · car is not reporting motor commands", level: "warn" },
  pending: { text: "Stopping…", level: "muted" },
};

// The robot's mode line. After a stop (robot.stopping set) it follows the car's own confirmation and
// never says "Stopped" before it (final audit #5). stopping.seq is the stop reply's link seq: only a
// link at least that new speaks for this stop; before the reply arrives (seq null) none does.
export function displayMode(robot, link) {
  const stopping = robot?.status === "connected" ? robot.stopping : null;
  if (!stopping) return { text: robot?.mode ?? "", level: "muted" };
  const floor = stopping.seq ?? Infinity;
  const state = link && seqOf(link) >= floor ? link.stop : "pending";
  return STOP_MODES[state] ?? STOP_MODES.pending;
}

// Home cards show a danger badge for a connected car whose newest link says the stop is not confirmed.
export const stopUnconfirmed = (robot, link) => robot?.status === "connected" && link?.stop === "unconfirmed";

// DDS UDP port range for a domain id (RTPS defaults), shown in the connect-failure hint.
export function ddsPorts(domainId) {
  const start = 7400 + 250 * domainId;
  return [start, start + 249];
}
