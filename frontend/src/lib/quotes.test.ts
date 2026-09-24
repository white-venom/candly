import { describe, expect, it } from "vitest";
import type { Candle, CandlesResponse } from "../api/types";
import { scannerRow } from "../test/fixtures";
import { watchQuote } from "./quotes";

const session = (d: number) => Date.UTC(2026, 8, d, 3, 45) / 1000;
const bar = (time: number, close: number): Candle => ({ time, open: close, high: close, low: close, close, volume: 0 });
const daily = (closed: Candle[], forming: Candle | null): CandlesResponse => ({ instrument: "NSE:NIFTY50", tf: "1D", source: "fyers", candles: closed, forming });
/** the slow daily scanner's row: yesterday's close */
const row = scannerRow({ instrument: "NSE:NIFTY50", time: session(23), last_close: 23446.8, change_pct: 0.5 });

describe("watchlist quote", () => {
  it("during the session: today's forming bar, i.e. the latest 5m close, against yesterday's close", () => {
    const q = watchQuote(daily([bar(session(22), 23330), bar(session(23), 23446.8)], bar(session(24), 23233.2)), row)!;
    expect(q).toMatchObject({ price: 23233.2, time: session(24), live: true });
    expect(q.changePct).toBeCloseTo(((23233.2 - 23446.8) / 23446.8) * 100, 6);
  });

  it("outside the session: the last close and that day's change", () => {
    const q = watchQuote(daily([bar(session(22), 23330), bar(session(23), 23446.8)], null), row)!;
    expect(q).toMatchObject({ price: 23446.8, time: session(23), live: false });
    expect(q.changePct).toBeCloseTo(((23446.8 - 23330) / 23330) * 100, 6);
  });

  it("ignores a forming bar that isn't newer than the last closed one", () => {
    expect(watchQuote(daily([bar(session(22), 23330), bar(session(23), 23446.8)], bar(session(23), 1)), row)).toMatchObject({ price: 23446.8, live: false });
  });

  it("falls back to the scanner row, and shows nothing with no data at all", () => {
    expect(watchQuote(undefined, row)).toEqual({ price: 23446.8, changePct: 0.5, time: session(23), live: false });
    expect(watchQuote(daily([], null), row)).toMatchObject({ price: 23446.8 });
    expect(watchQuote(daily([bar(session(23), 23446.8)], null), undefined)).toMatchObject({ price: 23446.8, changePct: null });
    expect(watchQuote(undefined, undefined)).toBeNull();
  });
});
