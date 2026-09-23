import clsx from "clsx";
import { useMemo } from "react";
import { useSearchParams } from "react-router";
import { useInstruments, useScorecard } from "../api/hooks";
import type { ScorecardResponse, ScorecardRow } from "../api/types";
import { CiBar } from "../components/CiBar";
import { InstrumentOptions } from "../components/InstrumentSelect";
import { QueryView } from "../components/States";
import { Badge, CertifiedBadge, DirectionTag, SelectField, Stat } from "../components/ui";
import { fmtInt, fmtNum, fmtPct, fmtProb, fmtPts, fmtPValue, humanize, signTone } from "../lib/format";
import { readLastSelection } from "../lib/routes";
import { formatDateTimeIST } from "../lib/time";
import { TIMEFRAMES } from "../lib/timeframes";

function ciDomain(rows: ScorecardRow[]): [number, number] {
  if (rows.length === 0) return [0, 1];
  const lo = Math.min(...rows.flatMap((r) => [r.ci_low, r.base_rate]));
  const hi = Math.max(...rows.flatMap((r) => [r.ci_high, r.base_rate]));
  return [Math.max(0, lo - 0.02), Math.min(1, hi + 0.02)];
}

function MetaStrip({ meta, shown }: { meta: ScorecardResponse["meta"]; shown: number }) {
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg border border-line bg-surface px-3 py-2">
      <Stat label="Train end" value={meta.train_end} />
      <Stat
        label="Holdout from"
        value={
          <span className="inline-flex items-center gap-1.5">
            {meta.holdout_start}
            <Badge tone="forming" title="Data from here on is locked and never used for tuning">
              🔒 locked
            </Badge>
          </span>
        }
      />
      <Stat label="Tests run" value={fmtInt(meta.n_tests)} sub="all hypotheses, for FDR" />
      <Stat label="FDR α" value={fmtNum(meta.fdr_alpha, 2)} sub="Benjamini–Hochberg" />
      <Stat label="Horizons" value={meta.horizons.map((h) => `${h}`).join(", ") + " bars"} />
      <Stat label="Built" value={meta.built_at ? `${formatDateTimeIST(meta.built_at)} IST` : "—"} />
      <Stat label="Rows shown" value={fmtInt(shown)} />
    </dl>
  );
}

const HEADERS = [
  "Pattern",
  "Instrument",
  "Context",
  "h",
  "n",
  "Hit rate",
  "Base",
  "Hit − base",
  "95% CI",
  "p",
  "q",
  "Posterior",
  "Exp. after costs",
  "Validation",
  "",
];

function ScorecardTable({ rows }: { rows: ScorecardRow[] }) {
  const domain = ciDomain(rows);
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full text-sm tabular-nums">
        <thead className="border-b border-line-strong text-left text-xs text-ink-muted">
          <tr>
            {HEADERS.map((h, i) => (
              <th key={i} scope="col" className="px-2 py-2 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const edge = r.hit_rate - r.base_rate;
            return (
              <tr key={`${r.pattern}|${r.instrument}|${r.context}|${r.horizon_bars}`} className="border-b border-line">
                <td className="px-2 py-1.5">
                  <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                    <DirectionTag direction={r.direction} compact />
                    <span className="text-ink">{r.label}</span>
                  </span>
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap text-ink-muted">{r.instrument === "ALL" ? "ALL (pooled)" : r.instrument}</td>
                <td className="px-2 py-1.5 text-ink-muted">{humanize(r.context)}</td>
                <td className="px-2 py-1.5">{r.horizon_bars}</td>
                <td className="px-2 py-1.5">{fmtInt(r.n)}</td>
                <td className="px-2 py-1.5">{fmtProb(r.hit_rate)}</td>
                <td className="px-2 py-1.5 text-ink-muted">{fmtProb(r.base_rate)}</td>
                <td className={clsx("px-2 py-1.5 font-medium whitespace-nowrap", signTone(edge))}>{fmtPts(edge)}</td>
                <td className="px-2 py-1.5">
                  <div className="flex items-center gap-2">
                    <CiBar low={r.ci_low} high={r.ci_high} point={r.hit_rate} base={r.base_rate} domain={domain} />
                    <span className="text-xs whitespace-nowrap text-ink-faint">
                      {fmtProb(r.ci_low)}–{fmtProb(r.ci_high)}
                    </span>
                  </div>
                </td>
                <td className="px-2 py-1.5 text-ink-muted">{fmtPValue(r.p_value)}</td>
                <td className="px-2 py-1.5">{fmtPValue(r.q_value)}</td>
                <td className="px-2 py-1.5">{fmtProb(r.posterior)}</td>
                <td className={clsx("px-2 py-1.5", signTone(r.expectancy_after_cost_pct))}>{fmtPct(r.expectancy_after_cost_pct)}</td>
                <td className="px-2 py-1.5 whitespace-nowrap text-ink-muted">
                  {fmtProb(r.validation_hit_rate)} <span className="text-ink-faint">n={fmtInt(r.validation_n)}</span>
                </td>
                <td className="px-2 py-1.5">{r.certified && <CertifiedBadge />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ScorecardPage() {
  const [params, setParams] = useSearchParams();
  const tf = params.get("tf") ?? readLastSelection()?.tf ?? "1D";
  const instrument = params.get("instrument") || null;
  const pattern = params.get("pattern") || null;
  const certifiedOnly = params.get("certified") === "1";

  const instruments = useInstruments();
  const scorecard = useScorecard({ tf, instrument, pattern, certifiedOnly });
  const allPatterns = useScorecard({ tf, instrument, pattern: null, certifiedOnly: false });
  const patternOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const r of allPatterns.data?.rows ?? []) seen.set(r.pattern, r.label);
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [allPatterns.data]);

  const update = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <div className="flex flex-col gap-3">
      <h1 className="text-lg font-semibold text-ink">Scorecard</h1>
      <div role="group" aria-label="Filters" className="flex flex-wrap items-end gap-3">
        <SelectField label="Timeframe" value={tf} onChange={(e) => update("tf", e.target.value)}>
          {TIMEFRAMES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </SelectField>
        <SelectField label="Instrument" value={instrument ?? ""} onChange={(e) => update("instrument", e.target.value)}>
          <option value="">All rows</option>
          <option value="ALL">ALL (pooled)</option>
          <InstrumentOptions instruments={instruments.data ?? []} />
        </SelectField>
        <SelectField label="Pattern" value={pattern ?? ""} onChange={(e) => update("pattern", e.target.value)}>
          <option value="">All patterns</option>
          {patternOptions.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </SelectField>
        <label className="flex items-center gap-2 pb-1.5 text-sm text-ink">
          <input
            type="checkbox"
            checked={certifiedOnly}
            onChange={(e) => update("certified", e.target.checked ? "1" : null)}
            className="accent-accent"
          />
          Certified only
        </label>
      </div>
      <QueryView query={scorecard} loadingLabel="Loading scorecard…">
        {(data) => (
          <>
            <MetaStrip meta={data.meta} shown={data.rows.length} />
            {data.rows.length === 0 ? (
              <p role="status" className="rounded-md border border-dashed border-line-strong p-4 text-ink-muted">
                {certifiedOnly ? "No certified buckets for these filters." : "No scorecard rows for these filters."}
              </p>
            ) : (
              <ScorecardTable rows={data.rows} />
            )}
          </>
        )}
      </QueryView>
    </div>
  );
}
