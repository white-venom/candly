import type { UseQueryResult } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import type { CandlesResponse, Forecast, PatternSignal } from "../../api/types";
import type { DrawnLevel } from "../../chart/levels";
import { PriceChart } from "../../chart/PriceChart";
import { barsWithForming, lastValues, latestBar, signalsAt, type Hover, type IndicatorPlot, type MarkerGlyph } from "../../chart/transforms";
import { currentStep, forecastTimes } from "../../lib/expected";
import { ErrorBoundary } from "../ErrorBoundary";
import { QueryView, type EmptyInfo } from "../ui/States";
import { ChartLegend } from "./ChartLegend";
import { PatternTooltip } from "./PatternTooltip";

type Props = {
  viewKey: string;
  tf: string;
  label: string;
  candles: UseQueryResult<CandlesResponse>;
  signals: PatternSignal[];
  markers: MarkerGlyph[];
  levels: DrawnLevel[];
  levelsStale: boolean;
  levelsNote: string | null;
  forecast: Forecast | null;
  plots: IndicatorPlot[];
  empty: EmptyInfo;
  noDataHint: string;
};

/** The chart with its overlays (OHLC legend, pattern card), or its loading / empty / error state. */
export function ChartArea({ viewKey, tf, label, candles, signals, markers, levels, levelsStale, levelsNote, forecast, plots, empty, noDataHint }: Props) {
  const [hover, setHover] = useState<Hover | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const latest = useMemo(() => (candles.data ? latestBar(candles.data.candles, candles.data.forming) : null), [candles.data]);
  const latestValues = useMemo(() => lastValues(plots), [plots]);
  const hovered = hover ? signalsAt(signals, hover.bar.time) : [];
  const stepsLeft = useMemo(() => {
    if (!forecast || !candles.data) return 0;
    const times = forecastTimes(forecast);
    const bars = { forming: barsWithForming(candles.data.candles, candles.data.forming).formingTime, lastClosed: candles.data.candles.at(-1)?.time ?? null };
    const step = currentStep(times, bars);
    return step === -1 ? 0 : times.length - step;
  }, [forecast, candles.data]);

  return (
    <div ref={box} className="relative min-h-[420px] flex-1 bg-surface">
      <QueryView
        query={candles}
        isEmpty={(d) => d.candles.length === 0 && d.forming === null}
        empty={empty}
        noDataHint={noDataHint}
        loadingLabel="Loading candles…"
        className="absolute inset-0"
      >
        {(data) => (
          <ErrorBoundary label="price chart">
            <PriceChart
              tf={tf}
              viewKey={viewKey}
              candles={data.candles}
              forming={data.forming}
              markers={markers}
              levels={levels}
              levelsStale={levelsStale}
              forecast={forecast}
              plots={plots}
              label={label}
              onHover={setHover}
            />
            <ChartLegend
              tf={tf}
              bar={hover?.bar ?? latest}
              plots={plots}
              values={hover?.values ?? latestValues}
              expected={stepsLeft}
              levelsNote={levelsNote}
            />
            {hover && hovered.length > 0 && <PatternTooltip signals={hovered} x={hover.x} containerWidth={box.current?.clientWidth ?? 0} />}
          </ErrorBoundary>
        )}
      </QueryView>
    </div>
  );
}
