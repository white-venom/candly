import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";
import type { Candle, Forecast } from "../api/types";
import { isIntraday } from "../lib/timeframes";
import type { Theme } from "../lib/theme";
import { chartTheme, type ChartTheme } from "./chartTheme";
import type { DrawnLevel } from "./levels";
import { BandFill, PatternMarkers, PriceTags, type PriceTag } from "./primitives";
import { chartTimeFormatter, istTickMarkFormatter } from "./timeFormat";
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
  volumeData,
  type BandKey,
  type Hover,
  type IndicatorPlot,
  type MarkerGlyph,
} from "./transforms";

type IndicatorHandle = { plot: IndicatorPlot; series: ISeriesApi<"Line"> | ISeriesApi<"Histogram"> };

const QUIET = { priceLineVisible: false, lastValueVisible: false } as const;
const BAR_PX = 5.5;
const PRICE_AXIS_PX = 70;
/** empty bars kept right of the last candle: room for three ghost candles and their cone */
const FUTURE_BARS = 10;
const MIN_BARS = 60;
const MAX_BARS = 300;
const FONT = 'Inter, "Segoe UI", system-ui, -apple-system, Roboto, sans-serif';

/**
 * Owns one lightweight-charts instance. The chart and its series are created once;
 * theme, timeframe and data changes are applied onto them (never recreated).
 */
export class PriceChartController {
  private readonly container: HTMLElement;
  private readonly chart: IChartApi;
  private readonly candleSeries: ISeriesApi<"Candlestick">;
  private readonly volumeSeries: ISeriesApi<"Histogram">;
  private readonly ghostSeries: ISeriesApi<"Candlestick">;
  private readonly bandSeries: Record<BandKey, ISeriesApi<"Line">>;
  private readonly markers = new PatternMarkers();
  private readonly bandFill = new BandFill();
  private readonly tags = new PriceTags();
  private theme: ChartTheme;
  private candles: Candle[] = [];
  private forming: Candle | null = null;
  private glyphs: MarkerGlyph[] = [];
  private forecast: Forecast | null = null;
  private levels: DrawnLevel[] = [];
  private levelsStale = false;
  private indicatorHandles: IndicatorHandle[] = [];

  constructor(container: HTMLElement, theme: Theme, tf: string) {
    this.container = container;
    this.theme = chartTheme(theme);
    const t = this.theme.chart;
    this.chart = createChart(container, {
      ...t,
      autoSize: true,
      layout: { ...t.layout, fontFamily: FONT, fontSize: 11 },
      localization: { locale: "en-IN", timeFormatter: chartTimeFormatter(tf) },
      timeScale: {
        ...t.timeScale,
        tickMarkFormatter: istTickMarkFormatter,
        timeVisible: isIntraday(tf),
        secondsVisible: false,
        rightOffset: FUTURE_BARS,
      },
      crosshair: { ...t.crosshair, mode: CrosshairMode.Normal },
    });

    this.volumeSeries = this.chart.addSeries(HistogramSeries, { ...QUIET, priceScaleId: "volume", priceFormat: { type: "volume" } });
    this.chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    // The last price is drawn as one of our axis tags, so it takes part in their spacing.
    this.candleSeries = this.chart.addSeries(CandlestickSeries, { ...this.theme.candles, lastValueVisible: false });
    this.candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.14, bottom: 0.18 } });
    this.bandSeries = {
      p10: this.chart.addSeries(LineSeries, this.theme.band.p10),
      p50: this.chart.addSeries(LineSeries, this.theme.band.p50),
      p90: this.chart.addSeries(LineSeries, this.theme.band.p90),
    };
    this.ghostSeries = this.chart.addSeries(CandlestickSeries, this.theme.ghost);
    this.candleSeries.attachPrimitive(this.bandFill);
    this.candleSeries.attachPrimitive(this.tags);
    this.candleSeries.attachPrimitive(this.markers);
    this.markers.setColors(this.theme.markers, this.theme.tokens.surface);
  }

  setTheme(theme: Theme): void {
    const th = chartTheme(theme);
    this.theme = th;
    this.chart.applyOptions(th.chart);
    this.candleSeries.applyOptions(th.candles);
    this.ghostSeries.applyOptions(this.forecast?.abstain ? th.ghostAbstain : th.ghost);
    for (const key of BAND_KEYS) this.bandSeries[key].applyOptions(th.band[key]);
    for (const handle of this.indicatorHandles) this.styleIndicator(handle);
    this.markers.setColors(th.markers, th.tokens.surface);
    // Per-bar colours (forming candle, volume), the band fill and the tags live in the data.
    this.renderCandles();
    this.renderBandFill();
    this.renderTags();
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
    this.renderTags();
  }

  setMarkers(glyphs: MarkerGlyph[]): void {
    this.glyphs = glyphs;
    this.renderMarkers();
  }

  /** Thin dotted lines with short axis tags; stale ones (from an older session) dimmed. */
  setLevels(levels: DrawnLevel[], stale = false): void {
    this.levels = levels;
    this.levelsStale = stale;
    this.renderTags();
  }

  /** Ghost candles, the p10–p90 cone and, for a directional call only, the stop. */
  setForecast(forecast: Forecast | null): void {
    this.forecast = forecast;
    this.ghostSeries.applyOptions(forecast?.abstain ? this.theme.ghostAbstain : this.theme.ghost);
    this.ghostSeries.setData(forecast ? ghostData(forecast) : []);
    const bands = forecast ? bandData(forecast) : null;
    for (const key of BAND_KEYS) this.bandSeries[key].setData(bands ? bands[key] : []);
    this.renderBandFill();
    this.renderTags();
  }

  setIndicators(plots: IndicatorPlot[]): void {
    for (const { series } of this.indicatorHandles) this.chart.removeSeries(series);
    this.indicatorHandles = plots.map((plot) => {
      let series: IndicatorHandle["series"];
      if (plot.kind === "histogram") {
        series = this.chart.addSeries(HistogramSeries, { ...QUIET }, plot.pane);
      } else {
        const line = this.chart.addSeries(LineSeries, { ...QUIET, lineWidth: 1, crosshairMarkerVisible: false }, plot.pane);
        line.setData(linePoints(plot.series.points));
        series = line;
      }
      const handle = { plot, series };
      this.styleIndicator(handle);
      return handle;
    });
    this.chart.panes().forEach((pane, i) => pane.setStretchFactor(i === 0 ? 3 : 1));
  }

  /** Shows the latest bars, about one per BAR_PX pixels, with room on the right for the forecast. */
  resetView(): void {
    const n = barsWithForming(this.candles, this.forming).bars.length;
    if (n === 0) return;
    const width = this.container.clientWidth - PRICE_AXIS_PX;
    const visible = width > 0 ? Math.min(MAX_BARS, Math.max(MIN_BARS, Math.round(width / BAR_PX))) : MIN_BARS * 2;
    this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - visible), to: n + FUTURE_BARS });
  }

  onHover(callback: (hover: Hover | null) => void): () => void {
    const bars = () => barsWithForming(this.candles, this.forming).bars;
    const handler = (param: MouseEventParams<Time>) => {
      const candle = param.seriesData.get(this.candleSeries) as CandlestickData<Time> | undefined;
      if (param.time === undefined || !param.point || !candle || !("open" in candle)) return callback(null);
      const volume = param.seriesData.get(this.volumeSeries) as HistogramData<Time> | undefined;
      const time = Number(candle.time);
      const all = bars();
      const index = all.findIndex((c) => c.time === time);
      const values: Record<string, number> = {};
      for (const { plot, series } of this.indicatorHandles) {
        const point = param.seriesData.get(series) as LineData<Time> | undefined;
        if (point && "value" in point) values[plot.series.name] = point.value;
      }
      callback({
        bar: {
          time,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: volume && "value" in volume ? volume.value : null,
          forming: this.forming?.time === time,
          prevClose: index > 0 ? all[index - 1].close : null,
        },
        x: param.point.x,
        values,
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
    const extents = new Map(barsWithForming(this.candles, this.forming).bars.map((c) => [c.time, { high: c.high, low: c.low }]));
    this.markers.set(this.glyphs, extents);
  }

  /** Levels nearest the price come first: when the axis is crowded, the far ones give up their tags. */
  private renderTags(): void {
    const look = this.levelsStale ? this.theme.levelStale : this.theme.level;
    const last = barsWithForming(this.candles, this.forming).bars.at(-1);
    const byDistance = [...this.levels].sort((a, b) => (last ? Math.abs(a.price - last.close) - Math.abs(b.price - last.close) : 0));
    const tags: PriceTag[] = byDistance.map((l) => ({ price: l.price, label: l.label, look, line: true }));
    const f = this.forecast;
    const stop = f && !f.abstain ? (f.trade?.stop ?? f.invalidation) : null;
    // The stop keeps its exact place on the axis; levels make room around it.
    if (stop !== null) tags.push({ price: stop, label: "Stop", look: this.theme.stop, line: true, fixed: true });
    if (last) {
      const lastLook = last.close >= last.open ? this.theme.lastPrice.up : this.theme.lastPrice.down;
      tags.push({ price: last.close, label: this.candleSeries.priceFormatter().format(last.close), look: lastLook, line: false, fixed: true });
    }
    this.tags.set(tags);
  }

  private renderBandFill(): void {
    const f = this.forecast;
    const band = f ? bandData(f) : null;
    const points = band ? band.p10.map((p, i) => ({ time: Number(p.time), low: p.value, high: band.p90[i].value })) : [];
    this.bandFill.set(points, f?.abstain ? this.theme.bandFillAbstain : this.theme.bandFill);
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
