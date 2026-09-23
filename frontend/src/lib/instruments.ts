import type { ExpiryInfo, Instrument } from "../api/types";

type Group = { key: string; label: string; test: (i: Instrument) => boolean };

const GROUPS: Group[] = [
  { key: "nse-index", label: "NSE Indices", test: (i) => i.exchange === "NSE" && i.kind === "index" },
  { key: "nse-stock", label: "NSE Stocks", test: (i) => i.exchange === "NSE" && i.kind !== "index" },
  { key: "bse", label: "BSE", test: (i) => i.exchange === "BSE" },
  { key: "mcx", label: "MCX", test: (i) => i.exchange === "MCX" },
];

export type InstrumentGroup = { key: string; label: string; items: Instrument[] };

/** Watchlist sections, in a fixed order; empty sections are dropped. */
export function groupInstruments(instruments: Instrument[]): InstrumentGroup[] {
  return GROUPS.map(({ key, label, test }) => ({ key, label, items: instruments.filter(test) })).filter((g) => g.items.length > 0);
}

/** Every instrument in watchlist order: what `[` and `]` step through. */
export function watchlistOrder(instruments: Instrument[]): Instrument[] {
  return groupInstruments(instruments).flatMap((g) => g.items);
}

export function matchesSearch(i: Instrument, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || i.symbol.toLowerCase().includes(q) || i.name.toLowerCase().includes(q) || i.id.toLowerCase().includes(q);
}

/** Expiry today or on the next trading day: worth a chip in the watchlist. */
export function expiryIsNear(expiry: ExpiryInfo | null | undefined): expiry is ExpiryInfo {
  return Boolean(expiry && (expiry.is_expiry_day || expiry.days_to_expiry <= 1));
}

/** Indices and MCX futures trade in lots, so a unit count needs saying so. */
export function unitLabel(i: Pick<Instrument, "kind" | "exchange"> | undefined): string {
  return i && (i.kind === "index" || i.exchange === "MCX") ? "units (not lots)" : "units";
}
