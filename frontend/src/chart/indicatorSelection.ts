import { useMemo, useState } from "react";
import type { IndicatorInfo } from "../api/types";
import { INDICATOR_SLOTS } from "../lib/palette";
import { readStorage, writeStorage } from "../lib/storage";
import type { EnabledIndicator } from "./transforms";

const KEY = "candly.indicators";
const DEFAULTS = ["ema20", "ema50"];

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
    return value.filter(
      (e): e is EnabledIndicator => typeof e?.name === "string" && Number.isInteger(e?.slot) && e.slot >= 0,
    );
  } catch {
    return null;
  }
}

export function useIndicatorSelection(catalog: IndicatorInfo[] | undefined) {
  const [saved, setSaved] = useState<EnabledIndicator[] | null>(readSaved);

  const enabled = useMemo(() => {
    if (!catalog) return [];
    const known = new Set(catalog.map((c) => c.name));
    const base = saved ?? DEFAULTS.filter((n) => known.has(n)).map((name, slot) => ({ name, slot }));
    return base.filter((e) => known.has(e.name));
  }, [catalog, saved]);

  const toggle = (name: string) => {
    const next = toggleIndicator(enabled, name);
    writeStorage(KEY, JSON.stringify(next));
    setSaved(next);
  };

  return { enabled, toggle };
}
