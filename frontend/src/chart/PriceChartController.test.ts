import { describe, expect, it, vi } from "vitest";
import { T0, DAY, candle, candles, forecast } from "../test/fixtures";
import { chartTheme } from "./chartTheme";
import { PriceChartController } from "./PriceChartController";

type Fake = Record<string, ReturnType<typeof vi.fn>>;

const lwc = vi.hoisted(() => {
  const fakePriceLine = () => ({ applyOptions: vi.fn() });
  const fakeSeries = (type: string) => ({
    type,
    applyOptions: vi.fn(),
    setData: vi.fn(),
    createPriceLine: vi.fn(fakePriceLine),
    removePriceLine: vi.fn(),
    priceScale: () => ({ applyOptions: vi.fn() }),
  });
  const state = { chart: null as unknown as Fake & { series: ReturnType<typeof fakeSeries>[] }, markers: { setMarkers: vi.fn() } };
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
  createSeriesMarkers: vi.fn(() => lwc.state.markers),
}));

function setup() {
  lwc.createChart.mockClear();
  const controller = new PriceChartController(document.createElement("div"), "dark", "1D");
  const chart = lwc.state.chart;
  // creation order: volume, candles, p10, p50, p90, ghost
  const [volume, candleSeries, p10, p50, p90, ghost] = chart.series;
  return { controller, chart, volume, candleSeries, p10, p50, p90, ghost };
}

describe("PriceChartController theme switching", () => {
  it("re-colours everything in place on toggle, without recreating the chart or series", () => {
    const { controller, chart, volume, candleSeries, p50, ghost } = setup();
    controller.setCandles(candles.candles, candle(T0 + DAY, 103, 105));
    controller.setLevels([{ price: 110, label: "PDH", kind: "pdh" }]);
    controller.setForecast(forecast);
    const levelLine = candleSeries.createPriceLine.mock.results[0].value;
    const invalidationLine = candleSeries.createPriceLine.mock.results[1].value;
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
    expect(levelLine.applyOptions).toHaveBeenLastCalledWith(light.levels.pdh);
    expect(invalidationLine.applyOptions).toHaveBeenLastCalledWith(light.invalidation);

    const candleData = candleSeries.setData.mock.lastCall![0];
    expect(candleData.at(-1)).toMatchObject(light.forming);
    const volumeData = volume.setData.mock.lastCall![0];
    expect(volumeData.at(-1).color).toBe(light.volume.forming);
    expect(lwc.state.markers.setMarkers).toHaveBeenCalled();
  });

  it("switches ghosts and invalidation to the abstain look when the forecast abstains", () => {
    const { controller, candleSeries, ghost } = setup();
    controller.setForecast({ ...forecast, abstain: true });
    const dark = chartTheme("dark");
    expect(ghost.applyOptions).toHaveBeenLastCalledWith(dark.ghostAbstain);
    expect(candleSeries.createPriceLine).toHaveBeenLastCalledWith(
      expect.objectContaining({ price: forecast.invalidation, title: "Invalidation (abstaining)", ...dark.invalidationAbstain }),
    );
  });

  it("draws stale levels dashed and dimmed, and keeps that look across a theme switch", () => {
    const { controller, candleSeries } = setup();
    const dark = chartTheme("dark");
    controller.setLevels([{ price: 110, label: "Prev day high", kind: "pdh" }], true);
    expect(candleSeries.createPriceLine).toHaveBeenLastCalledWith(expect.objectContaining({ price: 110, ...dark.levelsStale.pdh }));
    expect(dark.levelsStale.pdh.lineStyle).not.toBe(dark.levels.pdh.lineStyle);
    expect(dark.levelsStale.pdh.color).not.toBe(dark.levels.pdh.color);

    const line = candleSeries.createPriceLine.mock.results.at(-1)!.value;
    controller.setTheme("light");
    expect(line.applyOptions).toHaveBeenLastCalledWith(chartTheme("light").levelsStale.pdh);

    controller.setLevels([{ price: 110, label: "Prev day high", kind: "pdh" }], false);
    expect(candleSeries.createPriceLine).toHaveBeenLastCalledWith(expect.objectContaining(chartTheme("light").levels.pdh));
  });

  it("clears the forecast drawing when there is no forecast", () => {
    const { controller, candleSeries, ghost, p10 } = setup();
    controller.setForecast(forecast);
    controller.setForecast(null);
    expect(ghost.setData).toHaveBeenLastCalledWith([]);
    expect(p10.setData).toHaveBeenLastCalledWith([]);
    expect(candleSeries.removePriceLine).toHaveBeenCalledTimes(1);
  });
});
