import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";
import type { Candle, Forecast, Level, PatternSignal } from "../api/types";
import type { Theme } from "../lib/theme";
import { chartTimeFormatter, istTickMarkFormatter } from "./timeFormat";
import { isIntraday } from "../lib/timeframes";
import { chartTheme, type ChartTheme } from "./chartTheme";
import {
  BAND_KEYS,
  bandData,
  barsWithForming,
  candleData,
  ghostData,
  histogramPoints,
  indicatorColor,
  lineStyleFor,
  linePoints,
  signalMarkers,
  volumeData,
  type BandKey,
  type HoverBar,
  type IndicatorPlot,
} from "./transforms";

type IndicatorHandle = { plot: IndicatorPlot; series: ISeriesApi<"Line"> | ISeriesApi<"Histogram"> };

const QUIET = { priceLineVisible: false, lastValueVisible: false } as const;
const VISIBLE_BARS = 150;
const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

/**
 * Owns one lightweight-charts instance. The chart and its series are created once;
 * theme, timeframe and data changes are applied onto them (never recreated).
 */
export class PriceChartController {
  private readonly chart: IChartApi;
  private readonly candleSeries: ISeriesApi<"Candlestick">;
  private readonly volumeSeries: ISeriesApi<"Histogram">;
  private readonly ghostSeries: ISeriesApi<"Candlestick">;
  private readonly bandSeries: Record<BandKey, ISeriesApi<"Line">>;
  private readonly markers: ISeriesMarkersPluginApi<Time>;
  private theme: ChartTheme;
  private candles: Candle[] = [];
  private forming: Candle | null = null;
  private signals: PatternSignal[] = [];
  private forecast: Forecast | null = null;
  private levelLines: { level: Level; line: IPriceLine }[] = [];
  private levelsStale = false;
  private invalidationLine: IPriceLine | null = null;
  private indicatorHandles: IndicatorHandle[] = [];

  constructor(container: HTMLElement, theme: Theme, tf: string) {
    this.theme = chartTheme(theme);
    const t = this.theme.chart;
    this.chart = createChart(container, {
      ...t,
      autoSize: true,
      layout: { ...t.layout, fontFamily: FONT, fontSize: 12 },
      localization: { locale: "en-IN", timeFormatter: chartTimeFormatter(tf) },
      timeScale: {
        ...t.timeScale,
        tickMarkFormatter: istTickMarkFormatter,
        timeVisible: isIntraday(tf),
        secondsVisible: false,
        rightOffset: 4,
      },
      crosshair: { ...t.crosshair, mode: CrosshairMode.Normal },
    });

    this.volumeSeries = this.chart.addSeries(HistogramSeries, { ...QUIET, priceScaleId: "volume", priceFormat: { type: "volume" } });
    this.chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    this.candleSeries = this.chart.addSeries(CandlestickSeries, { ...this.theme.candles });
    this.candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.24 } });
    this.bandSeries = {
      p10: this.chart.addSeries(LineSeries, this.theme.band.p10),
      p50: this.chart.addSeries(LineSeries, this.theme.band.p50),
      p90: this.chart.addSeries(LineSeries, this.theme.band.p90),
    };
    this.ghostSeries = this.chart.addSeries(CandlestickSeries, this.theme.ghost);
    this.markers = createSeriesMarkers(this.candleSeries, []);
  }

  setTheme(theme: Theme): void {
    const th = chartTheme(theme);
    this.theme = th;
    this.chart.applyOptions(th.chart);
    this.candleSeries.applyOptions(th.candles);
    this.ghostSeries.applyOptions(this.forecast?.abstain ? th.ghostAbstain : th.ghost);
    for (const key of BAND_KEYS) this.bandSeries[key].applyOptions(th.band[key]);
    for (const { level, line } of this.levelLines) line.applyOptions(this.levelLook(level));
    this.invalidationLine?.applyOptions(this.invalidationLook());
    for (const handle of this.indicatorHandles) this.styleIndicator(handle);
    // Per-bar colours (forming candle, volume) and marker colours live in the data.
    this.renderCandles();
    this.renderMarkers();
  }

  setTimeframe(tf: string): void {
    this.chart.applyOptions({
      localization: { timeFormatter: chartTimeFormatter(tf) },
      timeScale: { timeVisible: isIntraday(tf) },
    });
  }

  setCandles(candles: Candle[], forming: Candle | null): void {
    this.candles = candles;
    this.forming = forming;
    this.renderCandles();
    this.renderMarkers();
  }

  setSignals(signals: PatternSignal[]): void {
    this.signals = signals;
    this.renderMarkers();
  }

  /** Stale levels (built from an older session than the newest data) are drawn dashed and dimmed. */
  setLevels(levels: Level[], stale = false): void {
    for (const { line } of this.levelLines) this.candleSeries.removePriceLine(line);
    this.levelsStale = stale;
    this.levelLines = levels.map((level) => ({
      level,
      line: this.candleSeries.createPriceLine({
        price: level.price,
        title: level.label,
        axisLabelVisible: true,
        ...this.levelLook(level),
      }),
    }));
  }

  setForecast(forecast: Forecast | null): void {
    this.forecast = forecast;
    this.ghostSeries.applyOptions(forecast?.abstain ? this.theme.ghostAbstain : this.theme.ghost);
    this.ghostSeries.setData(forecast ? ghostData(forecast) : []);
    const bands = forecast ? bandData(forecast) : null;
    for (const key of BAND_KEYS) this.bandSeries[key].setData(bands ? bands[key] : []);

    if (this.invalidationLine) this.candleSeries.removePriceLine(this.invalidationLine);
    this.invalidationLine =
      forecast && forecast.invalidation !== null
        ? this.candleSeries.createPriceLine({
            price: forecast.invalidation,
            title: forecast.abstain ? "Invalidation (abstaining)" : "Invalidation",
            axisLabelVisible: true,
            ...this.invalidationLook(),
          })
        : null;
  }

  setIndicators(plots: IndicatorPlot[]): void {
    for (const { series } of this.indicatorHandles) this.chart.removeSeries(series);
    this.indicatorHandles = plots.map((plot) => {
      let series: IndicatorHandle["series"];
      if (plot.kind === "histogram") {
        series = this.chart.addSeries(HistogramSeries, { ...QUIET }, plot.pane);
      } else {
        const line = this.chart.addSeries(LineSeries, { ...QUIET, lineWidth: 2, crosshairMarkerVisible: false }, plot.pane);
        line.setData(linePoints(plot.series.points));
        series = line;
      }
      const handle = { plot, series };
      this.styleIndicator(handle);
      return handle;
    });
    this.chart.panes().forEach((pane, i) => pane.setStretchFactor(i === 0 ? 3 : 1));
  }

  /** Shows the latest bars with room on the right for the forecast. */
  resetView(): void {
    const n = barsWithForming(this.candles, this.forming).bars.length;
    if (n === 0) return;
    this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - VISIBLE_BARS), to: n + 6 });
  }

  onHover(callback: (bar: HoverBar | null) => void): () => void {
    const handler = (param: MouseEventParams<Time>) => {
      const candle = param.seriesData.get(this.candleSeries) as CandlestickData<Time> | undefined;
      if (param.time === undefined || !candle || !("open" in candle)) return callback(null);
      const volume = param.seriesData.get(this.volumeSeries) as HistogramData<Time> | undefined;
      const time = Number(candle.time);
      callback({
        time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: volume && "value" in volume ? volume.value : null,
        forming: this.forming?.time === time,
      });
    };
    this.chart.subscribeCrosshairMove(handler);
    return () => this.chart.unsubscribeCrosshairMove(handler);
  }

  destroy(): void {
    this.chart.remove();
  }

  private renderCandles(): void {
    this.candleSeries.setData(candleData(this.candles, this.forming, this.theme));
    this.volumeSeries.setData(volumeData(this.candles, this.forming, this.theme));
  }

  private renderMarkers(): void {
    const times = new Set(barsWithForming(this.candles, this.forming).bars.map((c) => c.time));
    this.markers.setMarkers(signalMarkers(this.signals, times, this.theme));
  }

  private levelLook(level: Level) {
    return (this.levelsStale ? this.theme.levelsStale : this.theme.levels)[level.kind];
  }

  private invalidationLook() {
    return this.forecast?.abstain ? this.theme.invalidationAbstain : this.theme.invalidation;
  }

  /** Histogram colours are per bar, so a histogram re-sets its data; a line only changes options. */
  private styleIndicator({ plot, series }: IndicatorHandle): void {
    if (plot.kind === "histogram") {
      (series as ISeriesApi<"Histogram">).setData(histogramPoints(plot.series.points, this.theme));
    } else {
      (series as ISeriesApi<"Line">).applyOptions({ color: indicatorColor(plot.slot, this.theme), lineStyle: lineStyleFor(plot.order) });
    }
  }
}
