import { useId } from "react";
import type { AccuracyResponse } from "../api/types";
import { fmtInt, fmtProb } from "../lib/format";

type Bin = AccuracyResponse["calibration"][number];
type Point = Bin & { mean_pred: number; observed: number };

const W = 320;
const H = 300;
const M = { l: 44, r: 12, t: 12, b: 40 };

/** Empty bins carry nulls; plotting them as 0 would invent a badly calibrated corner. */
function isPoint(b: Bin): b is Point {
  return b.n > 0 && b.mean_pred !== null && b.observed !== null;
}

/** Tenths that cover every plotted value, at least one tenth wide. */
function domainOf(points: Point[]): [number, number] {
  const values = points.flatMap((p) => [p.mean_pred, p.observed]);
  let lo = Math.max(0, Math.floor(Math.min(...values) * 10));
  let hi = Math.min(10, Math.ceil(Math.max(...values) * 10));
  if (hi === lo) {
    lo = Math.max(0, lo - 1);
    hi = Math.min(10, hi + 1);
  }
  return [lo / 10, hi / 10];
}

/** Predicted p(up) against observed frequency. Points on the dashed diagonal are perfectly calibrated. */
export function CalibrationChart({ bins }: { bins: Bin[] }) {
  const titleId = useId();
  const points = bins.filter(isPoint).sort((a, b) => a.mean_pred - b.mean_pred);
  if (points.length === 0) return <p className="text-ink-muted">No graded forecasts yet</p>;

  const [lo, hi] = domainOf(points);
  const x = (v: number) => M.l + ((v - lo) / (hi - lo)) * (W - M.l - M.r);
  const y = (v: number) => H - M.b - ((v - lo) / (hi - lo)) * (H - M.t - M.b);
  const ticks = Array.from({ length: Math.round((hi - lo) * 10) + 1 }, (_, i) => Math.round(lo * 10 + i) / 10);
  const maxN = Math.max(...points.map((p) => p.n));
  const radius = (n: number) => 3 + 5 * Math.sqrt(n / maxN);

  return (
    <figure className="flex flex-col gap-2">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={titleId} className="w-full max-w-[420px]">
        <title id={titleId}>Calibration: predicted probability of up against the observed frequency</title>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={M.t} y2={H - M.b} className="stroke-grid" />
            <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} className="stroke-grid" />
            <text x={x(t)} y={H - M.b + 14} textAnchor="middle" className="fill-ink-faint text-[10px]">
              {Math.round(t * 100)}%
            </text>
            <text x={M.l - 6} y={y(t) + 3} textAnchor="end" className="fill-ink-faint text-[10px]">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} className="stroke-line-strong" />
        <line x1={M.l} x2={M.l} y1={M.t} y2={H - M.b} className="stroke-line-strong" />
        <line x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} strokeDasharray="4 3" className="stroke-ink-faint" />
        <polyline
          points={points.map((b) => `${x(b.mean_pred)},${y(b.observed)}`).join(" ")}
          fill="none"
          strokeWidth={2}
          className="stroke-accent"
        />
        {points.map((b) => (
          <circle key={`${b.bin_low}-${b.bin_high}`} cx={x(b.mean_pred)} cy={y(b.observed)} r={radius(b.n)} strokeWidth={2} className="fill-accent stroke-surface">
            <title>
              {`Bin ${fmtProb(b.bin_low, 0)}–${fmtProb(b.bin_high, 0)}: predicted ${fmtProb(b.mean_pred)}, observed ${fmtProb(b.observed)}, n=${fmtInt(b.n)}`}
            </title>
          </circle>
        ))}
        <text x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle" className="fill-ink-muted text-[11px]">
          Predicted p(up)
        </text>
        <text transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" className="fill-ink-muted text-[11px]">
          Observed up-rate
        </text>
      </svg>
      <figcaption className="text-xs text-ink-muted">
        Dots are probability bins (bigger = more forecasts); the dashed diagonal is perfect calibration. Abstained forecasts are excluded.
      </figcaption>
      <details className="text-xs">
        <summary className="cursor-pointer text-ink-muted">Table</summary>
        <table className="mt-1 w-full tabular-nums">
          <thead className="text-left text-ink-faint">
            <tr>
              <th className="py-0.5 font-normal">Bin</th>
              <th className="py-0.5 font-normal">Predicted</th>
              <th className="py-0.5 font-normal">Observed</th>
              <th className="py-0.5 font-normal">n</th>
            </tr>
          </thead>
          <tbody className="text-ink">
            {points.map((b) => (
              <tr key={`${b.bin_low}-${b.bin_high}`}>
                <td>
                  {fmtProb(b.bin_low, 0)}–{fmtProb(b.bin_high, 0)}
                </td>
                <td>{fmtProb(b.mean_pred)}</td>
                <td>{fmtProb(b.observed)}</td>
                <td>{fmtInt(b.n)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
