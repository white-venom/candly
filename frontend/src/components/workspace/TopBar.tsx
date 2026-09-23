import clsx from "clsx";
import type { ReactNode } from "react";
import type { Instrument } from "../../api/types";
import type { HoverBar } from "../../chart/transforms";
import { fmtPct, fmtPrice, signTone } from "../../lib/format";
import { formatBarWhenIST } from "../../lib/time";
import { sortTimeframes } from "../../lib/timeframes";
import { ExpiryBadge } from "../ExpiryBadge";
import { IconButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Segmented } from "../ui/Controls";

const KIND: Record<Instrument["kind"], string> = { equity: "Stock", index: "Index", future: "Future" };

/**
 * Instrument, last price and change, the bar the data runs to, then the chart controls.
 * It is a size container: labels drop away as the chart column narrows.
 */
export function TopBar({
  info,
  tf,
  last,
  asOf,
  onTimeframe,
  controls,
  showWatchlist,
  showPanel,
}: {
  info: Instrument;
  tf: string;
  /** the newest bar on the chart, with the close before it */
  last: HoverBar | null;
  /** time of the last closed bar */
  asOf: number | null;
  onTimeframe: (tf: string) => void;
  controls: ReactNode;
  showWatchlist?: () => void;
  showPanel?: () => void;
}) {
  const change = last && last.prevClose ? ((last.close - last.prevClose) / last.prevClose) * 100 : null;
  const tfs = sortTimeframes(info.timeframes);

  return (
    <header className="@container flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
      {showWatchlist && <IconButton icon="chevronRight" label="Show watchlist" size="sm" onClick={showWatchlist} className="-ml-1" />}
      <div className="flex min-w-24 flex-col justify-center">
        <div className="flex min-w-0 items-center gap-1.5">
          <h1 className="truncate text-sm leading-5 font-semibold text-ink" title={`${info.name} · ${KIND[info.kind]}`}>
            {info.name}
          </h1>
          <Chip className="h-4 px-1 text-[10px]">{info.exchange}</Chip>
        </div>
        <p className="flex min-w-0 items-center gap-1.5 text-2xs leading-4 whitespace-nowrap text-ink-faint tabular-nums">
          {info.expiry && <ExpiryBadge expiry={info.expiry} compact className="h-4 px-1 text-[10px]" />}
          {asOf !== null && (
            <span className="truncate">
              as of {formatBarWhenIST(asOf, tf)}
              {tf === "1D" ? "" : " IST"}
            </span>
          )}
        </p>
      </div>
      <div className="flex shrink-0 items-baseline gap-2 tabular-nums">
        <span className="text-lg leading-6 font-semibold text-ink" title="Last price">
          {fmtPrice(last?.close)}
        </span>
        <span className={clsx("text-xs font-medium", signTone(change))} title={tf === "1D" ? "Change on the day" : `Change vs the previous ${tf} bar`}>
          {fmtPct(change)}
        </span>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <Segmented
          label="Timeframe"
          value={tf}
          onChange={onTimeframe}
          options={tfs.map((t, i) => {
            const empty = info.data[t]?.bars === 0;
            return { value: t, label: t, muted: empty, title: empty ? `No ${t} data yet · key ${i + 1}` : `Key ${i + 1}` };
          })}
        />
        {controls}
        {showPanel && <IconButton icon="panelRight" label="Show setup panel" size="sm" onClick={showPanel} />}
      </div>
    </header>
  );
}
