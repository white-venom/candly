import type { UseQueryResult } from "@tanstack/react-query";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useAccuracy, useInstruments, useLedger } from "../api/hooks";
import type { AccuracyResponse, LedgerEntry } from "../api/types";
import { InstrumentOptions } from "../components/InstrumentOptions";
import { Page } from "../components/shell/Page";
import { Chip, Eyebrow } from "../components/ui/Chip";
import { SelectField } from "../components/ui/Controls";
import { EmptyState, QueryView } from "../components/ui/States";
import { Dash, TableFrame, Td, Th } from "../components/ui/Table";
import { CalibrationChart } from "../components/viz/CalibrationChart";
import { CandleGlyphs } from "../components/viz/CandleGlyphs";
import { MiniLineChart } from "../components/viz/MiniLineChart";
import { fmtInt, fmtNum, fmtPct, fmtProb, humanize, signTone } from "../lib/format";
import { formatWhenIST, nowUnix } from "../lib/time";
import { TIMEFRAMES } from "../lib/timeframes";

const DAY_OPTIONS = [7, 30, 90, 180, 365];

const ALL_METHODS = "all";
const DEFAULT_METHOD = "analog_v1";
const METHODS = [
  { value: "analog_v1", label: "Model (analog_v1)" },
  { value: "baseline_base_rate", label: "Baseline: base rate" },
  { value: "baseline_persistence", label: "Baseline: persistence" },
  { value: "baseline_random_walk", label: "Baseline: random-walk bands" },
  { value: ALL_METHODS, label: "All methods (ledger only)" },
];

const NO_GRADES = { title: "No graded forecasts yet", hint: "They appear after the first sessions with live data." };

function readMethod(value: string | null): string {
  return METHODS.find((m) => m.value === value)?.value ?? DEFAULT_METHOD;
}

function Card({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section aria-label={title} className={clsx("rounded-lg border border-line bg-surface p-4", className)}>
      <Eyebrow className="mb-3">{title}</Eyebrow>
      {children}
    </section>
  );
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <dt className="text-2xs font-semibold tracking-wider text-ink-faint uppercase">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-ink tabular-nums">{value}</dd>
      {sub && <dd className="mt-0.5 text-xs text-ink-faint tabular-nums">{sub}</dd>}
    </div>
  );
}

function Tiles({ s }: { s: AccuracyResponse["summary"] }) {
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Tile label="Hit rate" value={fmtProb(s.direction_hit_rate)} sub="direction, abstains excluded" />
      <Tile
        label="Brier"
        value={fmtNum(s.brier, 4)}
        sub={
          <>
            base {fmtNum(s.brier_baseline, 4)} · skill{" "}
            <span className={signTone(s.skill)}>
              {s.skill !== null && s.skill > 0 ? "+" : ""}
              {fmtNum(s.skill, 3)}
            </span>
          </>
        }
      />
      <Tile label="Calibration error" value={fmtNum(s.ece, 3)} sub="ECE, lower is better" />
      <Tile label="Band coverage" value={fmtProb(s.band_coverage_80)} sub="closes inside 10–90%, target ≈ 80%" />
      <Tile
        label="Match score"
        value={s.mean_match_score === null ? "—" : `${fmtNum(s.mean_match_score, 1)} / 100`}
        sub={`close error ${fmtNum(s.mean_close_err_atr, 2)} ATR`}
      />
      <Tile label="Graded" value={fmtInt(s.n_graded)} sub={`${fmtInt(s.n_abstained)} abstained · ${fmtInt(s.n_forecasts)} forecasts`} />
    </dl>
  );
}

function ByGroup({ rows }: { rows: AccuracyResponse["by_group"] }) {
  if (rows.length === 0) return <p className="text-xs text-ink-faint">No breakdown yet.</p>;
  const groups = [...new Set(rows.map((r) => r.group_by))];
  return (
    <table className="w-full text-sm tabular-nums">
      <thead className="text-left text-2xs font-semibold tracking-wider text-ink-faint uppercase">
        <tr>
          <th scope="col" className="py-1.5 font-semibold">
            Group
          </th>
          <th scope="col" className="py-1.5 text-right font-semibold">
            n
          </th>
          <th scope="col" className="py-1.5 text-right font-semibold">
            Hit rate
          </th>
          <th scope="col" className="py-1.5 text-right font-semibold">
            Brier
          </th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g}>
          <tr>
            <th scope="rowgroup" colSpan={4} className="pt-3 pb-1 text-left text-xs font-medium text-ink-muted">
              By {humanize(g)}
            </th>
          </tr>
          {rows
            .filter((r) => r.group_by === g)
            .map((r) => (
              <tr key={`${g}|${r.key}`} className="border-t border-line/60">
                <td className="py-1.5 text-ink">{humanize(r.key)}</td>
                <td className="py-1.5 text-right text-ink-muted">{fmtInt(r.n)}</td>
                <td className="py-1.5 text-right">{fmtProb(r.hit_rate)}</td>
                <td className="py-1.5 text-right text-ink-muted">{fmtNum(r.brier, 4)}</td>
              </tr>
            ))}
        </tbody>
      ))}
    </table>
  );
}

function LedgerTable({ entries }: { entries: LedgerEntry[] }) {
  return (
    <TableFrame label="Ledger">
      <thead>
        <tr>
          <Th>Made (IST)</Th>
          <Th>Instrument</Th>
          <Th>Method</Th>
          <Th numeric>p(up)</Th>
          <Th>Status</Th>
          <Th title="Per step: predicted (translucent) beside actual (solid)">Predicted | actual</Th>
          <Th title="Close error per step, in % and ATR">Close error</Th>
          <Th numeric>Match</Th>
          <Th>Direction</Th>
          <Th numeric>Brier</Th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e) => (
          <tr key={e.id} className="transition-colors hover:bg-raised/60">
            <Td className="text-ink-muted">{formatWhenIST(e.made_at)}</Td>
            <Td>
              <span className="font-medium text-ink">{e.instrument.split(":").pop()}</span> <span className="text-2xs text-ink-faint">{e.tf}</span>
            </Td>
            <Td className="text-xs text-ink-muted">{e.method}</Td>
            <Td numeric className={e.abstain ? "text-ink-faint" : "text-ink"}>
              {fmtProb(e.p_up)}
              {e.abstain && <span className="ml-1.5 text-2xs">abstained</span>}
            </Td>
            <Td>
              <Chip tone={e.status === "graded" ? "accent" : "neutral"}>{e.status === "graded" ? "Graded" : e.status === "void" ? "Void" : "Pending"}</Chip>
            </Td>
            <Td className="py-1">
              <CandleGlyphs predicted={e.predicted} actual={e.actual} />
            </Td>
            <Td className="text-xs text-ink-muted">
              {e.grade ? (
                e.grade.steps.map((s) => (
                  <span key={s.step} className="mr-2 inline-block" title={`Step ${s.step}: range overlap ${fmtNum(s.range_iou, 2)}, ${s.in_band_80 ? "inside" : "outside"} the 80% band`}>
                    <span className={signTone(s.close_err_pct)}>{fmtPct(s.close_err_pct)}</span> <span className="text-ink-faint">{fmtNum(s.close_err_atr, 2)} ATR</span>
                  </span>
                ))
              ) : (
                <Dash />
              )}
            </Td>
            <Td numeric>{e.grade ? fmtNum(e.grade.match_score, 0) : <Dash />}</Td>
            <Td>
              {e.grade?.direction_hit === true && <span className="text-up">✓ Hit</span>}
              {e.grade?.direction_hit === false && <span className="text-down">✗ Miss</span>}
              {(e.grade === null || e.grade.direction_hit === null) && <Dash />}
            </Td>
            <Td numeric className="text-ink-muted">
              {fmtNum(e.grade?.brier, 4)}
            </Td>
          </tr>
        ))}
      </tbody>
    </TableFrame>
  );
}

function Summary({ query }: { query: UseQueryResult<AccuracyResponse> }) {
  return (
    <QueryView query={query} isEmpty={(a) => a.summary.n_forecasts === 0} empty={NO_GRADES} loadingLabel="Loading accuracy…" className="rounded-lg border border-line bg-surface py-12">
      {(a) => (
        <div className={clsx("flex flex-col gap-4", query.isPlaceholderData && "opacity-60")}>
          <Tiles s={a.summary} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Calibration">
              <CalibrationChart bins={a.calibration} />
            </Card>
            <Card title="Rolling, last 30 graded">
              <div className="grid gap-3">
                <MiniLineChart
                  title="Hit rate"
                  points={a.rolling.map((r) => ({ time: r.time, value: r.hit_rate }))}
                  format={(v) => fmtProb(v, 0)}
                  reference={{ value: 0.5, label: "50%" }}
                />
                <MiniLineChart
                  title="Brier score (lower is better)"
                  points={a.rolling.map((r) => ({ time: r.time, value: r.brier }))}
                  format={(v) => fmtNum(v, 3)}
                  reference={a.summary.brier_baseline !== null ? { value: a.summary.brier_baseline, label: "baseline" } : undefined}
                />
                <MiniLineChart title="Match score" points={a.rolling.map((r) => ({ time: r.time, value: r.match_score }))} format={(v) => fmtNum(v, 0)} />
              </div>
            </Card>
          </div>
          <Card title="By group">
            <ByGroup rows={a.by_group} />
          </Card>
        </div>
      )}
    </QueryView>
  );
}

function Ledger({ instrument, tf, method, days }: { instrument: string | null; tf: string | null; method: string | null; days: number }) {
  const ledger = useLedger({ instrument, tf, method, limit: 100 });
  const since = nowUnix() - days * 86400;
  return (
    <section aria-label="Ledger" className="flex flex-col gap-2">
      <div className="flex items-baseline gap-3">
        <Eyebrow>Ledger</Eyebrow>
        <p className="text-2xs text-ink-faint">Every forecast is written before its outcome exists, then graded against the real candles.</p>
      </div>
      <QueryView
        query={ledger}
        isEmpty={(rows) => rows.length === 0}
        empty={{ title: "The ledger is empty", hint: "Forecasts are recorded at each bar close once live data flows." }}
        className="rounded-lg border border-line bg-surface"
      >
        {(rows) => {
          const recent = rows.filter((r) => r.made_at >= since);
          return recent.length === 0 ? (
            <EmptyState title={`No forecasts in the last ${days} days`} className="rounded-lg border border-line bg-surface" />
          ) : (
            <LedgerTable entries={recent} />
          );
        }}
      </QueryView>
    </section>
  );
}

export function AccuracyPage() {
  const [params, setParams] = useSearchParams();
  const instrument = params.get("instrument") || null;
  const tf = params.get("tf") || null;
  const days = Number(params.get("days")) || 90;
  const method = readMethod(params.get("method"));
  const oneMethod = method === ALL_METHODS ? null : method;
  const instruments = useInstruments();
  const accuracy = useAccuracy(instrument, tf, days, oneMethod);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <Page
      title="Accuracy"
      description="Forecasts graded against the candles that followed"
      actions={
        <SelectField label="Method" value={method} onChange={(e) => update("method", e.target.value === DEFAULT_METHOD ? "" : e.target.value)}>
          {METHODS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </SelectField>
      }
      toolbar={
        <>
          <SelectField label="Instrument" value={instrument ?? ""} onChange={(e) => update("instrument", e.target.value)}>
            <option value="">All instruments</option>
            <InstrumentOptions instruments={instruments.data ?? []} />
          </SelectField>
          <SelectField label="Timeframe" value={tf ?? ""} onChange={(e) => update("tf", e.target.value)}>
            <option value="">All timeframes</option>
            {TIMEFRAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </SelectField>
          <SelectField label="Window" value={String(days)} onChange={(e) => update("days", e.target.value)}>
            {DAY_OPTIONS.map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </SelectField>
        </>
      }
    >
      {oneMethod === null ? (
        <EmptyState
          title="Accuracy is scored one method at a time"
          hint="So the baselines never blur the model’s numbers. Pick a method to see its summary; the ledger below lists every method."
          className="rounded-lg border border-line bg-surface"
        />
      ) : (
        <Summary query={accuracy} />
      )}
      <Ledger instrument={instrument} tf={tf} method={oneMethod} days={days} />
    </Page>
  );
}
