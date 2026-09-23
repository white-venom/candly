import type { Theme } from "./theme";

/**
 * The colour tokens from src/index.css, for canvas drawing (lightweight-charts can't read CSS variables).
 * DOM and SVG use the CSS variables directly. palette.test.ts keeps the two in sync.
 */
export type Tokens = {
  page: string;
  surface: string;
  raised: string;
  line: string;
  lineStrong: string;
  grid: string;
  ink: string;
  inkMuted: string;
  inkFaint: string;
  up: string;
  down: string;
  forming: string;
  accent: string;
  band: string;
  danger: string;
  /** neutral direction, as text */
  neutral: string;
  /** abstaining forecast marks (graphics only, 3:1): kept apart from ghost and forming colours */
  abstain: string;
  focus: string;
  /** --ind-1 to --ind-8: indicator lines, in a fixed CVD-checked order */
  indicators: readonly string[];
};

export const TOKENS: Record<Theme, Tokens> = {
  dark: {
    page: "#111110",
    surface: "#1a1a19",
    raised: "#242422",
    line: "#383835",
    lineStrong: "#6f6e67",
    grid: "#2a2a28",
    ink: "#f2f1ec",
    inkMuted: "#c3c2b7",
    inkFaint: "#9d9b92",
    up: "#26a69a",
    down: "#f06560",
    forming: "#f0b429",
    accent: "#6ea8fe",
    band: "#5cc8e8",
    danger: "#ff7b7b",
    neutral: "#a3a29a",
    abstain: "#bcc0ca",
    focus: "#6ea8fe",
    indicators: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  },
  light: {
    page: "#f4f4f1",
    surface: "#fcfcfb",
    raised: "#efeee9",
    line: "#d6d5ce",
    lineStrong: "#86857d",
    grid: "#e6e5df",
    ink: "#0b0b0b",
    inkMuted: "#52514e",
    inkFaint: "#63625d",
    up: "#08756a",
    down: "#c62f2f",
    forming: "#8a6200",
    accent: "#1d5fc2",
    band: "#0e6f8c",
    danger: "#b91c1c",
    neutral: "#6b6a64",
    abstain: "#83889a",
    focus: "#1d5fc2",
    indicators: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
  },
};

export const INDICATOR_SLOTS = 8;

/** Tailwind classes for indicator swatches, by slot (literal so Tailwind generates them). */
export const INDICATOR_BG = ["bg-ind-1", "bg-ind-2", "bg-ind-3", "bg-ind-4", "bg-ind-5", "bg-ind-6", "bg-ind-7", "bg-ind-8"];
