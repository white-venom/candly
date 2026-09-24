import { sameDayIST } from "./time";
import { isIntraday, tfSeconds } from "./timeframes";

/**
 * How far the chart's closed bars (what forecasts, patterns and indicators are built from) run behind
 * `now`, in seconds, counted from the last closed bar's close. A live forming bar doesn't make them
 * fresh, except on a new day: before the session's first bar closes there is none to wait for.
 * `behind` flags intraday data more than two bars old while its market is open. Daily charts are never
 * flagged: weekends and holidays would need the market calendar.
 */
export function freshness(
  lastClosed: number | null,
  forming: number | null,
  tf: string,
  marketOpen: boolean,
  now: number,
): { lag: number; behind: boolean } | null {
  if (lastClosed === null) return null;
  const firstOfDay = forming !== null && !sameDayIST(forming, lastClosed);
  const lag = Math.max(0, now - (firstOfDay ? forming : lastClosed + tfSeconds(tf)));
  return { lag, behind: marketOpen && isIntraday(tf) && lag > 2 * tfSeconds(tf) };
}

/** "25 min", "3 h", "2 days". */
export function fmtLag(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 2 * 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} days`;
}
