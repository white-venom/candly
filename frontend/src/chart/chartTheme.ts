import {
  ColorType,
  LineStyle,
  type CandlestickSeriesPartialOptions,
  type ChartOptions,
  type DeepPartial,
  type LineSeriesPartialOptions,
  type PriceLineOptions,
} from "lightweight-charts";
import type { Direction, Level } from "../api/types";
import { withAlpha } from "../lib/color";
import { TOKENS, type Tokens } from "../lib/palette";
import type { Theme } from "../lib/theme";

type LineLook = Pick<PriceLineOptions, "color" | "lineStyle" | "lineWidth">;

export type ChartTheme = {
  tokens: Tokens;
  /** layout background, text, grid, borders, crosshair */
  chart: DeepPartial<ChartOptions>;
  candles: CandlestickSeriesPartialOptions;
  /** per-bar colours for the still-open candle: a hollow amber outline */
  forming: { color: string; borderColor: string; wickColor: string };
  volume: { up: string; down: string; forming: string };
  ghost: CandlestickSeriesPartialOptions;
  /** ghost candles of an abstaining forecast: grey, so they never read as a call */
  ghostAbstain: CandlestickSeriesPartialOptions;
  band: { p10: LineSeriesPartialOptions; p50: LineSeriesPartialOptions; p90: LineSeriesPartialOptions };
  invalidation: LineLook;
  invalidationAbstain: LineLook;
  levels: Record<Level["kind"], LineLook>;
  markers: Record<"confirmed" | "forming", Record<Direction, string>>;
  histogram: { up: string; down: string };
  indicators: readonly string[];
};

const SUPPORT: Level["kind"][] = ["pdl", "swing_low", "s1", "s2", "cpr_bottom"];
const RESISTANCE: Level["kind"][] = ["pdh", "swing_high", "r1", "r2", "cpr_top"];
const PIVOT: Level["kind"][] = ["pdc", "vwap", "pivot"];

const QUIET_SERIES = { priceLineVisible: false, lastValueVisible: false } as const;

export const GHOST_BODY_ALPHA = 0.28;

/** Translucent body, opaque outline: reads as a projection but stays at 3:1 against the chart. */
function ghostOptions(up: string, down: string): CandlestickSeriesPartialOptions {
  return {
    ...QUIET_SERIES,
    upColor: withAlpha(up, GHOST_BODY_ALPHA),
    downColor: withAlpha(down, GHOST_BODY_ALPHA),
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
  const volumeAlpha = theme === "dark" ? 0.45 : 0.4;
  const bandLine = (lineStyle: LineStyle, lineWidth: 1 | 2): LineSeriesPartialOptions => ({
    ...QUIET_SERIES,
    color: t.band,
    lineStyle,
    lineWidth,
    crosshairMarkerVisible: false,
  });
  const level = (color: string): LineLook => ({ color, lineStyle: LineStyle.Dotted, lineWidth: 1 });

  return {
    tokens: t,
    chart: {
      layout: {
        background: { type: ColorType.Solid, color: t.surface },
        textColor: t.inkMuted,
        panes: { separatorColor: t.line, separatorHoverColor: withAlpha(t.inkFaint, 0.25) },
      },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.line },
      timeScale: { borderColor: t.line },
      crosshair: {
        vertLine: { color: t.inkFaint, labelBackgroundColor: t.raised },
        horzLine: { color: t.inkFaint, labelBackgroundColor: t.raised },
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
      forming: withAlpha(t.forming, 0.55),
    },
    ghost: ghostOptions(t.up, t.down),
    ghostAbstain: ghostOptions(t.abstain, t.abstain),
    band: {
      p10: bandLine(LineStyle.Solid, 1),
      p50: bandLine(LineStyle.Dashed, 2),
      p90: bandLine(LineStyle.Solid, 1),
    },
    invalidation: { color: t.danger, lineStyle: LineStyle.Dashed, lineWidth: 2 },
    invalidationAbstain: { color: t.abstain, lineStyle: LineStyle.Dashed, lineWidth: 1 },
    levels: Object.fromEntries([
      ...SUPPORT.map((k) => [k, level(t.up)]),
      ...RESISTANCE.map((k) => [k, level(t.down)]),
      ...PIVOT.map((k) => [k, level(t.neutral)]),
    ]) as Record<Level["kind"], LineLook>,
    markers: {
      confirmed: { bullish: t.up, bearish: t.down, neutral: t.neutral },
      forming: {
        bullish: withAlpha(t.up, 0.5),
        bearish: withAlpha(t.down, 0.5),
        neutral: withAlpha(t.neutral, 0.5),
      },
    },
    histogram: { up: withAlpha(t.up, 0.6), down: withAlpha(t.down, 0.6) },
    indicators: t.indicators,
  };
}
