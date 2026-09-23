import clsx from "clsx";
import type { ReactNode } from "react";
import type { Forecast } from "../api/types";
import { fmtPrice, fmtVolume } from "../lib/format";
import { INDICATOR_BG } from "../lib/palette";
import { formatBarTimeIST } from "../lib/time";
import type { HoverBar, IndicatorPlot } from "./transforms";

function Key({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return (
    <li className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className="inline-flex h-3 w-4 items-center justify-center">
        {swatch}
      </span>
      {children}
    </li>
  );
}

function Readout({ bar, tf }: { bar: HoverBar; tf: string }) {
  const tone = bar.close >= bar.open ? "text-up" : "text-down";
  return (
    <p className="flex flex-wrap items-center gap-x-3 text-xs text-ink-muted tabular-nums">
      <span className="text-ink">{formatBarTimeIST(bar.time, tf)} IST</span>
      {bar.forming && <span className="text-forming">forming</span>}
      <span>
        O <span className={tone}>{fmtPrice(bar.open)}</span>
      </span>
      <span>
        H <span className={tone}>{fmtPrice(bar.high)}</span>
      </span>
      <span>
        L <span className={tone}>{fmtPrice(bar.low)}</span>
      </span>
      <span>
        C <span className={tone}>{fmtPrice(bar.close)}</span>
      </span>
      <span>V {fmtVolume(bar.volume)}</span>
    </p>
  );
}

export function ChartLegend({
  tf,
  bar,
  forecast,
  plots,
  hasLevels,
  levelsStale,
}: {
  tf: string;
  bar: HoverBar | null;
  forecast: Forecast | null;
  plots: IndicatorPlot[];
  hasLevels: boolean;
  levelsStale: boolean;
}) {
  const groups = [...new Map(plots.map((p) => [p.group, p])).values()];
  return (
    <div className="flex flex-col gap-1 px-1 pb-2">
      {bar && <Readout bar={bar} tf={tf} />}
      <ul aria-label="Chart legend" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
        <Key
          swatch={
            <>
              <span className="h-3 w-1.5 bg-up" />
              <span className="ml-0.5 h-3 w-1.5 bg-down" />
            </>
          }
        >
          Candles
        </Key>
        <Key swatch={<span className="h-3 w-2 border border-forming" />}>Forming bar</Key>
        <Key swatch={<span className="text-[10px] text-up">▲</span>}>Pattern</Key>
        <Key swatch={<span className="size-2 rounded-full bg-up/50" />}>Forming pattern</Key>
        {forecast && (
          <>
            <Key
              swatch={
                <span
                  className={clsx(
                    "h-3 w-2 border",
                    forecast.abstain ? "border-abstain bg-abstain/30" : "border-up bg-up/30",
                  )}
                />
              }
            >
              {forecast.abstain ? "Forecast (abstaining, grey)" : "Forecast"}
            </Key>
            <Key swatch={<span className="w-4 border-t border-band" />}>10–90% band</Key>
            <Key swatch={<span className="w-4 border-t-2 border-dashed border-band" />}>Median</Key>
            {forecast.invalidation !== null && (
              <Key swatch={<span className={clsx("w-4 border-t-2 border-dashed", forecast.abstain ? "border-abstain" : "border-danger")} />}>
                Invalidation
              </Key>
            )}
          </>
        )}
        {hasLevels && (
          <Key
            swatch={
              <span className={clsx("flex w-4 flex-col gap-0.5", levelsStale && "opacity-50")}>
                <span className={clsx("border-t border-down", levelsStale ? "border-dashed" : "border-dotted")} />
                <span className={clsx("border-t border-up", levelsStale ? "border-dashed" : "border-dotted")} />
              </span>
            }
          >
            {levelsStale ? "Levels (stale)" : "Levels"}
          </Key>
        )}
        {groups.map((p) => (
          <Key key={p.group} swatch={<span className={clsx("h-0.5 w-4", INDICATOR_BG[p.slot % INDICATOR_BG.length])} />}>
            {plots
              .filter((q) => q.group === p.group)
              .map((q) => q.series.label)
              .join(" · ")}
          </Key>
        ))}
      </ul>
    </div>
  );
}
