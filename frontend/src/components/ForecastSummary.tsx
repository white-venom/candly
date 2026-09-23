import type { Forecast } from "../api/types";
import { fmtInt, fmtPct, fmtPrice, fmtProb, fmtPts, signTone } from "../lib/format";
import { formatBarTimeIST, formatDateTimeIST } from "../lib/time";
import { TradeCard } from "./TradeCard";
import { Badge, DirectionTag, Stat } from "./ui";

function Abstaining({ forecast }: { forecast: Forecast }) {
  return (
    <div role="status" className="rounded-md border-2 border-dashed border-abstain p-3">
      <p className="text-base font-semibold text-ink">No clear edge right now — abstaining</p>
      <p className="mt-1 text-ink-muted">{forecast.abstain_reason ?? "The forecaster gave no reason."}</p>
      {forecast.p_up !== null && (
        <p className="mt-2 text-xs text-ink-faint">
          Context only, not a call: p(up) {fmtProb(forecast.p_up)} vs base rate {fmtProb(forecast.base_rate)}
        </p>
      )}
    </div>
  );
}

function Call({ forecast }: { forecast: Forecast }) {
  const edge = forecast.p_up !== null && forecast.base_rate !== null ? forecast.p_up - forecast.base_rate : null;
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-2xl font-semibold text-ink tabular-nums">{fmtProb(forecast.p_up)}</span>
        <span className="text-ink-muted">p(up) in {forecast.horizon_bars} bars</span>
        {edge !== null && edge !== 0 && <DirectionTag direction={edge > 0 ? "bullish" : "bearish"} />}
      </div>
      <p className="mt-1 text-ink-muted tabular-nums">
        vs base rate {fmtProb(forecast.base_rate)} <span className={signTone(edge)}>({fmtPts(edge)})</span>
        {forecast.p_up_ci && (
          <>
            {" · "}CI {fmtProb(forecast.p_up_ci[0])}–{fmtProb(forecast.p_up_ci[1])}
          </>
        )}
      </p>
    </div>
  );
}

const CONFIDENCE_TONE = { low: "neutral", medium: "accent", high: "up" } as const;

export function ForecastSummary({ forecast, tf }: { forecast: Forecast; tf: string }) {
  return (
    <div className="flex flex-col gap-3">
      {forecast.abstain ? <Abstaining forecast={forecast} /> : <Call forecast={forecast} />}
      {!forecast.abstain && forecast.trade && <TradeCard trade={forecast.trade} />}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        <Stat
          label="Confidence"
          value={forecast.confidence ? <Badge tone={CONFIDENCE_TONE[forecast.confidence]}>{forecast.confidence}</Badge> : "—"}
        />
        <Stat label="Expected move" value={fmtPct(forecast.expected_move_pct)} />
        <Stat label="Analogs" value={fmtInt(forecast.n_analogs)} />
        <Stat label="Reference close" value={fmtPrice(forecast.ref_close)} sub={`${formatBarTimeIST(forecast.ref_time, tf)} IST`} />
        <Stat label="Invalidation" value={fmtPrice(forecast.invalidation)} />
        <Stat label="Method" value={forecast.method} sub={`made ${formatDateTimeIST(forecast.made_at)} IST`} />
      </dl>

      {forecast.drivers.length > 0 && (
        <div>
          <h3 className="mb-1 text-[11px] tracking-wide text-ink-faint uppercase">Drivers</h3>
          <ul className="flex flex-col gap-1">
            {forecast.drivers.map((d) => (
              <li key={d.name} className="flex gap-2">
                <DirectionTag direction={d.effect} compact />
                <span>
                  <span className="text-ink">{d.name}</span>
                  {d.detail && <span className="text-ink-muted"> — {d.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {forecast.explanation && (
        <div>
          <h3 className="mb-1 text-[11px] tracking-wide text-ink-faint uppercase">Explanation</h3>
          <p className="text-ink-muted">{forecast.explanation}</p>
        </div>
      )}
    </div>
  );
}
