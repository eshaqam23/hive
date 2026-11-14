const OTHERS = " · Someone else is also driving this car";

export function describeLink(link) {
  if (!link) return { level: "muted", text: "No link data yet" };
  let level = "muted";
  let text = "Ready";
  switch (link.stop) {
    case "confirmed": {
      const line = String(link.last_line ?? "").trimEnd();
      text = `Car confirmed ${line} · ${Math.round(link.last_line_age_ms ?? 0)} ms ago`;
      level = "ok";
      break;
    }
    case "pending":
      text = "Stopping…";
      break;
    case "unconfirmed":
      text = "Stop not confirmed: car may still be moving";
      level = "danger";
      break;
    case "unreported":
      text = "Car is not reporting motor commands: is motor_commands running?";
      level = "warn";
      break;
    default:
  }
  if (link.other_publishers > 0) text += OTHERS;
  return { level, text };
}

// DDS UDP port range for a domain id (RTPS defaults), shown in the connect-failure hint.
export function ddsPorts(domainId) {
  const start = 7400 + 250 * domainId;
  return [start, start + 249];
}
