import { describe, expect, it, vi } from "vitest";
import { T0, DAY, candle, candles, forecast } from "../test/fixtures";
import { chartTheme } from "./chartTheme";
import { PriceChartController } from "./PriceChartController";
import type { PriceTag } from "./primitives";

type Fake = Record<string, ReturnType<typeof vi.fn>>;

const lwc = vi.hoisted(() => {
  const fakeSeries = (type: string) => ({
    type,
    applyOptions: vi.fn(),
    setData: vi.fn(),
    attachPrimitive: vi.fn(),
    priceFormatter: () => ({ format: (p: number) => p.toFixed(2) }),
    priceScale: () => ({ applyOptions: vi.fn() }),
  });
  const state = { chart: null as unknown as Fake & { series: ReturnType<typeof fakeSeries>[] } };
  const createChart = vi.fn(() => {
    const series: ReturnType<typeof fakeSeries>[] = [];
    state.chart = {
      series,
      applyOptions: vi.fn(),
      addSeries: vi.fn((definition: { type: string }) => {
        const s = fakeSeries(definition.type);
        series.push(s);
        return s;
      }),
      removeSeries: vi.fn(),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      panes: vi.fn(() => []),
      timeScale: vi.fn(() => ({ setVisibleLogicalRange: vi.fn() })),
      subscribeCrosshairMove: vi.fn(),
      unsubscribeCrosshairMove: vi.fn(),
      remove: vi.fn(),
    } as never;
    return state.chart;
  });
  return { state, createChart };
});

vi.mock("lightweight-charts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("lightweight-charts")>()),
  createChart: lwc.createChart,
}));

type Primitive = { set: ReturnType<typeof vi.fn> } & Record<string, unknown>;

function setup() {
  lwc.createChart.mockClear();
  const controller = new PriceChartController(document.createElement("div"), "dark", "1D");
  const chart = lwc.state.chart;
  // creation order: volume, candles, p10, p50, p90, ghost
  const [volume, candleSeries, p10, p50, p90, ghost] = chart.series;
  // attach order: band fill, price tags, pattern markers
  const [bandFill, tags, markers] = candleSeries.attachPrimitive.mock.calls.map(([p]) => {
    vi.spyOn(p as Primitive, "set");
    return p as Primitive;
  });
  const lastTags = () => (tags.set.mock.lastCall?.[0] ?? []) as PriceTag[];
  return { controller, chart, volume, candleSeries, p10, p50, p90, ghost, bandFill, tags, markers, lastTags };
}

describe("PriceChartController", () => {
  it("re-colours everything in place on a theme switch, without recreating the chart or series", () => {
    const { controller, chart, volume, candleSeries, p50, ghost, lastTags } = setup();
    controller.setCandles(candles.candles, candle(T0 + DAY, 103, 105));
    controller.setLevels([{ price: 110, label: "PDH", kinds: ["pdh"] }]);
    controller.setForecast(forecast);
    const seriesBefore = chart.addSeries.mock.calls.length;

    controller.setTheme("light");
    const light = chartTheme("light");

    expect(lwc.createChart).toHaveBeenCalledTimes(1);
    expect(chart.addSeries.mock.calls.length).toBe(seriesBefore);
    expect(chart.remove).not.toHaveBeenCalled();
    expect(chart.applyOptions).toHaveBeenLastCalledWith(light.chart);
    expect(candleSeries.applyOptions).toHaveBeenLastCalledWith(light.candles);
    expect(ghost.applyOptions).toHaveBeenLastCalledWith(light.ghost);
    expect(p50.applyOptions).toHaveBeenLastCalledWith(light.band.p50);
    expect(lastTags().find((t) => t.label === "PDH")?.look).toEqual(light.level);
    expect(lastTags().find((t) => t.label === "Stop")?.look).toEqual(light.stop);

    const candleData = candleSeries.setData.mock.lastCall![0];
    expect(candleData.at(-1)).toMatchObject(light.forming);
    const volumeData = volume.setData.mock.lastCall![0];
    expect(volumeData.at(-1).color).toBe(light.volume.forming);
  });

  it("tags levels by name, the stop as Stop, and the last price as a fixed tag", () => {
    const { controller, lastTags } = setup();
    controller.setCandles(candles.candles, null);
    controller.setLevels([{ price: 110, label: "PDH·R1", kinds: ["pdh", "r1"] }]);
    controller.setForecast(forecast);
    expect(lastTags().map((t) => [t.label, t.price, t.line, t.fixed ?? false])).toEqual([
      ["PDH·R1", 110, true, false],
      ["Stop", forecast.trade!.stop, true, true],
      ["103.00", 103, false, true],
    ]);
  });

  it("draws no stop while abstaining, and greys the ghosts", () => {
    const { controller, ghost, lastTags } = setup();
    controller.setForecast({ ...forecast, abstain: true });
    expect(ghost.applyOptions).toHaveBeenLastCalledWith(chartTheme("dark").ghostAbstain);
    expect(lastTags().some((t) => t.label === "Stop")).toBe(false);
  });

  it("dims stale levels and keeps that look across a theme switch", () => {
    const { controller, lastTags } = setup();
    controller.setLevels([{ price: 110, label: "PDH", kinds: ["pdh"] }], true);
    expect(lastTags()[0].look).toEqual(chartTheme("dark").levelStale);
    controller.setTheme("light");
    expect(lastTags()[0].look).toEqual(chartTheme("light").levelStale);
    controller.setLevels([{ price: 110, label: "PDH", kinds: ["pdh"] }], false);
    expect(lastTags()[0].look).toEqual(chartTheme("light").level);
  });

  it("hands pattern arrows and the band cone to their primitives, and clears them", () => {
    const { controller, markers, bandFill, ghost, p10 } = setup();
    controller.setCandles(candles.candles, null);
    controller.setMarkers([{ time: T0, direction: "bullish", filled: true, stack: 0 }]);
    expect(markers.set).toHaveBeenLastCalledWith([{ time: T0, direction: "bullish", filled: true, stack: 0 }], expect.any(Map));

    controller.setForecast(forecast);
    expect(bandFill.set.mock.lastCall![0]).toHaveLength(4);
    controller.setForecast(null);
    expect(bandFill.set.mock.lastCall![0]).toEqual([]);
    expect(ghost.setData).toHaveBeenLastCalledWith([]);
    expect(p10.setData).toHaveBeenLastCalledWith([]);
  });
});
