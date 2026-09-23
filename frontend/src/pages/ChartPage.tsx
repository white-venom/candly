import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router";
import {
  useCandles,
  useForecast,
  useIndicatorCatalog,
  useIndicators,
  useInstruments,
  useLevels,
  useNews,
  usePatterns,
} from "../api/hooks";
import type { Candle, IndicatorSeries, Level, PatternSignal } from "../api/types";
import { ChartLegend } from "../chart/ChartLegend";
import { IndicatorToggles } from "../chart/IndicatorToggles";
import { PriceChart } from "../chart/PriceChart";
import { useIndicatorSelection } from "../chart/indicatorSelection";
import { latestBar, planIndicators, type HoverBar } from "../chart/transforms";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ForecastSummary } from "../components/ForecastSummary";
import { NewsItemRow } from "../components/NewsItemRow";
import { SignalList } from "../components/SignalList";
import { EmptyState, ErrorState, LoadingState, QueryView } from "../components/States";
import { Card } from "../components/ui";
import { describeError } from "../lib/errors";
import { chartPath, defaultSelection, readLastSelection, writeLastSelection } from "../lib/routes";
import { defaultTimeframe, sortTimeframes } from "../lib/timeframes";

const NO_CANDLES: Candle[] = [];
const NO_SIGNALS: PatternSignal[] = [];
const NO_LEVELS: Level[] = [];
const NO_SERIES: IndicatorSeries[] = [];

const PRICE_PANE_HEIGHT = 460;
const OSCILLATOR_PANE_HEIGHT = 130;

/** `/`, `/chart` and `/chart/:instrument` land here and are sent to a full chart URL. */
export function ChartRedirect() {
  const { instrument } = useParams();
  const instruments = useInstruments();
  if (instruments.data) {
    const known = instrument ? instruments.data.find((i) => i.id === instrument) : undefined;
    const target = known
      ? { instrument: known.id, tf: defaultTimeframe(known.timeframes) }
      : defaultSelection(instruments.data, readLastSelection());
    if (!target) return <EmptyState className="m-4" />;
    return <Navigate to={chartPath(target.instrument, target.tf)} replace />;
  }
  if (instruments.isError) return <ErrorState error={instruments.error} className="m-4" />;
  return <LoadingState label="Loading instruments…" />;
}

function WhyPanel({ instrument, tf }: { instrument: string; tf: string }) {
  const forecast = useForecast(instrument, tf);
  const patterns = usePatterns(instrument, tf);
  const news = useNews(instrument, 8);
  return (
    <div className="flex flex-col gap-3">
      <Card title="Forecast — why">
        <QueryView query={forecast} loadingLabel="Loading forecast…">
          {(f) => <ForecastSummary forecast={f} tf={tf} />}
        </QueryView>
      </Card>
      <Card title="Signals">
        <QueryView query={patterns} isEmpty={(p) => p.length === 0} empty="No pattern signals on this chart yet.">
          {(p) => <SignalList signals={p} tf={tf} />}
        </QueryView>
      </Card>
      <Card title="Latest news">
        <QueryView query={news} isEmpty={(n) => n.length === 0} empty="No news logged for this instrument yet.">
          {(items) => (
            <div>
              {items.map((item) => (
                <NewsItemRow key={item.id} item={item} />
              ))}
            </div>
          )}
        </QueryView>
      </Card>
    </div>
  );
}

function ChartView({ instrument, tf, name }: { instrument: string; tf: string; name: string }) {
  const candles = useCandles(instrument, tf);
  const patterns = usePatterns(instrument, tf);
  const levels = useLevels(instrument, tf);
  const forecast = useForecast(instrument, tf);
  const catalog = useIndicatorCatalog();
  const { enabled, toggle } = useIndicatorSelection(catalog.data);
  const indicators = useIndicators(
    instrument,
    tf,
    enabled.map((e) => e.name),
  );
  const [hover, setHover] = useState<HoverBar | null>(null);

  const series = indicators.data?.series ?? NO_SERIES;
  const plots = useMemo(() => planIndicators(series, enabled), [series, enabled]);
  const oscillatorPanes = new Set(plots.filter((p) => p.pane > 0).map((p) => p.pane)).size;

  const closed = candles.data?.candles ?? NO_CANDLES;
  const forming = candles.data?.forming ?? null;
  const forecastData = forecast.data ?? null;
  const shownBar = hover ?? latestBar(closed, forming);
  const levelList = levels.data?.levels ?? NO_LEVELS;

  return (
    <Card className="min-w-0">
      {catalog.data && catalog.data.length > 0 && (
        <div className="mb-2">
          <IndicatorToggles catalog={catalog.data} enabled={enabled} onToggle={toggle} />
        </div>
      )}
      {indicators.isError && <ErrorState error={indicators.error} className="mb-2 p-2 text-xs" />}
      <QueryView
        query={candles}
        isEmpty={(d) => d.candles.length === 0 && d.forming === null}
        loadingLabel="Loading candles…"
      >
        {(data) => (
          <>
            <ChartLegend tf={tf} bar={shownBar} forecast={forecastData} plots={plots} hasLevels={levelList.length > 0} />
            <ErrorBoundary label="price chart">
              <PriceChart
                tf={tf}
                viewKey={`${instrument}|${tf}`}
                candles={data.candles}
                forming={data.forming}
                signals={patterns.data ?? NO_SIGNALS}
                levels={levelList}
                forecast={forecastData}
                plots={plots}
                height={PRICE_PANE_HEIGHT + OSCILLATOR_PANE_HEIGHT * oscillatorPanes}
                label={`${name} ${tf} candlestick chart with patterns, levels and forecast`}
                onHover={setHover}
              />
            </ErrorBoundary>
            <p className="mt-1 text-[11px] text-ink-faint">
              Times in IST · source {data.source} · {data.candles.length} closed bars
              {data.forming && " + forming bar"}
            </p>
            {(levels.isError || catalog.isError) && (
              <p role="status" className="mt-1 text-xs text-ink-muted">
                Couldn’t load {[levels.isError && "key levels", catalog.isError && "the indicator list"].filter(Boolean).join(" or ")}
                {" — "}
                {describeError(levels.error ?? catalog.error).title}
              </p>
            )}
          </>
        )}
      </QueryView>
    </Card>
  );
}

export function ChartPage() {
  const { instrument = "", tf = "" } = useParams();
  const instruments = useInstruments();
  const info = instruments.data?.find((i) => i.id === instrument);
  const valid = info !== undefined && info.timeframes.includes(tf);

  useEffect(() => {
    if (valid) writeLastSelection({ instrument, tf });
  }, [valid, instrument, tf]);

  if (!instruments.data) {
    if (instruments.isError) return <ErrorState error={instruments.error} className="m-4" />;
    return <LoadingState label="Loading instruments…" />;
  }
  if (!info) {
    return (
      <EmptyState className="m-4">
        Unknown instrument “{instrument}”. <Link to="/chart">Open the default chart</Link>.
      </EmptyState>
    );
  }
  if (!valid) {
    return (
      <EmptyState className="m-4">
        {info.name} has no {tf} data. Available:{" "}
        {sortTimeframes(info.timeframes).map((t, i) => (
          <span key={t}>
            {i > 0 && ", "}
            <Link to={chartPath(info.id, t)}>{t}</Link>
          </span>
        ))}
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-3 xl:flex-row xl:items-start">
      <div className="min-w-0 flex-1">
        <h1 className="mb-2 text-lg font-semibold text-ink">
          {info.name} <span className="text-sm font-normal text-ink-muted">{info.id} · {tf}</span>
        </h1>
        <ChartView instrument={instrument} tf={tf} name={info.name} />
      </div>
      <aside aria-label="Why" className="w-full xl:w-[400px] xl:shrink-0">
        <WhyPanel instrument={instrument} tf={tf} />
      </aside>
    </div>
  );
}
