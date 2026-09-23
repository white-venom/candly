import type { Candle, Level } from "../api/types";

export const LEVEL_SHORT: Record<Level["kind"], string> = {
  pdh: "PDH",
  pdl: "PDL",
  pdc: "PDC",
  pivot: "Pivot",
  r1: "R1",
  r2: "R2",
  s1: "S1",
  s2: "S2",
  cpr_top: "TC",
  cpr_bottom: "BC",
  vwap: "VWAP",
  swing_high: "Swing H",
  swing_low: "Swing L",
};

/** Which label leads a merged line, and whose price it is drawn at. */
const PRIORITY: Level["kind"][] = ["pdh", "pdl", "pdc", "pivot", "r1", "s1", "r2", "s2", "cpr_top", "cpr_bottom", "vwap", "swing_high", "swing_low"];

const KEY: Level["kind"][] = ["pdh", "pdl", "pdc", "pivot"];

export type LevelMode = "key" | "all";

/** One line on the chart; nearby levels share it, e.g. "PDH·R1". */
export type DrawnLevel = { price: number; label: string; kinds: Level["kind"][] };

/** Median high − low of the last `n` bars: the yardstick for "close together". */
export function typicalRange(candles: Candle[], n = 50): number | null {
  const ranges = candles
    .slice(-n)
    .map((c) => c.high - c.low)
    .filter((r) => r > 0)
    .sort((a, b) => a - b);
  if (ranges.length === 0) return null;
  const mid = Math.floor(ranges.length / 2);
  return ranges.length % 2 ? ranges[mid] : (ranges[mid - 1] + ranges[mid]) / 2;
}

/**
 * Key mode: PDH, PDL, PDC and Pivot, plus the nearest other level below the last close (support)
 * and above it (resistance) — at most six. All mode: everything the backend sent.
 */
export function selectLevels(levels: Level[], lastClose: number | null, mode: LevelMode): Level[] {
  if (mode === "all") return levels;
  const key = levels.filter((l) => KEY.includes(l.kind));
  if (lastClose === null) return key;
  const rest = levels.filter((l) => !KEY.includes(l.kind));
  const support = rest.filter((l) => l.price < lastClose).sort((a, b) => b.price - a.price)[0];
  const resistance = rest.filter((l) => l.price > lastClose).sort((a, b) => a.price - b.price)[0];
  return [...key, ...(support ? [support] : []), ...(resistance ? [resistance] : [])];
}

const rank = (kind: Level["kind"]) => PRIORITY.indexOf(kind);

/** Levels within `threshold` of a group's top level join that group and share one line and label. */
export function mergeLevels(levels: Level[], threshold: number): DrawnLevel[] {
  const sorted = [...levels].sort((a, b) => b.price - a.price);
  const groups: Level[][] = [];
  for (const level of sorted) {
    const group = groups.at(-1);
    if (group && group[0].price - level.price <= threshold) group.push(level);
    else groups.push([level]);
  }
  return groups.map((group) => {
    const members = [...group].sort((a, b) => rank(a.kind) - rank(b.kind));
    const labels = [...new Set(members.map((l) => LEVEL_SHORT[l.kind]))];
    return { price: members[0].price, label: labels.join("·"), kinds: members.map((l) => l.kind) };
  });
}

/** What the chart draws: the selection, merged within 0.15 × the typical bar range. */
export function levelsToDraw(levels: Level[], candles: Candle[], mode: LevelMode): DrawnLevel[] {
  const lastClose = candles.at(-1)?.close ?? null;
  const range = typicalRange(candles);
  return mergeLevels(selectLevels(levels, lastClose, mode), range === null ? 0 : 0.15 * range);
}
