import clsx from "clsx";
import type { Forecast } from "../../api/types";
import { fmtProb } from "../../lib/format";
import { abstainReason } from "../../lib/messages";
import { forecastVerdict, type Verdict } from "../../lib/verdict";
import { Chip } from "../ui/Chip";

const LOOK: Record<Verdict, { title: string; glyph: string; text: string; ring: string; fill: string }> = {
  bullish: { title: "Bullish setup", glyph: "▲", text: "text-up", ring: "bg-up/15 text-up", fill: "bg-up" },
  bearish: { title: "Bearish setup", glyph: "▼", text: "text-down", ring: "bg-down/15 text-down", fill: "bg-down" },
  none: { title: "No clear edge", glyph: "–", text: "text-ink", ring: "bg-raised text-ink-muted", fill: "bg-abstain" },
};

const pct = (p: number) => `${Math.min(100, Math.max(0, p * 100))}%`;

/** p(up) as a fill on a 0–100% track, the interval behind it, and a tick at the base rate. */
function ProbabilityBar({ p, base, ci, fill }: { p: number | null; base: number | null; ci: [number, number] | null; fill: string }) {
  return (
    <div className="relative h-2 rounded-full bg-line" aria-hidden="true">
      {ci && <div className={clsx("absolute inset-y-0 rounded-full opacity-30", fill)} style={{ left: pct(ci[0]), width: `calc(${pct(ci[1])} - ${pct(ci[0])})` }} />}
      {p !== null && <div className={clsx("absolute inset-y-0 left-0 rounded-full", fill)} style={{ width: pct(p) }} />}
      {base !== null && <div className="absolute -top-1 -bottom-1 w-0.5 -translate-x-1/2 rounded-full bg-ink" style={{ left: pct(base) }} />}
    </div>
  );
}

const CONFIDENCE = { low: "Low", medium: "Medium", high: "High" } as const;

/** The call at a glance: bullish, bearish or no clear edge, with the odds against the usual. */
export function VerdictCard({ forecast: f, tf, canConnect, now }: { forecast: Forecast; tf: string; canConnect: boolean; now?: number }) {
  const verdict = forecastVerdict(f);
  const look = LOOK[verdict];
  const reason = verdict === "none" ? abstainReason(f.abstain_reason, { tf, canConnect, now }) : null;

  return (
    <section aria-label="Verdict" className="rounded-lg border border-line bg-page/40 p-4">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className={clsx("flex size-9 shrink-0 items-center justify-center rounded-full text-base", look.ring)}>
          {look.glyph}
        </span>
        <div className="min-w-0 flex-1">
          <p className={clsx("text-lg leading-6 font-semibold whitespace-nowrap", look.text)}>{look.title}</p>
          <p className="mt-0.5 flex items-center gap-2 text-xs text-ink-faint">
            Next {f.horizon_bars} bars · {tf}
            {verdict !== "none" && f.confidence && (
              <Chip tone={f.confidence === "high" ? "accent" : "neutral"} title="How far the interval sits from the base rate">
                {CONFIDENCE[f.confidence]} confidence
              </Chip>
            )}
          </p>
        </div>
      </div>

      {verdict !== "none" ? (
        <div className="mt-4">
          <div className="mb-2 flex items-baseline justify-between text-xs text-ink-muted tabular-nums">
            <span>
              <span className={clsx("text-base font-semibold", look.text)}>{fmtProb(f.p_up, 0)}</span> chance it closes higher
            </span>
            <span>usually {fmtProb(f.base_rate, 0)}</span>
          </div>
          <ProbabilityBar p={f.p_up} base={f.base_rate} ci={f.p_up_ci} fill={look.fill} />
          {f.p_up_ci && (
            <p className="mt-2 text-2xs text-ink-faint tabular-nums">
              Likely range {fmtProb(f.p_up_ci[0], 0)}–{fmtProb(f.p_up_ci[1], 0)} · {f.n_analogs} similar past setups
            </p>
          )}
        </div>
      ) : (
        <div role="status" className="mt-3">
          <p className="text-sm text-ink">{reason?.text}</p>
          {reason?.hint && <p className="mt-1 text-xs text-ink-faint">{reason.hint}</p>}
          {f.p_up !== null ? (
            <div className="mt-3">
              <ProbabilityBar p={f.p_up} base={f.base_rate} ci={null} fill={look.fill} />
              <p className="mt-2 text-2xs text-ink-faint tabular-nums">
                Context only, not a call: p(up) {fmtProb(f.p_up)} vs usual {fmtProb(f.base_rate)}
              </p>
            </div>
          ) : (
            f.base_rate !== null && (
              <p className="mt-2 text-2xs text-ink-faint tabular-nums">
                Historically up {fmtProb(f.base_rate, 0)} of the time over {f.horizon_bars} bars.
              </p>
            )
          )}
        </div>
      )}
    </section>
  );
}
