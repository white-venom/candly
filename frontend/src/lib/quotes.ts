import type { CandlesResponse, ScannerRow } from "../api/types";

export type Quote = {
  price: number;
  /** percent units: the day's change against the previous session's close */
  changePct: number | null;
  /** the daily bar the price comes from */
  time: number;
  /** from today's forming daily bar, i.e. the latest 5m close */
  live: boolean;
};

/**
 * The watchlist price from the instrument's last two daily bars. While its market is open the forming
 * daily bar (built from today's 5m bars) gives the latest price, against the last closed session;
 * otherwise the last close and that day's change. The daily scanner row is the fallback.
 */
export function watchQuote(daily: CandlesResponse | undefined, scanner: ScannerRow | undefined): Quote | null {
  if (daily) {
    const closed = daily.candles;
    const forming = daily.forming && (closed.length === 0 || daily.forming.time > closed[closed.length - 1].time) ? daily.forming : null;
    const bars = forming ? [...closed, forming] : closed;
    const last = bars.at(-1);
    const prev = bars.at(-2);
    if (last) {
      const changePct = prev && prev.close !== 0 ? ((last.close - prev.close) / prev.close) * 100 : null;
      return { price: last.close, changePct, time: last.time, live: forming !== null };
    }
  }
  return scanner ? { price: scanner.last_close, changePct: scanner.change_pct, time: scanner.time, live: false } : null;
}
