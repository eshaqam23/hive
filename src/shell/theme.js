// The page theme: dark (live-data's own look, the default) or light. Kept in localStorage per viewer;
// a blocked or empty store just means dark. A tiny store so the map can redraw its canvas on a change.
import { useSyncExternalStore } from "react";

const KEY = "hive:theme";
const listeners = new Set();
let current = null;

function stored() {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function getTheme() {
  if (current === null) current = stored();
  return current;
}

export function applyTheme(theme = getTheme()) {
  if (typeof document !== "undefined") document.documentElement.dataset.theme = theme;
}

export function setTheme(theme) {
  current = theme === "light" ? "light" : "dark";
  try {
    localStorage.setItem(KEY, current);
  } catch {
    /* private window: the choice lasts for this page only */
  }
  applyTheme(current);
  listeners.forEach((fn) => fn());
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const useTheme = () => useSyncExternalStore(subscribe, getTheme, () => "dark");
