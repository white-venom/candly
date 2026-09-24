export const DASH = "—";

type Num = number | null | undefined;

const priceFormat = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const intFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

function isNum(x: Num): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

/** Probability 0–1 → "56.2%". */
export function fmtProb(p: Num, digits = 1): string {
  return isNum(p) ? `${(p * 100).toFixed(digits)}%` : DASH;
}

/** Difference of two probabilities → "+5.3 pts". */
export function fmtPts(diff: Num, digits = 1): string {
  if (!isNum(diff)) return DASH;
  const v = diff * 100;
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)} pts`;
}

/** Fields ending in `_pct` are already percent units: 1.25 → "+1.25%". */
export function fmtPct(x: Num, digits = 2, signed = true): string {
  if (!isNum(x)) return DASH;
  return `${signed && x > 0 ? "+" : ""}${x.toFixed(digits)}%`;
}

export function fmtPrice(x: Num): string {
  return isNum(x) ? priceFormat.format(x) : DASH;
}

const wholeFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Prices in a forecast range: whole numbers from 1,000 up (23,410), two decimals below (268.40). */
export function fmtRangePrice(x: Num): string {
  if (!isNum(x)) return DASH;
  return Math.abs(x) >= 1000 ? wholeFormat.format(x) : priceFormat.format(x);
}

/** Rupees, Indian digit grouping: 100000 → "₹1,00,000". */
export function fmtInr(x: Num, digits = 0): string {
  return isNum(x) ? `₹${x.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}` : DASH;
}

export function fmtInt(x: Num): string {
  return isNum(x) ? intFormat.format(x) : DASH;
}

export function fmtNum(x: Num, digits = 2): string {
  return isNum(x) ? x.toFixed(digits) : DASH;
}

/** p- and q-values: three decimals, with a floor instead of a misleading 0.000. */
export function fmtPValue(x: Num): string {
  if (!isNum(x)) return DASH;
  return x < 0.001 ? "<0.001" : x.toFixed(3);
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

/** Tailwind text class for the sign of a value; the sign itself is always printed as well. */
export function signTone(value: Num): string {
  if (!isNum(value) || value === 0) return "text-ink-muted";
  return value > 0 ? "text-up" : "text-down";
}

export type Sentiment = "positive" | "negative" | "neutral" | "unknown";

/** News sentiment −1..1, with a small dead zone so near-zero scores read as neutral. */
export function sentimentOf(value: number | null): Sentiment {
  if (value === null) return "unknown";
  if (value > 0.1) return "positive";
  if (value < -0.1) return "negative";
  return "neutral";
}

/** A pattern's track record in one line: "hit 58% vs base 52% · n 41". */
export function fmtStats(stats: { hit_rate: number; base_rate: number; n: number } | null): string | null {
  if (!stats) return null;
  return `hit ${fmtProb(stats.hit_rate, 0)} vs base ${fmtProb(stats.base_rate, 0)} · n ${fmtInt(stats.n)}`;
}

export function fmtVolume(x: Num): string {
  if (!isNum(x)) return DASH;
  if (x >= 1e7) return `${(x / 1e7).toFixed(2)} Cr`;
  if (x >= 1e5) return `${(x / 1e5).toFixed(2)} L`;
  return intFormat.format(x);
}
