import { useCandles, useForecast, useHealth, usePatterns } from "../../api/hooks";
import type { CandlesResponse, Instrument } from "../../api/types";
import { barsWithForming } from "../../chart/transforms";
import { unitLabel } from "../../lib/instruments";
import { dataStatus, humanizeText } from "../../lib/messages";
import type { SignalFilters } from "../../lib/signalFilters";
import { IconButton } from "../ui/Button";
import { Eyebrow } from "../ui/Chip";
import { QueryView } from "../ui/States";
import { ExpectedCard } from "./ExpectedCard";
import { PanelNews } from "./PanelNews";
import { RecentSignals } from "./RecentSignals";
import { TradeCard } from "./TradeCard";
import { VerdictCard } from "./VerdictCard";
import { WhyList } from "./WhyList";

const formingTime = (d: CandlesResponse | undefined) => (d ? barsWithForming(d.candles, d.forming).formingTime : null);

/** The right-hand panel: the expected next candle, verdict, trade plan, why, recent signals, news. */
export function SetupPanel({ info, tf, filters, onCollapse }: { info: Instrument; tf: string; filters: SignalFilters; onCollapse: () => void }) {
  const forecast = useForecast(info.id, tf);
  // the chart's own query: tells the card whether the first forecast bar is the one forming now
  const candles = useCandles(info.id, tf);
  const bars = { forming: formingTime(candles.data), lastClosed: candles.data?.candles.at(-1)?.time ?? null };
  const patterns = usePatterns(info.id, tf, filters);
  const health = useHealth();
  const canConnect = dataStatus(health.data, health.error).canConnect;

  return (
    <aside aria-label="Setup" className="flex w-[340px] shrink-0 flex-col border-l border-line bg-surface">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line pr-2 pl-4">
        <h2 className="text-sm font-semibold text-ink">Setup</h2>
        <span className="text-xs text-ink-faint">
          {info.symbol} · {tf}
        </span>
        <IconButton icon="panelRight" label="Hide setup panel" size="sm" side="bottom-end" onClick={onCollapse} className="ml-auto" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-5 p-4">
          <QueryView query={forecast} loadingLabel="Loading forecast…" noDataHint="The forecast needs candles first." className="rounded-lg border border-line">
            {(f) => (
              <>
                <ExpectedCard forecast={f} tf={tf} bars={bars} canConnect={canConnect} />
                <VerdictCard forecast={f} tf={tf} canConnect={canConnect} />
                {f.explanation && (
                  <section aria-label="In plain words">
                    <Eyebrow>In plain words</Eyebrow>
                    <p className="mt-2 text-sm leading-relaxed text-ink-muted">{humanizeText(f.explanation)}</p>
                  </section>
                )}
                {!f.abstain && f.trade && <TradeCard trade={f.trade} units={unitLabel(info)} />}
                <WhyList drivers={f.drivers} />
              </>
            )}
          </QueryView>
          <RecentSignals query={patterns} tf={tf} filters={filters} />
          <PanelNews instrument={info.id} name={info.name} />
        </div>
      </div>
    </aside>
  );
}
