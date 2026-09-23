import { useId } from "react";
import { formatDateIST } from "../lib/time";

type Point = { time: number; value: number | null };

const W = 320;
const H = 130;
const M = { l: 44, r: 8, t: 8, b: 20 };

/** One measure over time on its own axis (never two measures on one chart). */
export function MiniLineChart({
  title,
  points: unsorted,
  format,
  reference,
}: {
  title: string;
  points: Point[];
  format: (v: number) => string;
  reference?: { value: number; label: string };
}) {
  const titleId = useId();
  const points = [...unsorted].sort((a, b) => a.time - b.time);
  const present = points.filter((p): p is { time: number; value: number } => p.value !== null);
  if (present.length === 0) {
    return (
      <div>
        <h3 className="text-xs text-ink-muted">{title}</h3>
        <p className="text-xs text-ink-faint">No values yet.</p>
      </div>
    );
  }

  const values = present.map((p) => p.value).concat(reference ? [reference.value] : []);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.1 || 0.01;
  lo -= pad;
  hi += pad;
  const t0 = points[0].time;
  const t1 = points[points.length - 1].time;
  const x = (t: number) => (t1 === t0 ? (M.l + W - M.r) / 2 : M.l + ((t - t0) / (t1 - t0)) * (W - M.l - M.r));
  const y = (v: number) => H - M.b - ((v - lo) / (hi - lo)) * (H - M.t - M.b);

  // Break the line wherever a value is missing.
  const segments: Point[][] = [];
  let current: Point[] = [];
  for (const p of points) {
    if (p.value === null) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push(p);
  }
  if (current.length) segments.push(current);

  return (
    <figure>
      <figcaption id={titleId} className="text-xs text-ink-muted">
        {title}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={titleId} className="w-full">
        {[lo + pad, (lo + hi) / 2, hi - pad].map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className="stroke-grid" />
            <text x={M.l - 6} y={y(v) + 3} textAnchor="end" className="fill-ink-faint text-[10px]">
              {format(v)}
            </text>
          </g>
        ))}
        {reference && (
          <g>
            <line x1={M.l} x2={W - M.r} y1={y(reference.value)} y2={y(reference.value)} strokeDasharray="4 3" className="stroke-ink-faint" />
            <text x={W - M.r} y={y(reference.value) - 3} textAnchor="end" className="fill-ink-faint text-[10px]">
              {reference.label}
            </text>
          </g>
        )}
        {segments.map((seg) => (
          <polyline
            key={seg[0].time}
            points={seg.map((p) => `${x(p.time)},${y(p.value as number)}`).join(" ")}
            fill="none"
            strokeWidth={2}
            className="stroke-accent"
          />
        ))}
        {present.map((p) => (
          <circle key={p.time} cx={x(p.time)} cy={y(p.value)} r={2.5} className="fill-accent">
            <title>{`${formatDateIST(p.time)}: ${format(p.value)}`}</title>
          </circle>
        ))}
        <text x={M.l} y={H - 4} className="fill-ink-faint text-[10px]">
          {formatDateIST(t0)}
        </text>
        <text x={W - M.r} y={H - 4} textAnchor="end" className="fill-ink-faint text-[10px]">
          {formatDateIST(t1)}
        </text>
      </svg>
    </figure>
  );
}
