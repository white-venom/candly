import { LineStyle } from "lightweight-charts";
import { describe, expect, it } from "vitest";
import type { IndicatorSeries } from "../api/types";
import { DAY, T0, candle, candles, forecast, signal } from "../test/fixtures";
import { chartTheme } from "./chartTheme";
import { toggleIndicator } from "./indicatorSelection";
import {
  bandData,
  candleData,
  ghostData,
  latestBar,
  linePoints,
  lineStyleFor,
  planIndicators,
  signalMarkers,
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

  it("colours volume by candle direction", () => {
    const vol = volumeData(bars, candle(T0 + DAY, 103, 105, 50), dark);
    expect(vol.map((v) => v.color)).toEqual([dark.volume.up, dark.volume.down, dark.volume.up, dark.volume.forming]);
    expect(vol[3].value).toBe(50);
  });

  it("reports the forming bar as the latest", () => {
    expect(latestBar(bars, candle(T0 + DAY, 103, 105))).toMatchObject({ time: T0 + DAY, forming: true });
    expect(latestBar(bars, null)).toMatchObject({ time: T0, forming: false });
    expect(latestBar([], null)).toBeNull();
  });
});

describe("signal markers", () => {
  it("draws confirmed signals as filled arrows in the full colour", () => {
    const [m] = signalMarkers([signal()], times, dark);
    expect(m).toMatchObject({ time: T0, shape: "arrowUp", position: "belowBar", color: dark.markers.confirmed.bullish, text: "Hammer" });
  });

  it("draws forming signals as dimmed circles labelled forming", () => {
    const [m] = signalMarkers([signal({ state: "forming", direction: "bearish", label: "Shooting star" })], times, dark);
    expect(m).toMatchObject({ shape: "circle", position: "aboveBar", color: dark.markers.forming.bearish, text: "forming · Shooting star" });
    expect(m.color).not.toBe(dark.markers.confirmed.bearish);
  });

  it("marks certified signals and neutral ones distinctly", () => {
    const certified = signal({ stats: { ...signal().stats!, certified: true } });
    const neutral = signal({ direction: "neutral", pattern: "doji", label: "Doji", time: T0 - DAY });
    const markers = signalMarkers([certified, neutral], times, dark);
    expect(markers.map((m) => m.text)).toEqual(["Doji", "Hammer ✓"]);
    expect(markers[0].shape).toBe("square");
  });

  it("sorts by time, skips bars not on the chart and labels only the latest few", () => {
    const signals = [signal({ time: T0 }), signal({ time: T0 - 2 * DAY }), signal({ time: T0 - 9 * DAY })];
    const markers = signalMarkers(signals, times, dark, 1);
    expect(markers.map((m) => m.time)).toEqual([T0 - 2 * DAY, T0]);
    expect(markers.map((m) => m.text)).toEqual([undefined, "Hammer"]);
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

  it("overlays price indicators and gives each oscillator its own pane in enable order", () => {
    const enabled = [
      { name: "rsi14", slot: 2 },
      { name: "ema20", slot: 0 },
      { name: "macd", slot: 5 },
    ];
    const plots = planIndicators(
      [series("ema20", "price"), series("macd", "oscillator"), series("macd_signal", "oscillator"), series("macd_hist", "oscillator"), series("rsi14", "oscillator")],
      enabled,
    );
    const by = Object.fromEntries(plots.map((p) => [p.series.name, p]));
    expect(by.ema20).toMatchObject({ pane: 0, slot: 0, kind: "line" });
    expect(by.rsi14).toMatchObject({ pane: 1, slot: 2 });
    expect(by.macd).toMatchObject({ pane: 2, slot: 5, order: 0, group: "macd" });
    expect(by.macd_signal).toMatchObject({ pane: 2, slot: 5, order: 1, group: "macd" });
    expect(by.macd_hist).toMatchObject({ pane: 2, kind: "histogram" });
    expect(plots.map((p) => p.pane)).toEqual([...plots.map((p) => p.pane)].sort());
    expect(lineStyleFor(by.macd_signal.order)).toBe(LineStyle.Dashed);
  });

  it("turns missing values into whitespace instead of zeros", () => {
    expect(linePoints(series("ema20", "price").points)).toEqual([{ time: T0 - DAY }, { time: T0, value: 1.5 }]);
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
});
