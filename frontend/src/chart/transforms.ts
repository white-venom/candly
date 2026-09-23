import {
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import type { Candle, Forecast, IndicatorSeries, PatternSignal } from "../api/types";
import type { ChartTheme } from "./chartTheme";

// Times go in exactly as the API sends them (UTC seconds); IST lives only in the formatters.
export const asTime = (unix: number) => unix as UTCTimestamp;

/** Closed bars plus the forming bar, if it is newer than the last closed one. */
export function barsWithForming(candles: Candle[], forming: Candle | null): { bars: Candle[]; formingTime: number | null } {
  const last = candles.at(-1);
  if (forming && (!last || forming.time > last.time)) return { bars: [...candles, forming], formingTime: forming.time };
  return { bars: candles, formingTime: null };
}

/** The bar shown in the legend readout. */
export type HoverBar = Omit<Candle, "volume"> & { volume: number | null; forming: boolean };

export function latestBar(candles: Candle[], forming: Candle | null): HoverBar | null {
  const { bars, formingTime } = barsWithForming(candles, forming);
  const last = bars.at(-1);
  return last ? { ...last, forming: last.time === formingTime } : null;
}

export function candleData(candles: Candle[], forming: Candle | null, theme: ChartTheme): CandlestickData<Time>[] {
  const { bars, formingTime } = barsWithForming(candles, forming);
  return bars.map((c) => {
    const bar: CandlestickData<Time> = { time: asTime(c.time), open: c.open, high: c.high, low: c.low, close: c.close };
    return c.time === formingTime ? { ...bar, ...theme.forming } : bar;
  });
}

export function volumeData(candles: Candle[], forming: Candle | null, theme: ChartTheme): HistogramData<Time>[] {
  const { bars, formingTime } = barsWithForming(candles, forming);
  return bars.map((c) => ({
    time: asTime(c.time),
    value: c.volume,
    color: c.time === formingTime ? theme.volume.forming : c.close >= c.open ? theme.volume.up : theme.volume.down,
  }));
}

/**
 * Confirmed signals: filled arrows (a square when neutral). Forming signals: dimmed circles labelled "forming".
 * Only bars on the chart get a marker; labels are kept for forming, certified and the latest few signals.
 */
export function signalMarkers(
  signals: PatternSignal[],
  barTimes: ReadonlySet<number>,
  theme: ChartTheme,
  labelLatest = 12,
): SeriesMarker<Time>[] {
  const onChart = signals.filter((s) => barTimes.has(s.time)).sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
  const labelFrom = onChart.length - labelLatest;
  return onChart.map((s, i) => {
    const forming = s.state === "forming";
    const certified = s.stats?.certified === true;
    const showLabel = forming || certified || i >= labelFrom;
    const text = forming ? `forming · ${s.label}` : `${s.label}${certified ? " ✓" : ""}`;
    return {
      id: s.id,
      time: asTime(s.time),
      position: s.direction === "bullish" ? "belowBar" : "aboveBar",
      shape: forming ? "circle" : s.direction === "bullish" ? "arrowUp" : s.direction === "bearish" ? "arrowDown" : "square",
      color: theme.markers[forming ? "forming" : "confirmed"][s.direction],
      text: showLabel ? text : undefined,
    };
  });
}

// lightweight-charts throws on out-of-order times; the contract only guarantees order for candles.
function byTime<T extends { time: number }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => a.time - b.time);
}

export function ghostData(forecast: Forecast): CandlestickData<Time>[] {
  return byTime(forecast.ghost_candles).map((c) => ({ time: asTime(c.time), open: c.open, high: c.high, low: c.low, close: c.close }));
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
  /** the enabled catalog name this series belongs to (e.g. "macd" for "macd_signal") */
  group: string;
  slot: number;
  /** position inside its group: 0 solid, 1 dashed, 2 dotted */
  order: number;
  pane: number;
  kind: "line" | "histogram";
};

function groupFor(seriesName: string, enabled: EnabledIndicator[]): EnabledIndicator | undefined {
  const matches = enabled.filter((e) => seriesName === e.name || seriesName.startsWith(`${e.name}_`));
  return matches.sort((a, b) => b.name.length - a.name.length)[0];
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
