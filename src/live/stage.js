import { stageTopics } from "../viewers/registry.js";

export const MODEL_TAB = "3D model";

// The Live view's tabs: every image and scan topic (images first), plus a Topics row the operator
// clicked when it is not one of them.
export function stageTabs(discovered, selected) {
  const staged = stageTopics(discovered);
  const picked = discovered.find((t) => t.name === selected?.name);
  return picked && !staged.some((t) => t.name === picked.name) ? [...staged, picked] : staged;
}

// The topic on stage: the chosen tab, else the first; null for the 3D model or when there is none.
export function currentTab(tabs, active) {
  if (active === MODEL_TAB) return null;
  return tabs.find((t) => t.name === active) ?? tabs[0] ?? null;
}
