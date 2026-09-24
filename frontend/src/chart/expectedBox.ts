/** Pixel layout of the "Expected" box: the next bar's p10–p90 close range, drawn around its slot. */
export type BoxGeometry = {
  left: number;
  top: number;
  width: number;
  height: number;
  /** the median (p50), a little wider than the box */
  tick: { x1: number; x2: number; y: number };
  /** "Expected" above the box and the bar in it, or below the box when there is no room above */
  label: { x: number; y: number; baseline: "bottom" | "top" };
};

const MIN_WIDTH = 8;
const MAX_WIDTH = 48;
/** just wider than a candle body (about 0.8 of the bar spacing): the live candle sits inside, the neighbours stay clear */
const BODY_SHARE = 0.8;
const MARGIN = 3;
const MIN_HEIGHT = 4;
const TICK_OVERHANG = 3;
const LABEL_HEIGHT = 12;
const LABEL_GAP = 3;

/**
 * `x` is the bar's centre, `spacing` the bar spacing, and `y` the p90/p50/p10 prices as pane
 * coordinates (null when off the scale). `barTop` is the y of the bar's high when a candle is in the
 * slot, so the label clears its wick. Returns null when the box can't be placed.
 */
export function expectedBoxGeometry(
  x: number | null,
  spacing: number,
  y: { high: number | null; mid: number | null; low: number | null },
  barTop: number | null = null,
): BoxGeometry | null {
  if (x === null || y.high === null || y.low === null) return null;
  const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, spacing * BODY_SHARE + MARGIN));
  let top = Math.min(y.high, y.low);
  let height = Math.abs(y.low - y.high);
  if (height < MIN_HEIGHT) {
    top -= (MIN_HEIGHT - height) / 2;
    height = MIN_HEIGHT;
  }
  const left = x - width / 2;
  const tickY = y.mid ?? top + height / 2;
  const above = Math.min(top, barTop ?? top) - LABEL_GAP;
  const label =
    above - LABEL_HEIGHT >= 0 ? { x, y: above, baseline: "bottom" as const } : { x, y: top + height + LABEL_GAP, baseline: "top" as const };
  return {
    left,
    top,
    width,
    height,
    tick: { x1: left - TICK_OVERHANG, x2: left + width + TICK_OVERHANG, y: tickY },
    label,
  };
}
