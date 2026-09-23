import type { Candle } from "../../api/types";
import { fmtPrice } from "../../lib/format";

const H = 36;
const STEP_W = 22;
const BODY_W = 6;

function Glyph({ candle, x, y, predicted }: { candle: Candle; x: number; y: (v: number) => number; predicted: boolean }) {
  const up = candle.close >= candle.open;
  const top = y(Math.max(candle.open, candle.close));
  const bottom = y(Math.min(candle.open, candle.close));
  const cx = x + BODY_W / 2;
  return (
    <g className={up ? "stroke-up" : "stroke-down"}>
      <line x1={cx} x2={cx} y1={y(candle.high)} y2={y(candle.low)} strokeWidth={1} />
      <rect
        x={x}
        y={top}
        width={BODY_W}
        height={Math.max(bottom - top, 1)}
        strokeWidth={1}
        className={predicted ? (up ? "fill-up/25" : "fill-down/25") : up ? "fill-up" : "fill-down"}
      />
    </g>
  );
}

/** Per step: the predicted candle (translucent) beside the actual one (solid), on one shared price scale. */
export function CandleGlyphs({ predicted, actual }: { predicted: Candle[]; actual: Candle[] }) {
  const all = [...predicted, ...actual];
  if (all.length === 0) return <span className="text-ink-faint">—</span>;
  const lo = Math.min(...all.map((c) => c.low));
  const hi = Math.max(...all.map((c) => c.high));
  const y = (v: number) => (hi === lo ? H / 2 : 2 + (1 - (v - lo) / (hi - lo)) * (H - 4));
  const steps = Math.max(predicted.length, actual.length);

  const description = Array.from({ length: steps }, (_, i) => {
    const p = predicted[i];
    const a = actual[i];
    return `step ${i + 1}: predicted close ${p ? fmtPrice(p.close) : "—"}, actual ${a ? fmtPrice(a.close) : "pending"}`;
  }).join("; ");

  return (
    <svg width={steps * STEP_W} height={H} viewBox={`0 0 ${steps * STEP_W} ${H}`} role="img" aria-label={`Predicted vs actual candles — ${description}`}>
      {Array.from({ length: steps }, (_, i) => {
        const p = predicted[i];
        const a = actual[i];
        const x = i * STEP_W + 2;
        return (
          <g key={i}>
            {p && <Glyph candle={p} x={x} y={y} predicted />}
            {a ? (
              <Glyph candle={a} x={x + BODY_W + 3} y={y} predicted={false} />
            ) : (
              <rect x={x + BODY_W + 3} y={H / 2 - 5} width={BODY_W} height={10} strokeDasharray="2 2" className="fill-none stroke-ink-faint" />
            )}
          </g>
        );
      })}
    </svg>
  );
}
