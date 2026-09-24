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

const TF_SECONDS: Record<string, number> = { "5m": 300, "15m": 900, "1h": 3600, "1D": 86400 };

/** A bar's nominal length. The last bar of a session can be shorter (NSE 1h: 15:15–15:30). */
export function tfSeconds(tf: string): number {
  return TF_SECONDS[tf] ?? 86400;
}
