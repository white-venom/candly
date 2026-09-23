import { describe, expect, it } from "vitest";
import { GHOST_BODY_ALPHA } from "../chart/chartTheme";
import css from "../index.css?raw";
import { composite, contrastRatio, deltaE } from "./color";
import { TOKENS, type Tokens } from "./palette";
import type { Theme } from "./theme";

function cssVars(selector: string): Record<string, string> {
  for (const [, selectors, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!selectors.split(",").map((s) => s.trim()).includes(selector)) continue;
    return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]));
  }
  throw new Error(`no CSS block for ${selector}`);
}

function expectedVars(t: Tokens): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(t)) {
    if (key === "indicators") continue;
    out[key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)] = value as string;
  }
  t.indicators.forEach((hex, i) => (out[`ind-${i + 1}`] = hex));
  return out;
}

const THEMES: Theme[] = ["dark", "light"];
const AA_TEXT = 4.5;
const AA_GRAPHIC = 3;

describe.each(THEMES)("%s palette", (theme) => {
  const t = TOKENS[theme];

  it("matches the CSS variables exactly", () => {
    expect(cssVars(`:root[data-theme="${theme}"]`)).toEqual(expectedVars(t));
  });

  it.each(["ink", "inkMuted", "inkFaint", "up", "down", "forming", "accent", "danger", "neutral"] as const)(
    "%s text passes AA (4.5:1) on every surface",
    (key) => {
      for (const bg of [t.page, t.surface, t.raised]) expect(contrastRatio(t[key], bg)).toBeGreaterThanOrEqual(AA_TEXT);
    },
  );

  it.each(["up", "down", "forming", "band", "danger", "neutral", "abstain", "lineStrong", "focus"] as const)(
    "%s marks and control borders reach 3:1 against the chart and page",
    (key) => {
      for (const bg of [t.surface, t.page]) expect(contrastRatio(t[key], bg)).toBeGreaterThanOrEqual(AA_GRAPHIC);
    },
  );

  it("keeps green and red candles apart", () => {
    expect(deltaE(t.up, t.down)).toBeGreaterThan(25);
  });

  it("keeps forming, ghost and abstain states distinguishable", () => {
    // Outlines are what tell the states apart: forming (amber), ghost (up/down), abstain (grey).
    const outlines = { forming: t.forming, ghostUp: t.up, ghostDown: t.down, abstain: t.abstain };
    const names = Object.keys(outlines) as (keyof typeof outlines)[];
    for (const a of names) {
      for (const b of names) if (a < b) expect(deltaE(outlines[a], outlines[b]), `${a} vs ${b}`).toBeGreaterThanOrEqual(15);
    }
    // A ghost body is far lighter than a real candle's, so a projection never passes for a traded bar.
    for (const c of [t.up, t.down, t.abstain]) {
      expect(deltaE(composite(c, GHOST_BODY_ALPHA, t.surface), c)).toBeGreaterThanOrEqual(15);
    }
  });
});
