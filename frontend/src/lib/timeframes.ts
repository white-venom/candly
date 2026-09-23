export const TIMEFRAMES = ["5m", "15m", "1h", "1D"] as const;

export function isIntraday(tf: string): boolean {
  return tf !== "1D";
}

/** Canonical order (5m, 15m, 1h, 1D); unknown values go last. */
export function sortTimeframes(tfs: readonly string[]): string[] {
  const rank = (tf: string) => {
    const i = (TIMEFRAMES as readonly string[]).indexOf(tf);
    return i === -1 ? TIMEFRAMES.length : i;
  };
  return [...tfs].sort((a, b) => rank(a) - rank(b));
}

/** 1D when the instrument has it, otherwise its first timeframe. */
export function defaultTimeframe(available: readonly string[]): string {
  return available.includes("1D") ? "1D" : (sortTimeframes(available)[0] ?? "1D");
}
