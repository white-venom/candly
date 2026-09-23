import { describe, expect, it, vi } from "vitest";
import { DEFAULT_SIZING, positionSize, readSizing } from "./sizing";

describe("positionSize", () => {
  it("floors capital × risk% / |entry − stop| and reports the ₹ actually at risk", () => {
    // 1,00,000 × 0.5% = ₹500 budget; 500 / 4.5 = 111.1 → 111 units, 111 × 4.5 = ₹499.50 at risk
    expect(positionSize({ entry: 103, stop: 98.5 }, DEFAULT_SIZING)).toEqual({
      qty: 111,
      riskPerUnit: 4.5,
      riskBudget: 500,
      riskAmount: 499.5,
      positionValue: 111 * 103,
    });
  });

  it("sizes shorts by the distance, whichever side the stop is on", () => {
    expect(positionSize({ entry: 98.5, stop: 103 }, DEFAULT_SIZING)?.qty).toBe(111);
  });

  it("keeps an exact fit instead of flooring float noise one unit short", () => {
    // 1.3 − 1 = 0.30000000000000004, and 30 / that = 99.99999999999999
    expect(positionSize({ entry: 1.3, stop: 1 }, { capital: 3000, riskPct: 1 })?.qty).toBe(100);
    expect(positionSize({ entry: 103, stop: 98 }, DEFAULT_SIZING)?.qty).toBe(100);
  });

  it("rounds down, never up, even just below the next unit", () => {
    expect(positionSize({ entry: 1240.4, stop: 1228.1 }, DEFAULT_SIZING)?.qty).toBe(40); // 500 / 12.3 = 40.65
    expect(positionSize({ entry: 100, stop: 98.9999 }, { capital: 10_000, riskPct: 1 })?.qty).toBe(99); // 100 / 1.0001 = 99.99
  });

  it("gives 0 units when one unit already risks more than the budget", () => {
    const size = positionSize({ entry: 25_000, stop: 24_000 }, DEFAULT_SIZING);
    expect(size).toMatchObject({ qty: 0, riskAmount: 0, riskBudget: 500 });
  });

  it("can't size a zero-distance stop or invalid settings", () => {
    expect(positionSize({ entry: 100, stop: 100 }, DEFAULT_SIZING)).toBeNull();
    expect(positionSize({ entry: 100, stop: 99 }, { capital: 0, riskPct: 0.5 })).toBeNull();
    expect(positionSize({ entry: 100, stop: 99 }, { capital: 1000, riskPct: 0 })).toBeNull();
  });
});

describe("readSizing", () => {
  it("defaults to ₹1,00,000 and 0.5% per trade", () => {
    expect(readSizing()).toEqual({ capital: 100_000, riskPct: 0.5 });
  });

  it("reads saved settings and ignores corrupt or invalid ones", () => {
    window.localStorage.setItem("candly.sizing", JSON.stringify({ capital: 250_000, riskPct: 1 }));
    expect(readSizing()).toEqual({ capital: 250_000, riskPct: 1 });
    window.localStorage.setItem("candly.sizing", "{not json");
    expect(readSizing()).toEqual(DEFAULT_SIZING);
    window.localStorage.setItem("candly.sizing", JSON.stringify({ capital: -5, riskPct: 1 }));
    expect(readSizing()).toEqual(DEFAULT_SIZING);
    window.localStorage.setItem("candly.sizing", JSON.stringify({ capital: 1000, riskPct: 150 }));
    expect(readSizing()).toEqual(DEFAULT_SIZING);
  });

  it("falls back to the defaults when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readSizing()).toEqual(DEFAULT_SIZING);
  });
});
