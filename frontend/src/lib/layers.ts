/** Which chart layers show; remembered in this browser (lib/prefs). */
export type Layers = { patterns: boolean; levels: boolean; forecast: boolean; allLevels: boolean };

export const DEFAULT_LAYERS: Layers = { patterns: true, levels: true, forecast: true, allLevels: false };
