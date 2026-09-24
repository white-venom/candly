import {
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import type { Candle, Direction, Forecast, IndicatorSeries, PatternSignal } from "../api/types";
import type { ChartTheme } from "./chartTheme";
import { FAMILIES } from "./indicatorSelection";

// Times go in exactly as the API sends them (UTC seconds); IST lives only in the formatters.
export const asTime = (unix: number) => unix as UTCTimestamp;

/** Closed bars plus the forming bar, if it is newer than the last closed one. */
export function barsWithForming(candles: Candle[], forming: Candle | null): { bars: Candle[]; formingTime: number | null } {
  const last = candles.at(-1);
  if (forming && (!last || forming.time > last.time)) return { bars: [...candles, forming], formingTime: forming.time };
  return { bars: candles, formingTime: null };
}

/** The bar shown in the legend readout. `prevClose` gives its change. */
export type HoverBar = Omit<Candle, "volume"> & { volume: number | null; forming: boolean; prevClose: number | null };

/** What the crosshair is on: the bar, where it is on screen, and each indicator's value there. */
export type Hover = { bar: HoverBar; x: number; values: Record<string, number> };

export function latestBar(candles: Candle[], forming: Candle | null): HoverBar | null {
  const { bars, formingTime } = barsWithForming(candles, forming);
  const last = bars.at(-1);
  if (!last) return null;
  return { ...last, forming: last.time === formingTime, prevClose: bars.at(-2)?.close ?? null };
}

export function candleData(candles: Candle[], forming: Candle | null, theme: ChartTheme): CandlestickData<Time>[] {
  const { bars, formingTime } = barsWithForming(candles, forming);
  return bars.map((c) => {
    const bar: CandlestickData<Time> = { time: asTime(c.time), open: c.open, high: c.high, low: c.low, close: c.close };
    return c.time === formingTime ? { ...bar, ...theme.forming } : bar;
  });
}

/** Empty when no bar has volume (index data): zero-height columns would draw a dotted line. */
export function volumeData(candles: Candle[], forming: Candle | null, theme: ChartTheme): HistogramData<Time>[] {
  const { bars, formingTime } = barsWithForming(candles, forming);
  if (!bars.some((c) => c.volume > 0)) return [];
  return bars.map((c) => ({
    time: asTime(c.time),
    value: c.volume,
    color: c.time === formingTime ? theme.volume.forming : c.close >= c.open ? theme.volume.up : theme.volume.down,
  }));
}

/** One arrow on the chart. Several patterns on one bar and side share it; `stack` spaces out the rest. */
export type MarkerGlyph = { time: number; direction: Direction; filled: boolean; stack: number };

/**
 * Bullish below the bar, bearish and neutral above it. Filled when any pattern behind it is confirmed,
 * hollow when all are still forming. Bars that aren't on the chart get nothing.
 */
export function markerGlyphs(signals: PatternSignal[], barTimes: ReadonlySet<number>): MarkerGlyph[] {
  const byKey = new Map<string, MarkerGlyph>();
  for (const s of signals) {
    if (!barTimes.has(s.time)) continue;
    const key = `${s.time}|${s.direction}`;
    const confirmed = s.state === "confirmed";
    const existing = byKey.get(key);
    if (existing) existing.filled ||= confirmed;
    else byKey.set(key, { time: s.time, direction: s.direction, filled: confirmed, stack: 0 });
  }
  const glyphs = [...byKey.values()].sort((a, b) => a.time - b.time);
  // bearish sits nearest the high, neutral above it
  for (const g of glyphs) {
    if (g.direction === "neutral") g.stack = byKey.has(`${g.time}|bearish`) ? 1 : 0;
  }
  return glyphs;
}

/** Every signal on a bar, for the hover card. */
export function signalsAt(signals: PatternSignal[], time: number): PatternSignal[] {
  return signals.filter((s) => s.time === time);
}

// lightweight-charts throws on out-of-order times; the contract only guarantees order for candles.
function byTime<T extends { time: number }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => a.time - b.time);
}

/**
 * Ghost candles for the steps after `current`, the step drawn as the Expected box (lib/expected
 * currentStep; -1 when every step has closed). That step and any before it only keep their slots.
 */
export function ghostData(forecast: Forecast, current = 0): (CandlestickData<Time> | WhitespaceData<Time>)[] {
  const last = current === -1 ? Infinity : current;
  return byTime(forecast.ghost_candles).map((c, i) =>
    i <= last ? { time: asTime(c.time) } : { time: asTime(c.time), open: c.open, high: c.high, low: c.low, close: c.close },
  );
}

export type BandKey = "p10" | "p50" | "p90";
export const BAND_KEYS: BandKey[] = ["p10", "p50", "p90"];

/** The 10/50/90 lines, each starting from the reference close so the cone opens at the last closed bar. */
export function bandData(forecast: Forecast): Record<BandKey, LineData<Time>[]> {
  const out: Record<BandKey, LineData<Time>[]> = { p10: [], p50: [], p90: [] };
  const bands = byTime(forecast.bands);
  if (bands.length === 0) return out;
  const anchor = bands[0].time > forecast.ref_time;
  for (const key of BAND_KEYS) {
    if (anchor) out[key].push({ time: asTime(forecast.ref_time), value: forecast.ref_close });
    for (const b of bands) out[key].push({ time: asTime(b.time), value: b[key] });
  }
  return out;
}

export type EnabledIndicator = { name: string; slot: number };

export type IndicatorPlot = {
  series: IndicatorSeries;
  /** the enabled menu name this series belongs to (e.g. "macd" for "macd_signal") */
  group: string;
  slot: number;
  /** position inside its group: 0 solid, 1 dashed, 2 dotted */
  order: number;
  pane: number;
  kind: "line" | "histogram";
};

function groupFor(seriesName: string, enabled: EnabledIndicator[]): EnabledIndicator | undefined {
  const exact = enabled.find((e) => e.name === seriesName);
  if (exact) return exact;
  const family = enabled.find((e) => FAMILIES[e.name]?.members.includes(seriesName));
  if (family) return family;
  return enabled.filter((e) => seriesName.startsWith(`${e.name}_`)).sort((a, b) => b.name.length - a.name.length)[0];
}

/**
 * Price-pane series overlay the candles (pane 0). Every other indicator group gets its own pane,
 * in the order the user enabled them. Colour follows the group's slot, never its rank.
 */
export function planIndicators(series: IndicatorSeries[], enabled: EnabledIndicator[]): IndicatorPlot[] {
  const orderInGroup = new Map<string, number>();
  const paneOfGroup = new Map<string, number>();
  const draft = series.map((s) => {
    const owner = groupFor(s.name, enabled) ?? { name: s.name, slot: enabled.length };
    const order = orderInGroup.get(owner.name) ?? 0;
    orderInGroup.set(owner.name, order + 1);
    return { series: s, group: owner.name, slot: owner.slot, order };
  });

  const rank = (group: string) => {
    const i = enabled.findIndex((e) => e.name === group);
    return i === -1 ? enabled.length : i;
  };
  const oscillatorGroups = [...new Set(draft.filter((d) => d.series.pane !== "price").map((d) => d.group))].sort(
    (a, b) => rank(a) - rank(b),
  );
  oscillatorGroups.forEach((g, i) => paneOfGroup.set(g, i + 1));

  return draft
    .map((d) => ({
      ...d,
      pane: d.series.pane === "price" ? 0 : (paneOfGroup.get(d.group) ?? 1),
      kind: d.series.name.endsWith("_hist") ? ("histogram" as const) : ("line" as const),
    }))
    .sort((a, b) => a.pane - b.pane);
}

/** The newest value of each series, for the legend when the crosshair is off the chart. */
export function lastValues(plots: IndicatorPlot[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of plots) {
    const points = byTime(p.series.points);
    for (let i = points.length - 1; i >= 0; i--) {
      const v = points[i].value;
      if (v !== null) {
        out[p.series.name] = v;
        break;
      }
    }
  }
  return out;
}

const ORDER_STYLES = [LineStyle.Solid, LineStyle.Dashed, LineStyle.Dotted, LineStyle.SparseDotted];

export function lineStyleFor(order: number): LineStyle {
  return ORDER_STYLES[Math.min(order, ORDER_STYLES.length - 1)];
}

export function indicatorColor(slot: number, theme: ChartTheme): string {
  return theme.indicators[slot % theme.indicators.length];
}

export function linePoints(points: IndicatorSeries["points"]): (LineData<Time> | WhitespaceData<Time>)[] {
  return byTime(points).map((p) => (p.value === null ? { time: asTime(p.time) } : { time: asTime(p.time), value: p.value }));
}

export function histogramPoints(points: IndicatorSeries["points"], theme: ChartTheme): (HistogramData<Time> | WhitespaceData<Time>)[] {
  return byTime(points).map((p) =>
    p.value === null
      ? { time: asTime(p.time) }
      : { time: asTime(p.time), value: p.value, color: p.value >= 0 ? theme.histogram.up : theme.histogram.down },
  );
}
