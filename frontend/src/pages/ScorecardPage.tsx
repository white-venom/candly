import clsx from "clsx";
import { useMemo } from "react";
import { useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useHealth, useInstruments, useScorecard } from "../api/hooks";
import type { ScorecardResponse, ScorecardRow } from "../api/types";
import { InstrumentOptions } from "../components/InstrumentOptions";
import { Page } from "../components/shell/Page";
import { Button } from "../components/ui/Button";
import { CertifiedChip, DirectionGlyph } from "../components/ui/Chip";
import { Segmented, SelectField, Switch } from "../components/ui/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { Dash, TableFrame, Td, Th } from "../components/ui/Table";
import { CiBar } from "../components/viz/CiBar";
import { fmtInt, fmtPct, fmtProb, fmtPts, fmtPValue, humanize, signTone } from "../lib/format";
import { dataStatus } from "../lib/messages";
import { readLastSelection } from "../lib/routes";
import { useShell } from "../lib/shell";
import { formatIsoDate, formatWhenIST } from "../lib/time";
import { TIMEFRAMES } from "../lib/timeframes";

function ciDomain(rows: ScorecardRow[]): [number, number] {
  if (rows.length === 0) return [0, 1];
  const lo = Math.min(...rows.flatMap((r) => [r.ci_low, r.base_rate]));
  const hi = Math.max(...rows.flatMap((r) => [r.ci_high, r.base_rate]));
  return [Math.max(0, lo - 0.02), Math.min(1, hi + 0.02)];
}

/** "all" → "Any", "trend=down" → "Trend: down", "vol_regime=high" → "Vol regime: high". */
function contextLabel(context: string): string {
  if (context === "all") return "Any";
  const text = humanize(context).replace("=", ": ");
  return text[0].toUpperCase() + text.slice(1);
}

function MetaLine({ meta, shown }: { meta: ScorecardResponse["meta"]; shown: number }) {
  const items = [
    `Trained to ${formatIsoDate(meta.train_end)}`,
    `holdout from ${formatIsoDate(meta.holdout_start)} (locked, never tuned on)`,
    `${fmtInt(meta.n_tests)} tests, FDR ${fmtProb(meta.fdr_alpha, 0)}`,
    `horizons ${meta.horizons.join(", ")} bars`,
    meta.built_at ? `built ${formatWhenIST(meta.built_at)} IST` : null,
    `${fmtInt(shown)} rows`,
  ].filter(Boolean);
  return <p className="text-xs text-ink-faint">{items.join(" · ")}</p>;
}

function ScorecardTable({ rows }: { rows: ScorecardRow[] }) {
  const domain = ciDomain(rows);
  return (
    <TableFrame label="Scorecard">
      <thead>
        <tr>
          <Th>Pattern</Th>
          <Th>Instrument</Th>
          <Th>Context</Th>
          <Th numeric title="Horizon in bars">
            h
          </Th>
          <Th numeric>n</Th>
          <Th numeric>Hit</Th>
          <Th numeric>Base</Th>
          <Th numeric>Edge</Th>
          <Th title="95% interval of the hit rate (bar), the hit rate (dot) and the base rate (tick)">95% CI</Th>
          <Th numeric title="Benjamini–Hochberg q-value">q</Th>
          <Th numeric>After costs</Th>
          <Th numeric>Validation</Th>
          <Th />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const edge = r.hit_rate - r.base_rate;
          return (
            <tr key={`${r.pattern}|${r.instrument}|${r.context}|${r.horizon_bars}`} className="transition-colors hover:bg-raised/60">
              <Td>
                <span className="inline-flex items-center gap-1.5">
                  <DirectionGlyph direction={r.direction} />
                  <span className="font-medium text-ink">{r.label}</span>
                </span>
              </Td>
              <Td className="text-ink-muted">{r.instrument === "ALL" ? "All (pooled)" : r.instrument.split(":").pop()}</Td>
              <Td className="text-ink-muted">{contextLabel(r.context)}</Td>
              <Td numeric className="text-ink-muted">
                {r.horizon_bars}
              </Td>
              <Td numeric>{fmtInt(r.n)}</Td>
              <Td numeric className="text-ink">
                {fmtProb(r.hit_rate)}
              </Td>
              <Td numeric className="text-ink-muted">
                {fmtProb(r.base_rate)}
              </Td>
              <Td numeric className={clsx("font-medium", signTone(edge))}>
                {fmtPts(edge)}
              </Td>
              <Td>
                <span className="inline-flex items-center gap-2">
                  <CiBar low={r.ci_low} high={r.ci_high} point={r.hit_rate} base={r.base_rate} domain={domain} />
                  <span className="text-2xs text-ink-faint">
                    {fmtProb(r.ci_low, 0)}–{fmtProb(r.ci_high, 0)}
                  </span>
                </span>
              </Td>
              <Td numeric className="text-ink-muted">
                {fmtPValue(r.q_value)}
              </Td>
              <Td numeric className={signTone(r.expectancy_after_cost_pct)}>
                {fmtPct(r.expectancy_after_cost_pct)}
              </Td>
              <Td numeric className="text-ink-muted">
                {r.validation_hit_rate === null ? <Dash /> : fmtProb(r.validation_hit_rate, 0)}
                <span className="ml-1 text-2xs text-ink-faint">n {fmtInt(r.validation_n)}</span>
              </Td>
              <Td>{r.certified && <CertifiedChip />}</Td>
            </tr>
          );
        })}
      </tbody>
    </TableFrame>
  );
}

function NotBuilt() {
  const health = useHealth();
  const { openConnect } = useShell();
  const canConnect = dataStatus(health.data, health.error).canConnect;
  return (
    <EmptyState
      icon="scorecard"
      title="No scorecard yet"
      hint="It is built after the Fyers backfill: once years of candles are in, the nightly job tests every pattern out-of-sample and fills this table."
      action={
        canConnect && (
          <Button variant="primary" size="sm" icon="plug" onClick={openConnect}>
            Connect Fyers
          </Button>
        )
      }
      className="rounded-lg border border-line bg-surface py-16"
    />
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

  const notBuilt = scorecard.error instanceof ApiError && scorecard.error.noData && !scorecard.data;

  return (
    <Page
      title="Scorecard"
      description="How often each pattern worked, out-of-sample, vs the base rate"
      actions={<Segmented label="Timeframe" value={tf} onChange={(t) => update("tf", t)} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />}
      toolbar={
        <>
          <SelectField label="Instrument" value={instrument ?? ""} onChange={(e) => update("instrument", e.target.value)}>
            <option value="">All rows</option>
            <option value="ALL">All (pooled)</option>
            <InstrumentOptions instruments={instruments.data ?? []} />
          </SelectField>
          <SelectField label="Pattern" value={pattern ?? ""} onChange={(e) => update("pattern", e.target.value)} disabled={patternOptions.length === 0}>
            <option value="">All patterns</option>
            {patternOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </SelectField>
          <div className="w-44">
            <Switch label="Certified only" checked={certifiedOnly} onChange={(on) => update("certified", on ? "1" : null)} />
          </div>
        </>
      }
    >
      {scorecard.data ? (
        <div className={clsx("flex flex-col gap-3", scorecard.isPlaceholderData && "opacity-60")}>
          <MetaLine meta={scorecard.data.meta} shown={scorecard.data.rows.length} />
          {scorecard.data.rows.length === 0 ? (
            <EmptyState
              title={certifiedOnly ? "No certified patterns for these filters" : "No rows for these filters"}
              hint="Certification needs n ≥ 30 independent cases, a q-value under the FDR level, a positive expectancy after costs, and a hold-up in validation."
              className="rounded-lg border border-line bg-surface"
            />
          ) : (
            <ScorecardTable rows={scorecard.data.rows} />
          )}
        </div>
      ) : notBuilt ? (
        <NotBuilt />
      ) : scorecard.isError ? (
        <ErrorState error={scorecard.error} />
      ) : (
        <LoadingState label="Loading scorecard…" />
      )}
    </Page>
  );
}
