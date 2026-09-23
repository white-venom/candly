import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router";
import {
  useCandles,
  useForecast,
  useHealth,
  useIndicatorCatalog,
  useIndicators,
  useInstruments,
  useLevels,
  usePatterns,
} from "../api/hooks";
import type { Candle, IndicatorSeries, Instrument, PatternSignal } from "../api/types";
import { useIndicatorSelection } from "../chart/indicatorSelection";
import { levelsToDraw, type DrawnLevel } from "../chart/levels";
import { barsWithForming, latestBar, markerGlyphs, planIndicators } from "../chart/transforms";
import { NoticeStrip } from "../components/shell/NoticeStrip";
import { SetupPanel } from "../components/setup/SetupPanel";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { ChartArea } from "../components/workspace/ChartArea";
import { IndicatorsMenu } from "../components/workspace/IndicatorsMenu";
import { LayerControls } from "../components/workspace/LayerControls";
import { TopBar } from "../components/workspace/TopBar";
import { Watchlist } from "../components/workspace/Watchlist";
import { watchlistOrder } from "../lib/instruments";
import { DEFAULT_LAYERS } from "../lib/layers";
import { dataStatus } from "../lib/messages";
import { usePref } from "../lib/prefs";
import { chartPath, defaultSelection, readLastSelection, writeLastSelection } from "../lib/routes";
import { useHotkeys } from "../lib/shortcuts";
import { useSignalFilters, type SignalFilters } from "../lib/signalFilters";
import { formatDayIST } from "../lib/time";
import { defaultTimeframe, sortTimeframes } from "../lib/timeframes";

const NO_CANDLES: Candle[] = [];
const NO_SIGNALS: PatternSignal[] = [];
const NO_LEVELS: DrawnLevel[] = [];
const NO_SERIES: IndicatorSeries[] = [];
const NO_INSTRUMENTS: Instrument[] = [];

/** `/`, `/chart` and `/chart/:instrument` land here and are sent to a full chart URL. */
export function ChartRedirect() {
  const { instrument } = useParams();
  const instruments = useInstruments();
  if (instruments.data) {
    const known = instrument ? instruments.data.find((i) => i.id === instrument) : undefined;
    const target = known
      ? { instrument: known.id, tf: defaultTimeframe(known.timeframes) }
      : defaultSelection(instruments.data, readLastSelection());
    if (!target) return <EmptyState title="No instruments yet" hint="Check config/watchlist.yaml and restart the backend." />;
    return <Navigate to={chartPath(target.instrument, target.tf)} replace />;
  }
  if (instruments.isError) return <ErrorState error={instruments.error} className="flex-1" />;
  return <LoadingState label="Loading instruments…" className="flex-1" />;
}

function Workspace({
  info,
  tf,
  filters,
  onFilters,
  showWatchlist,
  showPanel,
}: {
  info: Instrument;
  tf: string;
  filters: SignalFilters;
  onFilters: (next: SignalFilters) => void;
  showWatchlist?: () => void;
  showPanel?: () => void;
}) {
  const navigate = useNavigate();
  const candles = useCandles(info.id, tf);
  const patterns = usePatterns(info.id, tf, filters);
  const levels = useLevels(info.id, tf);
  const forecast = useForecast(info.id, tf);
  const catalog = useIndicatorCatalog();
  const selection = useIndicatorSelection(catalog.data);
  const indicators = useIndicators(
    info.id,
    tf,
    selection.enabled.map((e) => e.name),
  );
  const health = useHealth();
  const [layers, setLayers] = usePref("candly.layers", DEFAULT_LAYERS);
  const [indicatorsOpen, setIndicatorsOpen] = useState(false);

  const tfs = sortTimeframes(info.timeframes);
  const pickTf = (i: number) => {
    if (tfs[i]) navigate(chartPath(info.id, tfs[i]));
  };
  useHotkeys({
    "1": () => pickTf(0),
    "2": () => pickTf(1),
    "3": () => pickTf(2),
    "4": () => pickTf(3),
    i: () => setIndicatorsOpen((o) => !o),
  });

  const closed = candles.data?.candles ?? NO_CANDLES;
  const forming = candles.data?.forming ?? null;
  const series = indicators.data?.series ?? NO_SERIES;
  const plots = useMemo(() => planIndicators(series, selection.enabled), [series, selection.enabled]);
  const signals = layers.patterns ? (patterns.data ?? NO_SIGNALS) : NO_SIGNALS;
  const markers = useMemo(() => markerGlyphs(signals, new Set(barsWithForming(closed, forming).bars.map((c) => c.time))), [signals, closed, forming]);
  const levelData = levels.data;
  const drawnLevels = useMemo(
    () => (layers.levels && levelData ? levelsToDraw(levelData.levels, closed, layers.allLevels ? "all" : "key") : NO_LEVELS),
    [layers.levels, layers.allLevels, levelData, closed],
  );
  const levelsStale = levelData?.stale === true;
  const levelsNote =
    layers.levels && levelsStale ? (levelData?.as_of !== undefined ? `Levels from ${formatDayIST(levelData.as_of)}` : "Levels from an older session") : null;
  const latest = useMemo(() => latestBar(closed, forming), [closed, forming]);
  const canConnect = dataStatus(health.data, health.error).canConnect;
  const noDataHint = canConnect ? `Connect Fyers — the backfill loads ${tf} candles.` : `Run ingest for ${tf} to load candles.`;

  return (
    <>
      <TopBar
        info={info}
        tf={tf}
        last={latest}
        asOf={closed.at(-1)?.time ?? null}
        onTimeframe={(t) => navigate(chartPath(info.id, t))}
        showWatchlist={showWatchlist}
        showPanel={showPanel}
        controls={
          <>
            <IndicatorsMenu
              entries={selection.entries}
              enabled={selection.enabled}
              onToggle={selection.toggle}
              onPreset={selection.applyPreset}
              open={indicatorsOpen}
              onOpenChange={setIndicatorsOpen}
            />
            <LayerControls layers={layers} onLayers={setLayers} filters={filters} onFilters={onFilters} levels={drawnLevels} />
          </>
        }
      />
      <NoticeStrip />
      <ChartArea
        viewKey={`${info.id}|${tf}`}
        tf={tf}
        label={`${info.name} ${tf} candlestick chart`}
        candles={candles}
        signals={signals}
        markers={markers}
        levels={drawnLevels}
        levelsStale={levelsStale}
        levelsNote={levelsNote}
        forecast={layers.forecast ? (forecast.data ?? null) : null}
        plots={plots}
        empty={{ title: `No ${tf} candles for ${info.name} yet`, hint: noDataHint }}
        noDataHint={noDataHint}
      />
    </>
  );
}

function Message({ children }: { children: ReactNode }) {
  return (
    <>
      <NoticeStrip />
      <div className="flex flex-1 items-center justify-center">{children}</div>
    </>
  );
}

export function ChartPage() {
  const { instrument = "", tf = "" } = useParams();
  const instruments = useInstruments();
  const navigate = useNavigate();
  const searchRef = useRef<HTMLInputElement>(null);
  const [layout, setLayout] = usePref("candly.layout", { watchlist: true, panel: true });
  const { filters, update: setFilters } = useSignalFilters();
  const list = instruments.data ?? NO_INSTRUMENTS;
  const info = list.find((i) => i.id === instrument);
  const valid = info !== undefined && info.timeframes.includes(tf);

  useEffect(() => {
    if (valid) writeLastSelection({ instrument, tf });
  }, [valid, instrument, tf]);

  const step = (delta: number) => {
    const order = watchlistOrder(list);
    if (order.length === 0) return;
    const index = order.findIndex((i) => i.id === instrument);
    const next = order[(index + delta + order.length) % order.length];
    navigate(chartPath(next.id, next.timeframes.includes(tf) ? tf : defaultTimeframe(next.timeframes)));
  };
  useHotkeys({
    "/": () => {
      if (searchRef.current) return searchRef.current.focus();
      setLayout({ watchlist: true });
      window.setTimeout(() => searchRef.current?.focus());
    },
    "[": () => step(-1),
    "]": () => step(1),
  });

  let main: ReactNode;
  if (!instruments.data) {
    main = <Message>{instruments.isError ? <ErrorState error={instruments.error} /> : <LoadingState label="Loading instruments…" />}</Message>;
  } else if (!info) {
    main = (
      <Message>
        <EmptyState title={`Unknown instrument “${instrument}”`} hint={<Link to="/chart">Open the default chart</Link>} />
      </Message>
    );
  } else if (!valid) {
    main = (
      <Message>
        <EmptyState
          title={`${info.name} has no ${tf} chart`}
          hint={
            <span className="inline-flex gap-2">
              Available:
              {sortTimeframes(info.timeframes).map((t) => (
                <Link key={t} to={chartPath(info.id, t)}>
                  {t}
                </Link>
              ))}
            </span>
          }
        />
      </Message>
    );
  } else {
    main = (
      <Workspace
        info={info}
        tf={tf}
        filters={filters}
        onFilters={setFilters}
        showWatchlist={layout.watchlist ? undefined : () => setLayout({ watchlist: true })}
        showPanel={layout.panel ? undefined : () => setLayout({ panel: true })}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1">
      {layout.watchlist && list.length > 0 && (
        <Watchlist instruments={list} current={instrument} tf={valid ? tf : "1D"} searchRef={searchRef} onCollapse={() => setLayout({ watchlist: false })} />
      )}
      <section aria-label="Chart" className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        {main}
      </section>
      {valid && layout.panel && <SetupPanel info={info} tf={tf} filters={filters} onCollapse={() => setLayout({ panel: false })} />}
    </div>
  );
}
