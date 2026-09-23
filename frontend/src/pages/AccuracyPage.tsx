import clsx from "clsx";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useAccuracy, useInstruments, useLedger } from "../api/hooks";
import type { AccuracyResponse, LedgerEntry, StepGrade } from "../api/types";
import { CalibrationChart } from "../components/CalibrationChart";
import { CandleGlyphs } from "../components/CandleGlyphs";
import { InstrumentOptions } from "../components/InstrumentSelect";
import { MiniLineChart } from "../components/MiniLineChart";
import { EmptyState, QueryView } from "../components/States";
import { Badge, Card, SelectField } from "../components/ui";
import { fmtInt, fmtNum, fmtPct, fmtProb, humanize, signTone } from "../lib/format";
import { formatDateTimeIST, nowUnix } from "../lib/time";
import { TIMEFRAMES } from "../lib/timeframes";

const DAY_OPTIONS = [7, 30, 90, 180, 365];

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <dt className="text-[11px] tracking-wide text-ink-faint uppercase">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-ink">{value}</dd>
      {sub && <dd className="mt-0.5 text-xs text-ink-muted">{sub}</dd>}
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
            baseline {fmtNum(s.brier_baseline, 4)} · skill{" "}
            <span className={signTone(s.skill)}>
              {s.skill !== null && s.skill > 0 ? "+" : ""}
              {fmtNum(s.skill, 3)}
            </span>
          </>
        }
      />
      <Tile label="ECE" value={fmtNum(s.ece, 3)} sub="calibration error; lower is better" />
      <Tile label="80% band coverage" value={fmtProb(s.band_coverage_80)} sub="target ≈ 80%" />
      <Tile
        label="Mean match score"
        value={s.mean_match_score === null ? "—" : `${fmtNum(s.mean_match_score, 1)} / 100`}
        sub={`close error ${fmtNum(s.mean_close_err_atr, 2)} ATR`}
      />
      <Tile label="Graded" value={fmtInt(s.n_graded)} sub={`${fmtInt(s.n_abstained)} abstained · ${fmtInt(s.n_forecasts)} forecasts`} />
    </dl>
  );
}

function ByGroup({ rows }: { rows: AccuracyResponse["by_group"] }) {
  if (rows.length === 0) return <p className="text-ink-muted">No breakdown yet.</p>;
  const groups = [...new Set(rows.map((r) => r.group_by))];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums">
        <thead className="border-b border-line-strong text-left text-xs text-ink-muted">
          <tr>
            <th scope="col" className="px-2 py-1.5 font-medium">Group</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">n</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Hit rate</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Brier</th>
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody key={g}>
            <tr>
              <th scope="rowgroup" colSpan={4} className="px-2 pt-3 pb-1 text-left text-[11px] font-medium tracking-wide text-ink-faint uppercase">
                by {humanize(g)}
              </th>
            </tr>
            {rows
              .filter((r) => r.group_by === g)
              .map((r) => (
                <tr key={`${g}|${r.key}`} className="border-b border-line">
                  <td className="px-2 py-1 text-ink">{humanize(r.key)}</td>
                  <td className="px-2 py-1 text-right">{fmtInt(r.n)}</td>
                  <td className="px-2 py-1 text-right">{fmtProb(r.hit_rate)}</td>
                  <td className="px-2 py-1 text-right">{fmtNum(r.brier, 4)}</td>
                </tr>
              ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function StepErrors({ steps }: { steps: StepGrade[] }) {
  return (
    <ul className="flex flex-col gap-0.5 text-xs whitespace-nowrap text-ink-muted">
      {steps.map((s) => (
        <li key={s.step}>
          <span className="text-ink">#{s.step}</span> close {fmtPct(s.close_err_pct)} ({fmtNum(s.close_err_atr, 2)} ATR) · H/L{" "}
          {fmtNum(s.high_err_atr, 2)}/{fmtNum(s.low_err_atr, 2)} ATR · IoU {fmtNum(s.range_iou, 2)}/{fmtNum(s.body_iou, 2)} ·{" "}
          <span className={s.color_match ? "text-up" : "text-down"}>{s.color_match ? "colour ✓" : "colour ✗"}</span> ·{" "}
          <span className={s.in_band_80 ? "text-up" : "text-down"}>{s.in_band_80 ? "in band" : "out of band"}</span>
        </li>
      ))}
    </ul>
  );
}

function LedgerTable({ entries }: { entries: LedgerEntry[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums">
        <thead className="border-b border-line-strong text-left text-xs text-ink-muted">
          <tr>
            {["Made (IST)", "Instrument", "p(up)", "Status", "Predicted | actual", "Per-step errors", "Match", "Direction", "Brier"].map((h) => (
              <th key={h} scope="col" className="px-2 py-1.5 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id} className="border-b border-line align-top">
              <td className="px-2 py-1.5 whitespace-nowrap text-ink-muted">{formatDateTimeIST(e.made_at)}</td>
              <td className="px-2 py-1.5 whitespace-nowrap">
                <span className="text-ink">{e.instrument}</span> <span className="text-ink-faint">{e.tf}</span>
                <div className="text-xs text-ink-faint">{e.method}</div>
              </td>
              <td className="px-2 py-1.5 whitespace-nowrap">
                {e.abstain ? (
                  <span className="text-ink-faint">
                    {fmtProb(e.p_up)} <Badge>abstain</Badge>
                  </span>
                ) : (
                  <span className="text-ink">{fmtProb(e.p_up)}</span>
                )}
              </td>
              <td className="px-2 py-1.5">
                <Badge tone={e.status === "graded" ? "accent" : e.status === "void" ? "danger" : "neutral"}>{e.status}</Badge>
              </td>
              <td className="px-2 py-1.5">
                <CandleGlyphs predicted={e.predicted} actual={e.actual} />
              </td>
              <td className="px-2 py-1.5">{e.grade ? <StepErrors steps={e.grade.steps} /> : <span className="text-ink-faint">—</span>}</td>
              <td className="px-2 py-1.5">{e.grade ? fmtNum(e.grade.match_score, 0) : "—"}</td>
              <td className="px-2 py-1.5">
                {e.grade?.direction_hit === true && <span className="text-up">✓ hit</span>}
                {e.grade?.direction_hit === false && <span className="text-down">✗ miss</span>}
                {(e.grade === null || e.grade.direction_hit === null) && <span className="text-ink-faint">—</span>}
              </td>
              <td className="px-2 py-1.5">{fmtNum(e.grade?.brier, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Ledger({ instrument, tf, days }: { instrument: string | null; tf: string | null; days: number }) {
  const ledger = useLedger({ instrument, tf, limit: 100 });
  const since = nowUnix() - days * 86400;
  return (
    <QueryView query={ledger} isEmpty={(rows) => rows.length === 0} empty="No forecasts in the ledger yet.">
      {(rows) => {
        const recent = rows.filter((r) => r.made_at >= since);
        return recent.length === 0 ? (
          <EmptyState>No ledger entries in the last {days} days.</EmptyState>
        ) : (
          <LedgerTable entries={recent} />
        );
      }}
    </QueryView>
  );
}

export function AccuracyPage() {
  const [params, setParams] = useSearchParams();
  const instrument = params.get("instrument") || null;
  const tf = params.get("tf") || null;
  const days = Number(params.get("days")) || 90;
  const instruments = useInstruments();
  const accuracy = useAccuracy(instrument, tf, days);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <div className="flex flex-col gap-3">
      <h1 className="text-lg font-semibold text-ink">Accuracy — expected vs actual</h1>
      <div role="group" aria-label="Filters" className="flex flex-wrap items-end gap-3">
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
      </div>

      <QueryView
        query={accuracy}
        isEmpty={(a) => a.summary.n_forecasts === 0}
        empty="No forecasts in this window yet — the ledger fills as bars close."
        loadingLabel="Loading accuracy…"
      >
        {(a) => (
          <div className={clsx("flex flex-col gap-3", accuracy.isPlaceholderData && "opacity-60")}>
            <Tiles s={a.summary} />
            <div className="grid gap-3 lg:grid-cols-2">
              <Card title="Calibration">
                <CalibrationChart bins={a.calibration} />
              </Card>
              <Card title="Rolling (last 30 graded forecasts)">
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
                    reference={a.summary.brier_baseline !== null ? { value: a.summary.brier_baseline, label: "window baseline" } : undefined}
                  />
                  <MiniLineChart
                    title="Match score"
                    points={a.rolling.map((r) => ({ time: r.time, value: r.match_score }))}
                    format={(v) => fmtNum(v, 0)}
                  />
                </div>
              </Card>
            </div>
            <Card title="By group">
              <ByGroup rows={a.by_group} />
            </Card>
          </div>
        )}
      </QueryView>

      <Card title="Ledger — predicted vs actual candles">
        <Ledger instrument={instrument} tf={tf} days={days} />
      </Card>
    </div>
  );
}
