import { useState } from "react";
import type { PatternSignal, ScoreStats } from "../api/types";
import { fmtInt, fmtNum, fmtPct, fmtPrice, fmtProb, fmtPts, fmtPValue, humanize, signTone } from "../lib/format";
import type { SignalFilters } from "../lib/signalFilters";
import { formatBarTimeIST } from "../lib/time";
import { Badge, CertifiedBadge, DirectionTag, FormingBadge, Stat, type Tone } from "./ui";

function StatsGrid({ stats }: { stats: ScoreStats }) {
  const edge = stats.hit_rate - stats.base_rate;
  return (
    <dl className="mt-1.5 grid grid-cols-3 gap-x-3 gap-y-1 text-xs">
      <Stat label="n" value={fmtInt(stats.n)} sub={`${stats.horizon_bars}-bar horizon`} />
      <Stat
        label="Hit vs base"
        value={
          <>
            {fmtProb(stats.hit_rate)} <span className="text-ink-faint">vs {fmtProb(stats.base_rate)}</span>
          </>
        }
        sub={<span className={signTone(edge)}>{fmtPts(edge)}</span>}
      />
      <Stat label="CI" value={`${fmtProb(stats.ci_low)}–${fmtProb(stats.ci_high)}`} />
      <Stat label="q-value" value={fmtPValue(stats.q_value)} />
      <Stat label="Posterior" value={fmtProb(stats.posterior)} />
      <Stat label="Exp. after costs" value={<span className={signTone(stats.expectancy_after_cost_pct)}>{fmtPct(stats.expectancy_after_cost_pct)}</span>} />
    </dl>
  );
}

function expiryTag(context: PatternSignal["context"]): { text: string; tone: Tone } | null {
  if (context.expiry_day) return { text: "expiry day", tone: "warn" };
  const days = context.days_to_expiry;
  if (days === null || days === undefined) return null;
  return { text: `expiry in ${days}d`, tone: "neutral" };
}

function ContextTags({ context }: { context: PatternSignal["context"] }) {
  const plain = [
    context.trend && `trend ${context.trend}`,
    context.vol_regime && `vol ${context.vol_regime}`,
    context.session_phase && humanize(context.session_phase),
    context.rel_volume !== null && `rel vol ×${fmtNum(context.rel_volume, 1)}`,
    context.near_level && `near ${humanize(context.near_level)}`,
    context.rsi14 !== null && `RSI ${fmtNum(context.rsi14, 0)}`,
  ].filter((t): t is string => Boolean(t));
  const expiry = expiryTag(context);
  const tags = [...(expiry ? [expiry] : []), ...plain.map((text) => ({ text, tone: "neutral" as const }))];
  if (tags.length === 0) return null;
  return (
    <ul aria-label="Context" className="mt-1.5 flex flex-wrap gap-1">
      {tags.map((t) => (
        <li key={t.text}>
          <Badge tone={t.tone}>{t.text}</Badge>
        </li>
      ))}
    </ul>
  );
}

function SignalItem({ signal, tf }: { signal: PatternSignal; tf: string }) {
  return (
    <li className="border-b border-line py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <DirectionTag direction={signal.direction} compact />
        <span className="font-medium text-ink">{signal.label}</span>
        {signal.state === "forming" && <FormingBadge />}
        {signal.stats?.certified && <CertifiedBadge />}
        <span className="ml-auto text-xs text-ink-faint">{formatBarTimeIST(signal.time, tf)}</span>
      </div>
      {signal.stats ? (
        <StatsGrid stats={signal.stats} />
      ) : (
        <p className="mt-1 text-xs text-ink-faint">No scorecard stats for this setup yet.</p>
      )}
      <ContextTags context={signal.context} />
      {signal.invalidation !== null && (
        <p className="mt-1 text-xs text-ink-muted">Invalidation {fmtPrice(signal.invalidation)}</p>
      )}
    </li>
  );
}

export function SignalFilterToggles({ filters, onChange }: { filters: SignalFilters; onChange: (next: SignalFilters) => void }) {
  return (
    <fieldset className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
      <legend className="sr-only">Signal filters, for this list and the chart markers</legend>
      <label className="inline-flex cursor-pointer items-center gap-1.5 hover:text-ink">
        <input
          type="checkbox"
          checked={filters.showNeutral}
          onChange={(e) => onChange({ ...filters, showNeutral: e.target.checked })}
          className="accent-accent"
        />
        Show neutral patterns
      </label>
      <label className="inline-flex cursor-pointer items-center gap-1.5 hover:text-ink">
        <input
          type="checkbox"
          checked={filters.certifiedOnly}
          onChange={(e) => onChange({ ...filters, certifiedOnly: e.target.checked })}
          className="accent-accent"
        />
        Certified only
      </label>
      <span className="text-ink-faint">Also filters the chart markers.</span>
    </fieldset>
  );
}

const INITIAL = 8;

export function SignalList({ signals, tf }: { signals: PatternSignal[]; tf: string }) {
  const [showAll, setShowAll] = useState(false);
  const sorted = [...signals].sort((a, b) => b.time - a.time);
  const shown = showAll ? sorted : sorted.slice(0, INITIAL);
  return (
    <div>
      <ul>
        {shown.map((s) => (
          <SignalItem key={s.id} signal={s} tf={tf} />
        ))}
      </ul>
      {sorted.length > INITIAL && (
        <button type="button" onClick={() => setShowAll((v) => !v)} className="mt-1 text-xs text-accent hover:underline">
          {showAll ? "Show fewer" : `Show all ${sorted.length}`}
        </button>
      )}
    </div>
  );
}
