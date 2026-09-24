import type { Forecast } from "../api/types";
import { formatSessionIST, formatTimeIST, sameDayIST } from "./time";
import { tfSeconds } from "./timeframes";

/** One forecast step: the 80% range of its close (p10–p90) and the median. */
export type ExpectedRange = { time: number; low: number; mid: number; high: number };

export function expectedRanges(f: Pick<Forecast, "bands">): ExpectedRange[] {
  return [...f.bands].sort((a, b) => a.time - b.time).map((b) => ({ time: b.time, low: b.p10, mid: b.p50, high: b.p90 }));
}

/** Future bar times of the forecast, in order: from the bands, else from the ghost candles. */
export function forecastTimes(f: Pick<Forecast, "bands" | "ghost_candles">): number[] {
  const source = f.bands.length > 0 ? f.bands : f.ghost_candles;
  return source.map((b) => b.time).sort((a, b) => a - b);
}

/** The chart's forming bar and last closed bar times. */
export type ChartBars = { forming: number | null; lastClosed: number | null };

/**
 * The first forecast step that hasn't closed: normally step 1, the bar forming now (or next). While the
 * forecast is a bar behind (the last bar is still downloading), a later step. -1 once all have closed.
 */
export function currentStep(times: number[], bars: ChartBars): number {
  return times.findIndex((t) => (bars.forming !== null ? t >= bars.forming : bars.lastClosed === null || t > bars.lastClosed));
}

export type CandleWindow = {
  /** "Forming now", "Next candle", "Today's session", "Next session" */
  title: string;
  /** "10:35–10:40 IST", "Fri 25 Sep, 09:15–09:20 IST", "15:15 IST to the close", "Thu 25 Sep" */
  when: string;
  forming: boolean;
};

/**
 * Names the bar of step `index`. A step whose next step falls on a later IST day is its session's last
 * bar, which can end early (NSE 1h: 15:15–15:30), so it reads "to the close".
 */
export function candleWindow(times: number[], index: number, tf: string, forming: number | null, now: number): CandleWindow | null {
  const start = times[index];
  if (start === undefined) return null;
  const isForming = forming === start;
  if (tf === "1D") return { title: isForming ? "Today's session" : "Next session", when: formatSessionIST(start), forming: isForming };
  const next = times[index + 1];
  const day = sameDayIST(start, now) ? "" : `${formatSessionIST(start)}, `;
  const when =
    next !== undefined && !sameDayIST(start, next)
      ? `${day}${formatTimeIST(start)} IST to the close`
      : `${day}${formatTimeIST(start)}–${formatTimeIST(start + tfSeconds(tf))} IST`;
  return { title: isForming ? "Forming now" : "Next candle", when, forming: isForming };
}

/**
 * The newest forecast, but when it comes back without steps (no ranges, no ghost candles) the previous
 * one's steps for the same chart carry over, with the reference bar they were built from. The call and
 * its reason are always the newest. Steps age out on their own once their bars close (currentStep).
 */
export function withLastSteps(next: Forecast, prev: Forecast | undefined): Forecast {
  if (next.bands.length > 0 || next.ghost_candles.length > 0) return next;
  if (!prev || prev.instrument !== next.instrument || prev.tf !== next.tf) return next;
  if (prev.bands.length === 0 && prev.ghost_candles.length === 0) return next;
  return { ...next, ref_time: prev.ref_time, ref_close: prev.ref_close, bands: prev.bands, ghost_candles: prev.ghost_candles };
}

/**
 * How often the 80% range held. analog_v1 was measured in go/no-go #1 (NSE daily bars, validation
 * 2019–2025); the API doesn't carry this yet, so it is quoted here with where it was measured.
 */
export function coverageNote(f: Pick<Forecast, "instrument" | "tf" | "method">): string {
  if (f.method !== "analog_v1") return "80% range — still being tested";
  const note = "80% range — right ~78% of the time in testing";
  return f.tf === "1D" && f.instrument.startsWith("NSE:") ? note : `${note} (NSE daily bars)`;
}
