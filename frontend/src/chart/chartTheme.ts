import {
  ColorType,
  LineStyle,
  type CandlestickSeriesPartialOptions,
  type ChartOptions,
  type DeepPartial,
  type LineSeriesPartialOptions,
} from "lightweight-charts";
import type { Direction } from "../api/types";
import { withAlpha } from "../lib/color";
import { TOKENS, type Tokens } from "../lib/palette";
import type { Theme } from "../lib/theme";

/** A horizontal line across the pane with a short tag ("PDH", "Stop") on the price axis. */
export type TagLook = { color: string; dash: number[]; width: number; tagBackground: string; tagText: string };

/** The "Expected" box: outline and median, translucent fill, the label and its halo. */
export type ExpectedLook = { stroke: string; fill: string; text: string; halo: string };

export type ChartTheme = {
  tokens: Tokens;
  /** layout background, text, grid, borders, crosshair */
  chart: DeepPartial<ChartOptions>;
  candles: CandlestickSeriesPartialOptions;
  /** per-bar colours for the still-open candle: a hollow amber outline */
  forming: { color: string; borderColor: string; wickColor: string };
  volume: { up: string; down: string; forming: string };
  /** ghost candles of a directional call: tinted bodies */
  ghost: CandlestickSeriesPartialOptions;
  /** ghost candles of an abstaining forecast: grey outlines, hollow, so they never read as a call */
  ghostAbstain: CandlestickSeriesPartialOptions;
  /** the next bar's range box, in the band colour: the range holds whether or not there is a call */
  expected: ExpectedLook;
  band: { p10: LineSeriesPartialOptions; p50: LineSeriesPartialOptions; p90: LineSeriesPartialOptions };
  /** the translucent fill between p10 and p90 */
  bandFill: string;
  bandFillAbstain: string;
  /** the call's stop (invalidation): dashed */
  stop: TagLook;
  /** key levels: thin, dotted and neutral; colour is kept for direction */
  level: TagLook;
  /** levels built from an older session than the newest data: dimmed */
  levelStale: TagLook;
  /** the last price's axis tag, coloured by the last candle */
  lastPrice: { up: TagLook; down: TagLook };
  /** pattern arrows; forming ones are drawn hollow in the same colour */
  markers: Record<Direction, string>;
  histogram: { up: string; down: string };
  indicators: readonly string[];
};

const QUIET_SERIES = { priceLineVisible: false, lastValueVisible: false } as const;

export const GHOST_BODY_ALPHA = 0.28;
const GHOST_HOLLOW_ALPHA = 0.06;

/** Translucent body, opaque outline: reads as a projection but stays at 3:1 against the chart. */
function ghostOptions(up: string, down: string, bodyAlpha = GHOST_BODY_ALPHA): CandlestickSeriesPartialOptions {
  return {
    ...QUIET_SERIES,
    upColor: withAlpha(up, bodyAlpha),
    downColor: withAlpha(down, bodyAlpha),
    borderVisible: true,
    borderUpColor: up,
    borderDownColor: down,
    wickUpColor: up,
    wickDownColor: down,
  };
}

/** Pure: theme → every colour option the price chart uses. Applied live on toggle. */
export function chartTheme(theme: Theme): ChartTheme {
  const t = TOKENS[theme];
  const volumeAlpha = theme === "dark" ? 0.24 : 0.22;
  const bandLine = (color: string, lineStyle: LineStyle): LineSeriesPartialOptions => ({
    ...QUIET_SERIES,
    color,
    lineStyle,
    lineWidth: 1,
    crosshairMarkerVisible: false,
  });

  return {
    tokens: t,
    chart: {
      layout: {
        background: { type: ColorType.Solid, color: t.surface },
        textColor: t.inkMuted,
        panes: { separatorColor: t.line, separatorHoverColor: withAlpha(t.inkFaint, 0.25) },
      },
      grid: { vertLines: { color: t.grid, visible: false }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.line },
      timeScale: { borderColor: t.line },
      crosshair: {
        vertLine: { color: withAlpha(t.inkFaint, 0.6), labelBackgroundColor: t.raised },
        horzLine: { color: withAlpha(t.inkFaint, 0.6), labelBackgroundColor: t.raised },
      },
    },
    candles: {
      upColor: t.up,
      downColor: t.down,
      borderVisible: true,
      borderUpColor: t.up,
      borderDownColor: t.down,
      wickUpColor: t.up,
      wickDownColor: t.down,
    },
    forming: { color: withAlpha(t.forming, 0.12), borderColor: t.forming, wickColor: t.forming },
    volume: {
      up: withAlpha(t.up, volumeAlpha),
      down: withAlpha(t.down, volumeAlpha),
      forming: withAlpha(t.forming, 0.45),
    },
    ghost: ghostOptions(t.up, t.down),
    ghostAbstain: ghostOptions(t.abstain, t.abstain, GHOST_HOLLOW_ALPHA),
    expected: { stroke: t.band, fill: withAlpha(t.band, 0.16), text: t.band, halo: t.surface },
    band: {
      p10: bandLine(withAlpha(t.band, 0.55), LineStyle.Solid),
      p50: bandLine(t.band, LineStyle.Dashed),
      p90: bandLine(withAlpha(t.band, 0.55), LineStyle.Solid),
    },
    bandFill: withAlpha(t.band, 0.1),
    bandFillAbstain: withAlpha(t.abstain, 0.08),
    stop: { color: t.danger, dash: [6, 4], width: 1, tagBackground: t.danger, tagText: t.surface },
    level: { color: t.lineStrong, dash: [1, 3], width: 1, tagBackground: t.raised, tagText: t.inkMuted },
    levelStale: { color: withAlpha(t.lineStrong, 0.45), dash: [1, 5], width: 1, tagBackground: t.raised, tagText: t.inkFaint },
    lastPrice: {
      up: { color: t.up, dash: [], width: 1, tagBackground: t.up, tagText: t.surface },
      down: { color: t.down, dash: [], width: 1, tagBackground: t.down, tagText: t.surface },
    },
    markers: { bullish: t.up, bearish: t.down, neutral: t.neutral },
    histogram: { up: withAlpha(t.up, 0.55), down: withAlpha(t.down, 0.55) },
    indicators: t.indicators,
  };
}
