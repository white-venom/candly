import clsx from "clsx";
import { fmtProb } from "../../lib/format";

function position(value: number, [lo, hi]: [number, number]): number {
  if (hi <= lo) return 50;
  return Math.min(100, Math.max(0, ((value - lo) / (hi - lo)) * 100));
}

/** Interval bar with the hit rate as a dot and the base rate as a tick; all rows share one domain. */
export function CiBar({ low, high, point, base, domain }: { low: number; high: number; point: number; base: number; domain: [number, number] }) {
  const tone = low > base ? "bg-up/50" : high < base ? "bg-down/50" : "bg-neutral/35";
  const left = position(low, domain);
  const right = position(high, domain);
  return (
    <div role="img" aria-label={`CI ${fmtProb(low)} to ${fmtProb(high)}, hit rate ${fmtProb(point)}, base rate ${fmtProb(base)}`} className="relative h-3.5 w-28">
      <div className="absolute inset-x-0 top-1/2 h-px bg-line" />
      <div className={clsx("absolute top-[3px] h-2 rounded-sm", tone)} style={{ left: `${left}%`, width: `${Math.max(right - left, 1)}%` }} />
      <div className="absolute top-0 h-3.5 w-px bg-ink-muted" style={{ left: `${position(base, domain)}%` }} />
      <div className="absolute top-[3px] size-2 -translate-x-1/2 rounded-full bg-ink" style={{ left: `${position(point, domain)}%` }} />
    </div>
  );
}
