import type { Forecast, ScannerRow } from "../api/types";

export type Verdict = "bullish" | "bearish" | "none";

/** A call only when the forecast doesn't abstain and p(up) sits off the base rate. */
export function forecastVerdict(f: Pick<Forecast, "abstain" | "p_up" | "base_rate">): Verdict {
  if (f.abstain || f.p_up === null || f.base_rate === null || f.p_up === f.base_rate) return "none";
  return f.p_up > f.base_rate ? "bullish" : "bearish";
}

export function scannerVerdict(row: Pick<ScannerRow, "abstain" | "direction">): Verdict {
  return row.abstain || row.direction === "neutral" ? "none" : row.direction;
}

export const VERDICT_LABEL: Record<Verdict, string> = { bullish: "Bullish", bearish: "Bearish", none: "No edge" };
