import { useEffect, useRef, useState } from "react";
import { streamUrl } from "./api.js";
import { newerLink } from "./link.js";

let factory = null;
// Tests inject a fake EventSource; pass null to restore the browser one.
export function setEventSourceFactory(fn) {
  factory = fn;
}
const open = (url) => (factory ? factory(url) : new EventSource(url));

export const RECONNECT_MS = [1000, 2000, 5000];

const EMPTY = { payloads: {}, link: null, stale: {}, imageStats: {}, topicErrors: {}, error: null };

const only = (map, wanted) => Object.fromEntries(Object.entries(map).filter(([topic]) => wanted.has(topic)));

// When only the watched set changes, keep the link and whatever is still wanted, so other rows do not blank.
const keepWanted = (s, wanted) => ({
  ...EMPTY,
  link: s.link,
  payloads: only(s.payloads, wanted),
  stale: only(s.stale, wanted),
  imageStats: only(s.imageStats, wanted),
});

// Exactly one SSE connection per car page, only while the robot is connected, so the browser's
// per-host connection limit stays free for drive requests and the camera. It opens even with no topic
// at all, because it also carries the link (R27). Never pass image topics in `topics`; the ones in
// `images` get stats and a stale flag only. A change of topics closes the stream and opens one new one
// (R26). A dropped stream is reopened with backoff (1, 2, 5 s). Topics the backend skips arrive as
// status events with an error code. The link kept is the one with the highest seq (R28).
export function useLiveStream({ base, robot, topics, images = [], enabled }) {
  const [state, setState] = useState(EMPTY);
  const key = topics.join(",");
  const imageKey = images.join(",");
  const connected = robot?.status === "connected";
  const host = robot?.host;
  const domainId = robot?.domainId;
  const identity = `${base}|${host}|${domainId}|${connected}|${enabled}`;
  const lastIdentity = useRef(null);

  useEffect(() => {
    const sameRobot = lastIdentity.current === identity;
    lastIdentity.current = identity;
    const topicList = key ? key.split(",") : [];
    const imageList = imageKey ? imageKey.split(",") : [];
    const wanted = new Set([...topicList, ...imageList]);
    setState((s) => (sameRobot ? keepWanted(s, wanted) : EMPTY));
    if (!enabled || !connected) return undefined;
    let source = null;
    let timer = 0;
    let failures = 0;
    let disposed = false;
    const url = streamUrl(base, { host, domainId }, topicList, imageList);
    const parse = (event) => {
      try {
        return JSON.parse(event.data);
      } catch {
        return null;
      }
    };

    const connect = () => {
      source = open(url);
      const alive = () => {
        failures = 0;
        setState((s) => (s.error ? { ...s, error: null } : s));
      };
      source.addEventListener("data", (event) => {
        const payload = parse(event);
        if (!payload?.topic) return;
        alive();
        setState((s) => ({ ...s, payloads: { ...s.payloads, [payload.topic]: payload }, stale: { ...s.stale, [payload.topic]: false } }));
      });
      source.addEventListener("image_stats", (event) => {
        const stats = parse(event);
        if (!stats?.topic) return;
        alive();
        setState((s) => ({ ...s, imageStats: { ...s.imageStats, [stats.topic]: stats }, stale: { ...s.stale, [stats.topic]: false } }));
      });
      source.addEventListener("link", (event) => {
        const link = parse(event);
        if (!link) return;
        alive();
        setState((s) => {
          const newest = newerLink(s.link, link);
          return newest === s.link ? s : { ...s, link: newest };
        });
      });
      source.addEventListener("status", (event) => {
        const status = parse(event);
        if (!status?.topic) return;
        if (status.error) setState((s) => ({ ...s, topicErrors: { ...s.topicErrors, [status.topic]: status.error } }));
        else setState((s) => ({ ...s, stale: { ...s.stale, [status.topic]: Boolean(status.stale) } }));
      });
      source.onerror = () => {
        if (disposed) return;
        source.close();
        const delay = RECONNECT_MS[Math.min(failures, RECONNECT_MS.length - 1)];
        failures += 1;
        setState((s) => ({ ...s, error: "Live stream interrupted, reconnecting…" }));
        timer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      source?.close();
    };
  }, [base, host, domainId, connected, enabled, key, imageKey, identity]);

  return state;
}
