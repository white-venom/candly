import { describe, expect, it } from "vitest";
import type { Level } from "../api/types";
import { T0, DAY, candle } from "../test/fixtures";
import { levelsToDraw, mergeLevels, selectLevels, typicalRange } from "./levels";

// Nifty's levels from the dev API: fifteen of them, bunched around the close.
const LEVELS: Level[] = [
  { price: 23466.8, label: "Prev day high", kind: "pdh" },
  { price: 23314.8, label: "Prev day low", kind: "pdl" },
  { price: 23414.3, label: "Prev day close", kind: "pdc" },
  { price: 23398.63, label: "Pivot", kind: "pivot" },
  { price: 23482.47, label: "R1", kind: "r1" },
  { price: 23550.63, label: "R2", kind: "r2" },
  { price: 23330.47, label: "S1", kind: "s1" },
  { price: 23246.63, label: "S2", kind: "s2" },
  { price: 23406.47, label: "CPR top", kind: "cpr_top" },
  { price: 23390.8, label: "CPR bottom", kind: "cpr_bottom" },
  { price: 23592.85, label: "Swing high", kind: "swing_high" },
  { price: 24378.6, label: "Swing high", kind: "swing_high" },
  { price: 23116.1, label: "Swing low", kind: "swing_low" },
];

describe("level selection", () => {
  it("keeps PDH, PDL, PDC, Pivot and only the nearest support and resistance", () => {
    const picked = selectLevels(LEVELS, 23420, "key");
    expect(picked.map((l) => l.kind)).toEqual(["pdh", "pdl", "pdc", "pivot", "cpr_top", "r1"]);
    expect(picked.length).toBeLessThanOrEqual(6);
  });

  it("keeps everything in All mode, and only the key four without a close", () => {
    expect(selectLevels(LEVELS, 23420, "all")).toHaveLength(LEVELS.length);
    expect(selectLevels(LEVELS, null, "key").map((l) => l.kind)).toEqual(["pdh", "pdl", "pdc", "pivot"]);
  });
});

describe("merging close levels", () => {
  it("joins levels within the threshold into one line at the leading level's price", () => {
    const merged = mergeLevels(selectLevels(LEVELS, 23420, "key"), 22);
    expect(merged.map((m) => m.label)).toEqual(["PDH·R1", "PDC·Pivot·TC", "PDL"]);
    expect(merged[0].price).toBe(23466.8);
    expect(merged[1].kinds).toEqual(["pdc", "pivot", "cpr_top"]);
  });

  it("doesn't chain: a group is measured from its top level", () => {
    const chain: Level[] = [
      { price: 100, label: "", kind: "pdh" },
      { price: 90, label: "", kind: "r1" },
      { price: 80, label: "", kind: "r2" },
    ];
    expect(mergeLevels(chain, 12).map((m) => m.label)).toEqual(["PDH·R1", "R2"]);
  });

  it("uses 0.15 × the median bar range as the threshold", () => {
    const bars = [candle(T0 - 2 * DAY, 100, 104), candle(T0 - DAY, 104, 101), candle(T0, 101, 103)];
    expect(typicalRange(bars)).toBe(7); // ranges 8, 7, 6
    expect(typicalRange([])).toBeNull();
    const levels: Level[] = [
      { price: 110, label: "", kind: "pdh" },
      { price: 109, label: "", kind: "r1" },
      { price: 100, label: "", kind: "pdl" },
    ];
    // 0.15 × 7 = 1.05: 110 and 109 merge, 100 stays apart
    expect(levelsToDraw(levels, bars, "all").map((l) => l.label)).toEqual(["PDH·R1", "PDL"]);
  });
});
