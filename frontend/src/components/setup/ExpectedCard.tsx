import clsx from "clsx";
import type { Forecast } from "../../api/types";
import { candleWindow, coverageNote, currentStep, expectedRanges, forecastTimes, type ChartBars, type ExpectedRange } from "../../lib/expected";
import { fmtProb, fmtRangePrice } from "../../lib/format";
import { abstainReason } from "../../lib/messages";
import { formatSessionIST, formatWhenIST, nowUnix } from "../../lib/time";
import { forecastVerdict } from "../../lib/verdict";
import { Eyebrow } from "../ui/Chip";

/** Up or down only for a directional call; otherwise plainly unclear, with why. */
function Lean({ f, tf, canConnect, now }: { f: Forecast; tf: string; canConnect: boolean; now: number }) {
  const verdict = forecastVerdict(f);
  if (verdict === "none") {
    return (
      <div className="mt-3">
        <p className="text-sm font-medium text-ink">Up/down unclear</p>
        <p className="text-xs text-ink-muted">{abstainReason(f.abstain_reason, { tf, canConnect, now }).text}</p>
      </div>
    );
  }
  const up = verdict === "bullish";
  const odds = up ? fmtProb(f.p_up, 0) : `${fmtProb(f.p_up, 0)} up`;
  return (
    <p className={clsx("mt-3 text-sm font-medium", up ? "text-up" : "text-down")}>
      <span aria-hidden="true">{up ? "▲" : "▼"} </span>
      Leaning {up ? "up" : "down"} over {f.horizon_bars} {tf === "1D" ? "sessions" : "bars"}{" "}
      <span className="font-normal text-ink-muted tabular-nums">
        ({odds} vs usual {fmtProb(f.base_rate, 0)})
      </span>
    </p>
  );
}

const STRIP_W = 276;
const STRIP_H = 48;
const STRIP_PAD = 4;
const BAR_W = 12;

/** Each step's 80% range on one shared scale, against the last close (dashed). */
function NextStrip({ ranges, refClose, tf, now }: { ranges: ExpectedRange[]; refClose: number; tf: string; now: number }) {
  const lo = Math.min(refClose, ...ranges.map((r) => r.low));
  const hi = Math.max(refClose, ...ranges.map((r) => r.high));
  const y = (price: number) => (hi === lo ? STRIP_H / 2 : STRIP_PAD + ((hi - price) / (hi - lo)) * (STRIP_H - 2 * STRIP_PAD));
  const cx = (i: number) => ((i + 0.5) * STRIP_W) / ranges.length;

  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="flex items-baseline justify-between gap-2">
        <Eyebrow>Next {ranges.length}</Eyebrow>
        <span className="inline-flex items-center gap-1 text-2xs text-ink-faint tabular-nums">
          <span aria-hidden="true" className="w-3 border-t border-dashed border-line-strong" />
          last close {fmtRangePrice(refClose)}
        </span>
      </div>
      <svg viewBox={`0 0 ${STRIP_W} ${STRIP_H}`} aria-hidden="true" className="mt-2 block h-12 w-full">
        <line x1={0} x2={STRIP_W} y1={y(refClose)} y2={y(refClose)} strokeDasharray="3 3" className="stroke-line-strong" />
        {ranges.map((r, i) => (
          <g key={r.time}>
            <rect x={cx(i) - BAR_W / 2} y={y(r.high)} width={BAR_W} height={Math.max(2, y(r.low) - y(r.high))} rx={2} className="fill-band/20 stroke-band" />
            <line x1={cx(i) - BAR_W / 2 - 3} x2={cx(i) + BAR_W / 2 + 3} y1={y(r.mid)} y2={y(r.mid)} strokeWidth={2} className="stroke-band" />
          </g>
        ))}
      </svg>
      <ol
        aria-label={`Next ${ranges.length} expected ranges`}
        className="mt-1 grid text-center text-2xs tabular-nums"
        style={{ gridTemplateColumns: `repeat(${ranges.length}, minmax(0, 1fr))` }}
      >
        {ranges.map((r) => (
          <li key={r.time}>
            <span className="block text-ink-muted">{tf === "1D" ? formatSessionIST(r.time) : formatWhenIST(r.time, now)}</span>
            <span className="block text-ink-faint">
              {fmtRangePrice(r.low)}–{fmtRangePrice(r.high)}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/**
 * The bar the forecast is about, in plain terms: when it is, where it will likely close (the 80% range,
 * which holds up in testing), and which way it leans only when there is a call. A forecast without
 * steps (stale data, no model data) says so instead of leaving the trader looking for it.
 */
export function ExpectedCard({
  forecast: f,
  tf,
  bars,
  canConnect,
  now = nowUnix(),
}: {
  forecast: Forecast;
  tf: string;
  /** the chart's forming and last closed bar times: which step is the live one */
  bars: ChartBars;
  canConnect: boolean;
  now?: number;
}) {
  const times = forecastTimes(f);
  if (times.length === 0) {
    return (
      <section aria-label="Expected next candle" className="rounded-lg border border-line p-4">
        <Eyebrow>Expected next candle</Eyebrow>
        <p role="status" className="mt-1 text-sm text-ink-muted">
          No expected range right now.{f.abstain && ` ${abstainReason(f.abstain_reason, { tf, canConnect, now }).text}`}
        </p>
      </section>
    );
  }
  const step = currentStep(times, bars);
  const ranges = step === -1 ? [] : expectedRanges(f).slice(step);
  const slot = step === -1 ? null : candleWindow(times, step, tf, bars.forming, now);
  const first = ranges[0];

  return (
    <section aria-label="Expected next candle" className="rounded-lg border border-band/60 bg-band/5 p-4">
      <h3 className="text-2xs font-semibold tracking-wider text-band uppercase">Expected next candle</h3>
      {slot ? (
        <p className="mt-1 text-sm font-medium text-ink tabular-nums">
          <span className={clsx(slot.forming && "text-forming")}>{slot.title}</span> · {slot.when}
        </p>
      ) : (
        <p role="status" className="mt-1 text-sm text-ink-muted">
          The bars in this forecast have closed. A fresh one comes with the next bar.
        </p>
      )}
      {slot && first ? (
        <div className="mt-3">
          <p className="text-lg leading-6 font-semibold text-ink tabular-nums">
            <span className="text-sm font-normal text-ink-muted">Likely range </span>
            {fmtRangePrice(first.low)} – {fmtRangePrice(first.high)}
          </p>
          <p className="mt-0.5 text-xs text-ink-muted tabular-nums">Middle {fmtRangePrice(first.mid)}</p>
          <p className="text-2xs text-ink-faint">{coverageNote(f)}</p>
        </div>
      ) : (
        slot && <p className="mt-3 text-xs text-ink-muted">No price range came with this forecast.</p>
      )}
      <Lean f={f} tf={tf} canConnect={canConnect} now={now} />
      {ranges.length > 0 && <NextStrip ranges={ranges} refClose={f.ref_close} tf={tf} now={now} />}
    </section>
  );
}
