import { useEffect, useState } from "react";
import { streamUrl } from "./api.js";

let factory = null;
// Tests inject a fake EventSource; pass null to restore the browser one.
export function setEventSourceFactory(fn) {
  factory = fn;
}
const open = (url) => (factory ? factory(url) : new EventSource(url));

export const RECONNECT_MS = [1000, 2000, 5000];

const EMPTY = { payloads: {}, link: null, stale: {}, imageStats: {}, topicErrors: {}, error: null };

// Exactly one SSE connection per car page, only while the robot is connected, so the browser's
// per-host connection limit stays free for drive requests and the camera. Never pass image topics in
// `topics`; the ones in `images` get stats and a stale flag only. A dropped stream is reopened with
// backoff (1, 2, 5 s). Topics the backend skips arrive as status events with an error code.
export function useLiveStream({ base, robot, topics, images = [], enabled }) {
  const [state, setState] = useState(EMPTY);
  const key = topics.join(",");
  const imageKey = images.join(",");
  const connected = robot?.status === "connected";
  const host = robot?.host;
  const domainId = robot?.domainId;

  useEffect(() => {
    setState(EMPTY);
    if (!enabled || !connected || !(key || imageKey)) return undefined;
    let source = null;
    let timer = 0;
    let failures = 0;
    let disposed = false;
    const url = streamUrl(base, { host, domainId }, key ? key.split(",") : [], imageKey ? imageKey.split(",") : []);
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
        setState((s) => ({ ...s, link }));
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
  }, [base, host, domainId, connected, enabled, key, imageKey]);

  return state;
}
