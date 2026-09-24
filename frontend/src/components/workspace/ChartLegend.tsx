import clsx from "clsx";
import { FAMILIES } from "../../chart/indicatorSelection";
import type { HoverBar, IndicatorPlot } from "../../chart/transforms";
import { fmtNum, fmtPrice, fmtVolume } from "../../lib/format";
import { INDICATOR_BG } from "../../lib/palette";
import { formatBarWhenIST } from "../../lib/time";

function fmtValue(v: number | undefined, onPrice: boolean): string {
  if (v === undefined) return "—";
  if (onPrice) return fmtPrice(v);
  return Math.abs(v) >= 1e5 ? fmtVolume(v) : fmtNum(v, 2);
}

/** OHLC and change of the bar under the crosshair (else the latest), indicator values, the forecast key, and a stale-levels note. */
export function ChartLegend({
  tf,
  bar,
  plots,
  values,
  expected,
  levelsNote,
}: {
  tf: string;
  bar: HoverBar | null;
  plots: IndicatorPlot[];
  values: Record<string, number>;
  /** forecast bars on the chart; 0 when the forecast layer is off */
  expected: number;
  levelsNote: string | null;
}) {
  const groups = [...new Map(plots.map((p) => [p.group, p])).values()];
  const up = bar ? bar.close >= bar.open : true;
  const change = bar && bar.prevClose ? bar.close - bar.prevClose : null;
  const changePct = change !== null && bar?.prevClose ? (change / bar.prevClose) * 100 : null;

  return (
    <div className="pointer-events-none absolute top-2 left-3 z-10 flex max-w-[calc(100%-6rem)] flex-col gap-1 text-xs [text-shadow:0_0_4px_var(--surface)]">
      {bar && (
        <p className="flex flex-wrap items-baseline gap-x-2.5 text-ink-muted tabular-nums">
          <span className="text-ink">
            {formatBarWhenIST(bar.time, tf)}
            {tf === "1D" ? "" : " IST"}
          </span>
          {bar.forming && <span className="font-medium text-forming">Forming</span>}
          {(["open", "high", "low", "close"] as const).map((k) => (
            <span key={k}>
              {k[0].toUpperCase()} <span className={up ? "text-up" : "text-down"}>{fmtPrice(bar[k])}</span>
            </span>
          ))}
          {change !== null && changePct !== null && (
            <span className={change >= 0 ? "text-up" : "text-down"}>
              {change >= 0 ? "+" : ""}
              {fmtPrice(change)} ({change >= 0 ? "+" : ""}
              {changePct.toFixed(2)}%)
            </span>
          )}
          {bar.volume !== null && bar.volume > 0 && <span>Vol {fmtVolume(bar.volume)}</span>}
        </p>
      )}
      {groups.length > 0 && (
        <ul aria-label="Indicators on the chart" className="flex flex-wrap gap-x-3 gap-y-0.5 text-ink-muted tabular-nums">
          {groups.map((g) => {
            const members = plots.filter((p) => p.group === g.group);
            const label = FAMILIES[g.group]?.label ?? g.series.label;
            return (
              <li key={g.group} className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className={clsx("h-0.5 w-3 rounded-full", INDICATOR_BG[g.slot % INDICATOR_BG.length])} />
                <span>{label}</span>
                <span className="text-ink">{members.map((m) => fmtValue(values[m.series.name], m.pane === 0)).join("  ")}</span>
              </li>
            );
          })}
        </ul>
      )}
      {expected > 0 && (
        <p className="inline-flex items-center gap-1.5 text-ink-muted">
          <span aria-hidden="true" className="h-3 w-2.5 rounded-[2px] border border-band bg-band/15" />
          Expected (next {expected})
        </p>
      )}
      {levelsNote && <p className="font-medium text-forming">{levelsNote}</p>}
    </div>
  );
}
