import { useState } from "react";
import type { PatternSignal } from "../api/types";
import { readStorage, writeStorage } from "./storage";

/** Neutral patterns are three quarters of the flow and carry no stop, so they are hidden by default. */
export type SignalFilters = { showNeutral: boolean; certifiedOnly: boolean };

export const DEFAULT_SIGNAL_FILTERS: SignalFilters = { showNeutral: false, certifiedOnly: false };

const KEY = "candly.signalFilters";

export function readSignalFilters(): SignalFilters {
  const raw = readStorage(KEY);
  if (!raw) return DEFAULT_SIGNAL_FILTERS;
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object") {
      const { showNeutral, certifiedOnly } = value as Record<string, unknown>;
      return {
        showNeutral: typeof showNeutral === "boolean" ? showNeutral : DEFAULT_SIGNAL_FILTERS.showNeutral,
        certifiedOnly: typeof certifiedOnly === "boolean" ? certifiedOnly : DEFAULT_SIGNAL_FILTERS.certifiedOnly,
      };
    }
  } catch {
    // corrupt value
  }
  return DEFAULT_SIGNAL_FILTERS;
}

export function useSignalFilters() {
  const [filters, setFilters] = useState<SignalFilters>(readSignalFilters);
  const update = (next: SignalFilters) => {
    writeStorage(KEY, JSON.stringify(next));
    setFilters(next);
  };
  return { filters, update };
}

export function emptySignalsText(filters: SignalFilters): string {
  if (filters.certifiedOnly) return "No certified signals on this chart.";
  if (!filters.showNeutral) return "No directional signals on this chart yet. Neutral patterns can be shown from the Patterns menu.";
  return "No pattern signals on this chart yet.";
}

/** The API filters these too; repeating it here keeps an older backend, which ignores the query flags, consistent. */
export function applySignalFilters(signals: PatternSignal[], filters: SignalFilters): PatternSignal[] {
  return signals.filter(
    (s) => (filters.showNeutral || s.direction !== "neutral") && (!filters.certifiedOnly || s.stats?.certified === true),
  );
}
