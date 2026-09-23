import { describe, expect, it } from "vitest";
import type { Instrument } from "../api/types";
import { instruments, nifty } from "../test/fixtures";
import { expiryIsNear, groupInstruments, matchesSearch, unitLabel, watchlistOrder } from "./instruments";

const sensex: Instrument = { ...nifty, id: "BSE:SENSEX", exchange: "BSE", symbol: "SENSEX", name: "Sensex" };
const all = [...instruments, sensex, nifty];

describe("watchlist grouping", () => {
  it("orders sections NSE indices, NSE stocks, BSE, MCX and drops empty ones", () => {
    expect(groupInstruments(all).map((g) => [g.label, g.items.map((i) => i.symbol)])).toEqual([
      ["NSE Indices", ["NIFTY50"]],
      ["NSE Stocks", ["RELIANCE"]],
      ["BSE", ["SENSEX"]],
      ["MCX", ["CRUDEOIL"]],
    ]);
    expect(watchlistOrder(all).map((i) => i.symbol)).toEqual(["NIFTY50", "RELIANCE", "SENSEX", "CRUDEOIL"]);
  });

  it("searches symbol and name", () => {
    expect(all.filter((i) => matchesSearch(i, "rel")).map((i) => i.symbol)).toEqual(["RELIANCE"]);
    expect(all.filter((i) => matchesSearch(i, "crude oil")).map((i) => i.symbol)).toEqual(["CRUDEOIL"]);
    expect(all.filter((i) => matchesSearch(i, " "))).toHaveLength(4);
  });

  it("flags an expiry today or on the next trading day", () => {
    expect(expiryIsNear(nifty.expiry)).toBe(true);
    expect(expiryIsNear({ next: "2026-09-23", kind: "weekly", days_to_expiry: 0, is_expiry_day: true })).toBe(true);
    expect(expiryIsNear(instruments[0].expiry)).toBe(false);
    expect(expiryIsNear(null)).toBe(false);
  });

  it("says units are not lots for indices and MCX", () => {
    expect(unitLabel(nifty)).toBe("units (not lots)");
    expect(unitLabel(instruments[1])).toBe("units (not lots)");
    expect(unitLabel(instruments[0])).toBe("units");
  });
});
