import { useEffect, useState } from "react";
import { streamUrl } from "./api.js";

let factory = null;
// Tests inject a fake EventSource; pass null to restore the browser one.
export function setEventSourceFactory(fn) {
  factory = fn;
}
const open = (url) => (factory ? factory(url) : new EventSource(url));
const CLOSED = 2;

const EMPTY = { payloads: {}, link: null, stale: {}, error: null };

// One SSE connection per car page, only while the robot is connected. Never pass image topics.
// The backend refuses a whole stream if any one topic is unknown (HTTP 404, which closes the
// EventSource for good), so a refused multi-topic stream falls back to one stream per topic.
export function useLiveStream({ base, robot, topics, enabled }) {
  const [state, setState] = useState(EMPTY);
  const key = topics.join(",");
  const connected = robot?.status === "connected";
  const host = robot?.host;
  const domainId = robot?.domainId;

  useEffect(() => {
    setState(EMPTY);
    if (!enabled || !connected || !key) return undefined;
    const sources = new Set();
    let disposed = false;
    const parse = (event) => {
      try {
        return JSON.parse(event.data);
      } catch {
        return null;
      }
    };

    const connect = (names, canSplit) => {
      const source = open(streamUrl(base, { host, domainId }, names));
      sources.add(source);
      source.addEventListener("data", (event) => {
        const payload = parse(event);
        if (payload?.topic) setState((s) => ({ ...s, payloads: { ...s.payloads, [payload.topic]: payload }, stale: { ...s.stale, [payload.topic]: false }, error: null }));
      });
      source.addEventListener("link", (event) => {
        const link = parse(event);
        if (link) setState((s) => ({ ...s, link }));
      });
      source.addEventListener("status", (event) => {
        const status = parse(event);
        if (status?.topic) setState((s) => ({ ...s, stale: { ...s.stale, [status.topic]: Boolean(status.stale) } }));
      });
      source.onerror = () => {
        if (disposed) return;
        if (source.readyState === CLOSED && canSplit && names.length > 1) {
          sources.delete(source);
          names.forEach((name) => connect([name], false));
          return;
        }
        setState((s) => ({ ...s, error: "Live stream interrupted, retrying…" }));
      };
    };
    connect(key.split(","), true);
    return () => {
      disposed = true;
      sources.forEach((source) => source.close());
    };
  }, [base, host, domainId, connected, enabled, key]);

  return state;
}
