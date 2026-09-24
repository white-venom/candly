import { useState } from "react";
import { readPref, usePref } from "./prefs";

/** Which chart layers show; remembered in this browser (lib/prefs). */
export type Layers = { patterns: boolean; levels: boolean; forecast: boolean; allLevels: boolean };

export const DEFAULT_LAYERS: Layers = { patterns: true, levels: true, forecast: true, allLevels: false };

export const LAYERS_KEY = "candly.layers.v2";
const V1_KEY = "candly.layers";

/** Version 2 turns the forecast layer back on once for everyone; the other v1 choices carry over. */
function migratedLayers(): Layers {
  return { ...readPref(V1_KEY, DEFAULT_LAYERS), forecast: true };
}

export function useLayers() {
  const [fallback] = useState(migratedLayers);
  return usePref(LAYERS_KEY, fallback);
}
