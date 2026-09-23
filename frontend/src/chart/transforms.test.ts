import { LineStyle } from "lightweight-charts";
import { describe, expect, it } from "vitest";
import type { IndicatorSeries } from "../api/types";
import { DAY, T0, candle, candles, catalog, forecast, signal } from "../test/fixtures";
import { chartTheme } from "./chartTheme";
import { menuEntries, toggleIndicator } from "./indicatorSelection";
import {
  bandData,
  candleData,
  ghostData,
  lastValues,
  latestBar,
  linePoints,
  lineStyleFor,
  markerGlyphs,
  planIndicators,
  signalsAt,
  volumeData,
} from "./transforms";

const dark = chartTheme("dark");
const bars = candles.candles;
const times = new Set(bars.map((b) => b.time));

describe("candles", () => {
  it("keeps UTC times untouched and appends a newer forming bar with its own colours", () => {
    const forming = candle(T0 + DAY, 103, 105);
    const data = candleData(bars, forming, dark);
    expect(data.map((d) => d.time)).toEqual([T0 - 2 * DAY, T0 - DAY, T0, T0 + DAY]);
    expect(data[2]).not.toHaveProperty("borderColor");
    expect(data[3]).toMatchObject({ close: 105, ...dark.forming });
  });

  it("ignores a forming bar that is not newer than the last closed bar", () => {
    expect(candleData(bars, candle(T0, 1, 2), dark)).toHaveLength(3);
  });

  it("colours volume by candle direction, and draws none when no bar has volume", () => {
    const vol = volumeData(bars, candle(T0 + DAY, 103, 105, 50), dark);
    expect(vol.map((v) => v.color)).toEqual([dark.volume.up, dark.volume.down, dark.volume.up, dark.volume.forming]);
    expect(vol[3].value).toBe(50);
    expect(volumeData(bars.map((b) => ({ ...b, volume: 0 })), null, dark)).toEqual([]);
  });

  it("reports the latest bar with the close before it", () => {
    expect(latestBar(bars, candle(T0 + DAY, 103, 105))).toMatchObject({ time: T0 + DAY, forming: true, prevClose: 103 });
    expect(latestBar(bars, null)).toMatchObject({ time: T0, forming: false, prevClose: 101 });
    expect(latestBar([], null)).toBeNull();
  });
});

describe("pattern markers", () => {
  it("draws a confirmed bullish pattern as a filled arrow under the bar", () => {
    expect(markerGlyphs([signal()], times)).toEqual([{ time: T0, direction: "bullish", filled: true, stack: 0 }]);
  });

  it("draws forming patterns hollow, and never labels anything", () => {
    const [g] = markerGlyphs([signal({ state: "forming", direction: "bearish", label: "Shooting star" })], times);
    expect(g).toEqual({ time: T0, direction: "bearish", filled: false, stack: 0 });
    expect(g).not.toHaveProperty("text");
  });

  it("gives several patterns on one bar and side a single arrow, filled if any is confirmed", () => {
    const glyphs = markerGlyphs(
      [signal({ pattern: "hammer" }), signal({ pattern: "bullish_harami", state: "forming" }), signal({ pattern: "doji", direction: "neutral" })],
      times,
    );
    expect(glyphs).toHaveLength(2);
    expect(glyphs.find((g) => g.direction === "bullish")).toMatchObject({ filled: true });
  });

  it("stacks a neutral diamond above a bearish arrow on the same bar", () => {
    const glyphs = markerGlyphs([signal({ direction: "bearish" }), signal({ pattern: "doji", direction: "neutral" })], times);
    expect(glyphs.find((g) => g.direction === "neutral")?.stack).toBe(1);
    expect(glyphs.find((g) => g.direction === "bearish")?.stack).toBe(0);
  });

  it("sorts by time and skips bars not on the chart", () => {
    const glyphs = markerGlyphs([signal({ time: T0 }), signal({ time: T0 - 2 * DAY }), signal({ time: T0 - 9 * DAY })], times);
    expect(glyphs.map((g) => g.time)).toEqual([T0 - 2 * DAY, T0]);
  });

  it("finds the patterns on a hovered bar", () => {
    const list = [signal({ time: T0 }), signal({ time: T0 - DAY, pattern: "doji" })];
    expect(signalsAt(list, T0 - DAY).map((s) => s.pattern)).toEqual(["doji"]);
  });
});

describe("forecast drawing", () => {
  it("puts ghost candles at the forecast's future times, without volume", () => {
    const ghosts = ghostData(forecast);
    expect(ghosts.map((g) => g.time)).toEqual([T0 + DAY, T0 + 2 * DAY, T0 + 3 * DAY]);
    expect(ghosts[0]).toEqual({ time: T0 + DAY, open: 103, high: 106, low: 101, close: 104 });
  });

  it("builds the 10/50/90 band from the reference close", () => {
    const band = bandData(forecast);
    expect(band.p10.map((p) => p.value)).toEqual([103, 101, 100, 99]);
    expect(band.p50.map((p) => p.value)).toEqual([103, 104, 103.5, 104.5]);
    expect(band.p90.map((p) => p.value)).toEqual([103, 106, 107, 109]);
    expect(band.p90[0].time).toBe(forecast.ref_time);
  });

  it("skips the anchor when bands already start at the reference bar, and handles no bands", () => {
    const sameStart = { ...forecast, bands: [{ time: T0, p10: 1, p50: 2, p90: 3 }] };
    expect(bandData(sameStart).p50).toEqual([{ time: T0, value: 2 }]);
    expect(bandData({ ...forecast, bands: [] })).toEqual({ p10: [], p50: [], p90: [] });
  });
});

describe("indicators", () => {
  const series = (name: string, pane: IndicatorSeries["pane"]): IndicatorSeries => ({
    name,
    label: name.toUpperCase(),
    pane,
    points: [
      { time: T0 - DAY, value: null },
      { time: T0, value: 1.5 },
    ],
  });

  it("overlays price indicators and gives each oscillator family its own pane in enable order", () => {
    const enabled = [
      { name: "rsi14", slot: 2 },
      { name: "ema20", slot: 0 },
      { name: "macd", slot: 5 },
      { name: "adx", slot: 3 },
    ];
    const plots = planIndicators(
      [
        series("ema20", "price"),
        series("macd", "oscillator"),
        series("macd_signal", "oscillator"),
        series("macd_hist", "oscillator"),
        series("rsi14", "oscillator"),
        series("adx14", "oscillator"),
        series("plus_di14", "oscillator"),
      ],
      enabled,
    );
    const by = Object.fromEntries(plots.map((p) => [p.series.name, p]));
    expect(by.ema20).toMatchObject({ pane: 0, slot: 0, kind: "line" });
    expect(by.rsi14).toMatchObject({ pane: 1, slot: 2 });
    expect(by.macd).toMatchObject({ pane: 2, slot: 5, order: 0, group: "macd" });
    expect(by.macd_signal).toMatchObject({ pane: 2, slot: 5, order: 1, group: "macd" });
    expect(by.macd_hist).toMatchObject({ pane: 2, kind: "histogram" });
    expect(by.adx14).toMatchObject({ pane: 3, group: "adx" });
    expect(by.plus_di14).toMatchObject({ pane: 3, group: "adx", order: 1 });
    expect(lineStyleFor(by.macd_signal.order)).toBe(LineStyle.Dashed);
  });

  it("turns missing values into whitespace instead of zeros, and reads the newest value", () => {
    expect(linePoints(series("ema20", "price").points)).toEqual([{ time: T0 - DAY }, { time: T0, value: 1.5 }]);
    const [plot] = planIndicators([series("ema20", "price")], [{ name: "ema20", slot: 0 }]);
    expect(lastValues([plot])).toEqual({ ema20: 1.5 });
  });

  it("keeps each indicator's colour slot when another is switched off", () => {
    let enabled = toggleIndicator([], "ema20");
    enabled = toggleIndicator(enabled, "ema50");
    enabled = toggleIndicator(enabled, "rsi14");
    enabled = toggleIndicator(enabled, "ema50");
    expect(enabled).toEqual([
      { name: "ema20", slot: 0 },
      { name: "rsi14", slot: 2 },
    ]);
    expect(toggleIndicator(enabled, "macd")).toContainEqual({ name: "macd", slot: 1 });
  });

  it("folds each family's lines into one menu entry", () => {
    const full = [
      ...catalog,
      { name: "macd_signal", label: "MACD signal (9)", pane: "oscillator" as const, group: "momentum" as const },
      { name: "bb_upper", label: "Bollinger upper", pane: "price" as const, group: "volatility" as const },
      { name: "bb_lower", label: "Bollinger lower", pane: "price" as const, group: "volatility" as const },
    ];
    expect(menuEntries(full).map((e) => e.name)).toEqual(["ema20", "ema50", "rsi14", "macd", "bb"]);
    expect(menuEntries(full).find((e) => e.name === "bb")?.label).toBe("Bollinger Bands (20, 2)");
  });
});
