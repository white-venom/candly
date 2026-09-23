import { useState } from "react";
import type { Trade } from "../api/types";
import { readStorage, writeStorage } from "./storage";

/** The user's own sizing inputs. riskPct is in percent units: 0.5 means 0.5% of capital per trade. */
export type Sizing = { capital: number; riskPct: number };

export const DEFAULT_SIZING: Sizing = { capital: 100_000, riskPct: 0.5 };

const KEY = "candly.sizing";

export function isValidCapital(x: number): boolean {
  return Number.isFinite(x) && x > 0;
}

export function isValidRiskPct(x: number): boolean {
  return Number.isFinite(x) && x > 0 && x <= 100;
}

export function readSizing(): Sizing {
  const raw = readStorage(KEY);
  if (!raw) return DEFAULT_SIZING;
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object") {
      const { capital, riskPct } = value as Record<string, unknown>;
      if (typeof capital === "number" && typeof riskPct === "number" && isValidCapital(capital) && isValidRiskPct(riskPct)) {
        return { capital, riskPct };
      }
    }
  } catch {
    // corrupt value
  }
  return DEFAULT_SIZING;
}

export function useSizing() {
  const [sizing, setSizing] = useState<Sizing>(readSizing);
  const update = (next: Sizing) => {
    writeStorage(KEY, JSON.stringify(next));
    setSizing(next);
  };
  return { sizing, update };
}

export type PositionSize = {
  qty: number;
  /** |entry − stop| per unit */
  riskPerUnit: number;
  /** capital × risk% */
  riskBudget: number;
  /** qty × riskPerUnit: what the stop actually costs after rounding down */
  riskAmount: number;
  positionValue: number;
};

// Absorbs float noise such as 30 / 0.30000000000000004 so an exact fit isn't floored one unit short.
const EPSILON = 1e-9;

/** qty = floor(capital × risk% / |entry − stop|), per docs/CONTRACTS.md. null when it can't be sized. */
export function positionSize(trade: Pick<Trade, "entry" | "stop">, sizing: Sizing): PositionSize | null {
  const riskPerUnit = Math.abs(trade.entry - trade.stop);
  const riskBudget = (sizing.capital * sizing.riskPct) / 100;
  if (!(riskPerUnit > 0) || !isValidCapital(sizing.capital) || !isValidRiskPct(sizing.riskPct)) return null;
  const qty = Math.floor(riskBudget / riskPerUnit + EPSILON);
  return { qty, riskPerUnit, riskBudget, riskAmount: qty * riskPerUnit, positionValue: qty * trade.entry };
}
