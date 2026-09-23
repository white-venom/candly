import type { Instrument } from "../api/types";
import { readStorage, writeStorage } from "./storage";
import { defaultTimeframe } from "./timeframes";

/** `/chart/NSE:RELIANCE/1D` — the colon stays readable; anything else unsafe is encoded. */
export function chartPath(instrument: string, tf: string): string {
  const id = encodeURIComponent(instrument).replace(/%3A/gi, ":");
  return `/chart/${id}/${encodeURIComponent(tf)}`;
}

export type Selection = { instrument: string; tf: string };

const LAST_KEY = "candly.last";

export function readLastSelection(): Selection | null {
  const raw = readStorage(LAST_KEY);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object" && "instrument" in value && "tf" in value) {
      const { instrument, tf } = value as Record<string, unknown>;
      if (typeof instrument === "string" && typeof tf === "string") return { instrument, tf };
    }
  } catch {
    // corrupt value
  }
  return null;
}

export function writeLastSelection(selection: Selection): void {
  writeStorage(LAST_KEY, JSON.stringify(selection));
}

const PREFERRED_DEFAULT = "NSE:NIFTY50";

/** Last viewed chart if it still exists, else Nifty 50, else the first instrument. */
export function defaultSelection(instruments: Instrument[], last: Selection | null): Selection | null {
  const known = last && instruments.find((i) => i.id === last.instrument);
  if (known) {
    return { instrument: known.id, tf: known.timeframes.includes(last.tf) ? last.tf : defaultTimeframe(known.timeframes) };
  }
  const pick = instruments.find((i) => i.id === PREFERRED_DEFAULT) ?? instruments[0];
  return pick ? { instrument: pick.id, tf: defaultTimeframe(pick.timeframes) } : null;
}
