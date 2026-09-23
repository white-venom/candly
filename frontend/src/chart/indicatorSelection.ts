import { useMemo, useState } from "react";
import type { IndicatorInfo } from "../api/types";
import { INDICATOR_SLOTS } from "../lib/palette";
import { readStorage, writeStorage } from "../lib/storage";
import type { EnabledIndicator } from "./transforms";

const KEY = "candly.indicators";
const DEFAULTS = ["ema20", "ema50"];

/** Family names the API expands to all their lines (docs/CONTRACTS.md); the menu shows one switch each. */
export const FAMILIES: Record<string, { label: string; members: string[] }> = {
  macd: { label: "MACD (12, 26, 9)", members: ["macd", "macd_signal", "macd_hist"] },
  bb: { label: "Bollinger Bands (20, 2)", members: ["bb_upper", "bb_mid", "bb_lower"] },
  stoch: { label: "Stochastic (14, 3, 3)", members: ["stoch_k", "stoch_d"] },
  adx: { label: "ADX / DMI (14)", members: ["adx14", "plus_di14", "minus_di14"] },
};

export const PRESETS: { id: string; label: string; names: string[] }[] = [
  { id: "clean", label: "Clean", names: [] },
  { id: "trend", label: "Trend: EMA 20/50/200", names: ["ema20", "ema50", "ema200"] },
  { id: "momentum", label: "Momentum: RSI + MACD", names: ["rsi14", "macd"] },
];

/** The catalog as the menu lists it: each family's lines folded into one entry. */
export function menuEntries(catalog: IndicatorInfo[]): IndicatorInfo[] {
  const out: IndicatorInfo[] = [];
  const seen = new Set<string>();
  for (const info of catalog) {
    const family = Object.entries(FAMILIES).find(([, f]) => f.members.includes(info.name));
    if (!family) {
      out.push(info);
      continue;
    }
    const [name, { label }] = family;
    if (seen.has(name)) continue;
    seen.add(name);
    out.push({ ...info, name, label });
  }
  return out;
}

function firstFreeSlot(enabled: EnabledIndicator[]): number {
  for (let slot = 0; slot < INDICATOR_SLOTS; slot++) {
    if (!enabled.some((e) => e.slot === slot)) return slot;
  }
  return enabled.length % INDICATOR_SLOTS;
}

/** Turning one indicator on or off never changes the colour slot of the others. */
export function toggleIndicator(enabled: EnabledIndicator[], name: string): EnabledIndicator[] {
  if (enabled.some((e) => e.name === name)) return enabled.filter((e) => e.name !== name);
  return [...enabled, { name, slot: firstFreeSlot(enabled) }];
}

function readSaved(): EnabledIndicator[] | null {
  const raw = readStorage(KEY);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return null;
    return value.filter((e): e is EnabledIndicator => typeof e?.name === "string" && Number.isInteger(e?.slot) && e.slot >= 0);
  } catch {
    return null;
  }
}

export function useIndicatorSelection(catalog: IndicatorInfo[] | undefined) {
  const [saved, setSaved] = useState<EnabledIndicator[] | null>(readSaved);
  const entries = useMemo(() => (catalog ? menuEntries(catalog) : []), [catalog]);

  const enabled = useMemo(() => {
    const known = new Set(entries.map((e) => e.name));
    const base = saved ?? DEFAULTS.map((name, slot) => ({ name, slot }));
    return base.filter((e) => known.has(e.name));
  }, [entries, saved]);

  const save = (next: EnabledIndicator[]) => {
    writeStorage(KEY, JSON.stringify(next));
    setSaved(next);
  };

  return {
    entries,
    enabled,
    toggle: (name: string) => save(toggleIndicator(enabled, name)),
    applyPreset: (names: string[]) => save(names.map((name, slot) => ({ name, slot }))),
  };
}
