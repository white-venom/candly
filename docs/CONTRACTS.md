# candly — Contracts

These are the shapes every module agrees on. **Change this file first**, and mention it in your report, before changing any shape below. The lead resolves conflicts.

## 1. Conventions

| Topic | Rule |
|---|---|
| Instrument id | `EXCHANGE:SYMBOL`, e.g. `NSE:RELIANCE`, `NSE:NIFTY50`, `NSE:BANKNIFTY`, `NSE:INDIAVIX`, `BSE:SENSEX`, `MCX:CRUDEOIL`. MCX ids mean the continuous front-month future. The list lives in `config/watchlist.yaml`. |
| Timeframes | `5m`, `15m`, `1h`, `1D` |
| Time in code | tz-aware UTC (`pd.Timestamp`, `datetime64[ns, UTC]`). Never naive. |
| Time in JSON | integer UNIX seconds (UTC). The UI formats it in IST. |
| Candle time | The bar's **open** time. Intraday bars align to the session open (NSE/BSE 09:15 IST, MCX 09:00 IST). NSE `1h` bars open at 09:15, 10:15 … 15:15; the last one is 15 minutes long. A `1D` bar's time is that day's session open. |
| Closed vs forming | A bar is closed once `calendar.bar_close_time(exchange, ts, tf) <= now`. The live partial bar is "forming". |
| Prices | INR floats |
| Percent | Fields ending in `_pct` are in percent units: `1.25` means 1.25 %. Python internals use fractions for returns. |
| ATR units | Fields ending in `_atr` are divided by ATR(14) at the reference bar. |
| Probabilities | 0–1 |

## 2. Candle frame (Python)

Columns, in this order:

- `ts`: `datetime64[ns, UTC]`
- `open`, `high`, `low`, `close`, `volume`, `oi`: float64. `oi` is NaN when it doesn't apply.

Rows are sorted ascending by `ts`, and `ts` is unique. Prices are never NaN. `high ≥ max(open, close)` and `low ≤ min(open, close)`. `candly.core.schema.validate_candles` enforces all of this.

## 3. Python interfaces

### Foundation: `candly.core` (lead, done)

- `settings.get_settings() -> Settings`
  - paths: `data_dir`, `candles_dir`, `derived_dir`, `db_dir`, `secrets_dir`, `logs_dir`, `config_dir`
  - keys, stored as `SecretStr`
  - `has_fyers` / `has_kotak` / `has_anthropic` / `has_telegram`
  - `resolved_data_source()` → `"fyers"` or `"yahoo"`
- `timeframes`: `TIMEFRAMES`, `INTRADAY`, `validate_tf`, `is_intraday`, `tf_delta`.
- `instruments`:
  - `Instrument`, with fields `id`, `name`, `kind`, `tradable`, `timeframes`, `aliases`, `sources` and properties `exchange`, `symbol`, `source_symbol(name)`
  - `load_watchlist()`
  - `get_instrument(id)`, which raises `UnknownInstrument`
  - `exchange_of(id)`
- `calendar.get_calendar() -> MarketCalendar`: `is_trading_day`, `session`, `session_times`, `bar_close_time`, `expected_bar_opens`, `session_phase`, `is_open`, `next_expiry`, `is_expiry_day`.
- `schema`: `CANDLE_COLUMNS`, `validate_candles`, `empty_candles`, `CandleSchemaError`.
- `log.setup_logging()`: console plus a rotating file, with secrets redacted.

### Platform: backend-engineer

- `candly.data.store.load_candles(instrument_id, tf, start=None, end=None) -> pd.DataFrame`
  - returns closed bars only, as a candle frame (an empty frame if there is no data)
- `candly.data.store.save_candles(instrument_id, tf, df) -> int`
  - validates, then does an idempotent upsert by `ts` with an atomic write
  - returns the number of rows added or changed
- `candly.data.store.data_summary() -> dict[str, dict[str, dict]]`
  - shape: `{instrument_id: {tf: {"bars": int, "first": Timestamp | None, "last": Timestamp | None}}}`
- `candly.data.live.get_forming(instrument_id, tf) -> pd.Series | None`
  - the current partial bar
  - `None` when the market is closed or there is no live source
- `candly.data.ingest`
  - CLI: `python -m candly.data.ingest --tf 1D [--instrument NSE:RELIANCE ...] [--source auto|fyers|yahoo] [--since 2015-01-01]`
  - function: `ingest(tf, instruments=None, source="auto", since=None) -> dict[str, int]`
- `candly.news.store.NewsStore(path)`: `add(items) -> int`, `query(instrument_id=None, since=None, limit=50) -> list[NewsItem]`.
- `candly.news.fetch.poll_news() -> int`: one polling cycle over `config/news_feeds.yaml`.

### Research: quant-engineer

- `candly.indicators.compute_indicators(df, tf, names=None) -> pd.DataFrame`
  - same index as `df`, one column per series: `ema20`, `rsi14`, `macd`, `macd_signal`, `macd_hist`, `bb_upper`, `bb_mid`, `bb_lower`, `atr14`, `supertrend`, `supertrend_dir`, `vwap`, `rel_volume`, …
  - `INDICATOR_CATALOG` lists every name with its label, pane and group.
- `candly.patterns.detect_patterns(df, tf, forming_bar=None) -> pd.DataFrame`
  - columns: `ts, pattern, label, direction, state, bars, invalidation`
  - passing `forming_bar` (a partial candle) adds rows with `state="forming"`.
- `candly.features.context.compute_context(df, tf, exchange) -> pd.DataFrame`
  - columns: `trend`, `trend_strength`, `vol_regime`, `rel_volume`, `session_phase`, `near_level`, …
- `candly.features.levels.key_levels(df, tf, exchange) -> list[Level]`
- `candly.research.scorecard.build_scorecard(tf, instruments=None) -> Scorecard`
  - persists to `data/derived/scorecard_{tf}.parquet`, with meta in a `.json` file next to it
- `candly.research.scorecard.load_scorecard(tf) -> Scorecard | None`
- `candly.forecast.make_forecast(instrument_id, tf, candles, scorecard, steps=None) -> Forecast`
- `candly.ledger.Ledger(path)`: `record(forecast) -> int`, `grade_pending(load_candles) -> int`, `entries(...)`, `accuracy(...)`.

### Jobs (the lead wires these after wave 1)

- `ingest_incremental(tf)`
- `poll_news()`
- `run_forecast_cycle(tf)`: forecasts every instrument at bar close and writes each forecast to the ledger
- `grade_pending()`
- `rebuild_scorecard(tf)`: nightly

## 4. REST API

The FastAPI app is `candly.api.app:app`, and every route sits under `/api`.

- Platform routes: `api/routes/platform.py` (backend-engineer)
- Analytics routes: `api/routes/analytics.py` (quant-engineer)

Errors come back as `{"detail": string}`:

- 400: bad parameter
- 404: unknown instrument
- 503: no data yet (run ingest)

In development, the Vite server on :5173 proxies `/api` to `http://127.0.0.1:8000`.

```ts
type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };
type Direction = "bullish" | "bearish" | "neutral";
type Band = { time: number; p10: number; p50: number; p90: number };
```

### Platform routes

```ts
// GET /api/health
type Health = {
  status: "ok";
  version: string;
  time: number;
  data_source: "fyers" | "yahoo";
  keys: { fyers: boolean; fyers_connected: boolean; kotak_neo: boolean; anthropic: boolean; telegram: boolean };
  markets: { exchange: "NSE" | "BSE" | "MCX"; open: boolean; phase: string }[];
  // "blocked" when data updates can't run. There is no Yahoo fallback once Fyers keys exist. Reasons:
  //   "Fyers not connected — log in to resume data updates"
  //   "Fyers keys are not set — add FYERS_APP_ID and FYERS_SECRET_KEY to .env"  (DATA_SOURCE=fyers without keys)
  // Older backends omit it; treat missing as ok.
  ingest?: { status: "ok" | "blocked"; reason: string | null };
};

// GET /api/instruments -> Instrument[]
type Instrument = {
  id: string; exchange: "NSE" | "BSE" | "MCX"; symbol: string; name: string;
  kind: "equity" | "index" | "future"; tradable: boolean; timeframes: string[];
  data: Record<string, { bars: number; first: number | null; last: number | null }>;  // keyed by tf
};

// GET /api/candles?instrument=NSE:RELIANCE&tf=1D&limit=500&end=<unix>
// limit: default 500, max 5000. end: default now. Closed bars only, ascending.
type CandlesResponse = { instrument: string; tf: string; source: string; candles: Candle[]; forming: Candle | null };

// GET /api/news?instrument=NSE:RELIANCE&limit=50   (instrument is optional; without it, all news)
// -> NewsItem[], newest first
type NewsItem = {
  id: string; title: string; url: string; source: string;
  published_at: number | null; fetched_at: number;
  instruments: string[];
  sentiment: number | null;                         // -1 .. 1
  sentiment_method: "lexicon" | "claude" | null;
  event_type: string | null; summary: string | null;
};

// Fyers login. Fyers rejects localhost/IP redirect URLs, so the app's redirect URL is Fyers' own page
// (https://trade.fyers.in/api-login/redirect-uri/index.html). After logging in, the user copies the
// address-bar URL (it contains auth_code=...) and pastes it into the dashboard.
// GET  /api/auth/fyers/login     -> 307 redirect to the Fyers login page (issues a one-time `state`, valid 15 min)
// POST /api/auth/fyers/code      body { code: string }  (a raw auth_code OR the full redirect URL)
//                                Content-Type must be application/json, else 415 (blocks cross-site simple POSTs).
//                                If the pasted URL carries a `state`, it must be one issued by /login, else 400
//                                "login link expired or not from this app — click Connect Fyers again".
//                                -> { connected: boolean; expires_at: number | null }; 400 { detail } if the exchange fails
// GET  /api/auth/fyers/callback  -> same exchange for a local redirect URL (kept for future use); 307 to `${FRONTEND_URL}/?fyers=connected|error`
// GET  /api/auth/fyers/status    -> { connected: boolean; expires_at: number | null }
```

### Analytics routes

```ts
// GET /api/indicators/catalog
type IndicatorInfo = { name: string; label: string; pane: "price" | "oscillator" | "volume"; group: "trend" | "momentum" | "volatility" | "volume" };

// GET /api/indicators?instrument=&tf=&names=ema20,ema50,rsi14&limit=500
// Multi-line indicators come back as separate series (macd, macd_signal, macd_hist; bb_upper, bb_mid, bb_lower).
type IndicatorSeries = { name: string; label: string; pane: "price" | "oscillator" | "volume"; points: { time: number; value: number | null }[] };
type IndicatorsResponse = { instrument: string; tf: string; series: IndicatorSeries[] };

// GET /api/patterns?instrument=&tf=&limit=200
// -> PatternSignal[], newest first; includes a forming signal when there is one
type ScoreStats = {
  horizon_bars: number; n: number; hit_rate: number; base_rate: number;
  ci_low: number; ci_high: number; posterior: number; q_value: number | null;
  expectancy_after_cost_pct: number | null; certified: boolean;
};
type PatternSignal = {
  id: string;                                   // `${instrument}|${tf}|${time}|${pattern}`
  instrument: string; tf: string; time: number;
  pattern: string; label: string; direction: Direction; state: "confirmed" | "forming";
  bars: number; invalidation: number | null;
  context: {
    trend: "up" | "down" | "sideways" | null; vol_regime: "low" | "normal" | "high" | null;
    session_phase: string | null; rel_volume: number | null; near_level: string | null; rsi14: number | null;
  };
  stats: ScoreStats | null;
};

// GET /api/levels?instrument=&tf=
type Level = {
  price: number; label: string;
  kind: "pdh" | "pdl" | "pdc" | "swing_high" | "swing_low" | "vwap" | "pivot" | "r1" | "r2" | "s1" | "s2" | "cpr_top" | "cpr_bottom";
};
type LevelsResponse = { instrument: string; tf: string; levels: Level[] };

// GET /api/forecast?instrument=&tf=&steps=3
// p_up may be non-null while abstaining; the UI then shows it only as muted context, never as a call.
type Forecast = {
  instrument: string; tf: string; method: string;
  made_at: number; ref_time: number; ref_close: number; horizon_bars: number;
  p_up: number | null; p_up_ci: [number, number] | null; base_rate: number | null;
  abstain: boolean; abstain_reason: string | null; confidence: "low" | "medium" | "high" | null;
  expected_move_pct: number | null;
  ghost_candles: Candle[];                      // future bar times, volume 0
  bands: Band[];
  invalidation: number | null;
  drivers: { name: string; effect: Direction; detail: string }[];
  n_analogs: number;
  explanation: string | null;                   // Claude text from Phase 4; null until then
};

// GET /api/scanner?tf=1D   -> ScannerRow[], sorted by score, descending
type ScannerRow = {
  instrument: string; name: string; tf: string; time: number;
  last_close: number; change_pct: number;
  p_up: number | null; base_rate: number | null; abstain: boolean;
  score: number;                                // |p_up - base_rate|; 0 when abstaining
  direction: Direction;
  top_signal: { label: string; state: "confirmed" | "forming"; certified: boolean } | null;
  rel_volume: number | null; trend: "up" | "down" | "sideways" | null;
};

// GET /api/scorecard?tf=1D&instrument=&pattern=&certified_only=false
type ScorecardRow = {
  pattern: string; label: string; direction: Direction;
  instrument: string;                           // an instrument id, or "ALL" (pooled)
  context: string;                              // "all" or a bucket such as "trend=down"
  horizon_bars: number; n: number; hits: number; hit_rate: number; base_rate: number;
  ci_low: number; ci_high: number;             // Wilson interval, display only
  p_value: number;                             // cluster-robust, one-sided in the pattern's direction (two-sided for neutral)
  q_value: number | null;                      // BH q; null when the row is outside the BH family (n_clusters < min_samples)
  posterior: number;
  expectancy_after_cost_pct: number | null;
  validation_n: number; validation_hit_rate: number | null;
  certified: boolean;                          // also requires n_clusters >= stats.min_clusters
};
type ScorecardResponse = {
  meta: {
    tf: string; built_at: number | null; train_end: string; holdout_start: string;
    n_tests: number;                           // BH family size
    n_rows: number;                            // all emitted rows
    instruments: string[];                     // one exchange per build
    fdr_alpha: number; horizons: number[];
    config_sha256: Record<string, string>;     // research / patterns / costs YAML hashes used for the build
  };
  rows: ScorecardRow[];
};

// GET /api/ledger?instrument=&tf=&status=&limit=100   -> LedgerEntry[], newest first
type StepGrade = {
  step: number; close_err_pct: number; close_err_atr: number; high_err_atr: number; low_err_atr: number;
  range_iou: number; body_iou: number; color_match: boolean; in_band_80: boolean;
};
type LedgerEntry = {
  id: number; instrument: string; tf: string; method: string;
  made_at: number; ref_time: number; ref_close: number; horizon_bars: number;
  p_up: number | null; abstain: boolean;
  predicted: Candle[]; bands: Band[];
  actual: Candle[];                             // filled in as bars close
  status: "pending" | "graded" | "void";
  grade: { direction_hit: boolean | null; brier: number | null; match_score: number; steps: StepGrade[] } | null;
};

// GET /api/accuracy?instrument=&tf=&days=90
type AccuracyResponse = {
  summary: {
    n_forecasts: number; n_graded: number; n_abstained: number;
    direction_hit_rate: number | null; brier: number | null; brier_baseline: number | null; skill: number | null;
    ece: number | null; band_coverage_80: number | null; mean_match_score: number | null; mean_close_err_atr: number | null;
  };
  calibration: { bin_low: number; bin_high: number; mean_pred: number | null; observed: number | null; n: number }[];  // null when n = 0
  rolling: { time: number; hit_rate: number | null; brier: number | null; match_score: number | null }[];  // over the last 30 graded forecasts
  by_group: { group_by: "instrument" | "tf" | "pattern" | "session_phase" | "vol_regime"; key: string; n: number; hit_rate: number | null; brier: number | null }[];
};
```

### Details settled during wave 1

These were agreed while building and override anything above.

- **Candles**
  - `CandlesResponse.source` is where the stored series came from, e.g. `"yahoo"`, `"fyers"` or `"fyers+yahoo"`. It is not the current setting.
  - `save_candles` takes an optional `source=`.
  - The store also exposes `candle_source()` and `series_stats()`.
  - `load_candles` rejects naive `start`/`end`.
- **Indicators**
  - Series names include `adx14`, `plus_di14`, `minus_di14`, `stoch_k`, `stoch_d`, `bb_width` and `supertrend_dir`.
  - The family names `macd`, `bb`, `stoch` and `adx` expand to all their lines.
  - A series whose name ends in `_hist` is drawn as a histogram.
- **Scorecard**
  - `base_rate` is the rate of moves *in the pattern's direction* on the same rows: P(down) for bearish rows, P(up) otherwise. That makes `hit_rate − base_rate` comparable across rows.
  - CIs are at `stats.ci_level` from research.yaml (95%).
  - `/api/scorecard` returns 503 until a scorecard has been built.
- **Ledger and accuracy**
  - `/api/ledger` and `/api/accuracy` accept an optional `method` query: ledger defaults to all methods, accuracy to `analog_v1`.
  - There are always 10 calibration bins. Empty bins have `mean_pred` and `observed` set to `null`.
- **Forecast**
  - The first ghost candle is the first bar **after** the reference (last closed) bar. During a session, that is the bar that is currently forming.
  - `expected_move_pct` is signed.
  - `ScannerRow.score` is a probability difference (0–1).
- **Errors:** every validation error returns 400 with `{detail}`, including FastAPI's own type errors.
- **Research APIs** (after the quant audit):
  - `build_scorecard` defaults to the `go_no_go_1` slice universe and accepts one exchange per build.
  - `make_forecast(..., check_stale=True)`: replays pass `check_stale=False`.
  - `Ledger.record(forecast)` takes no `now`; the late check uses the real clock.
  - `research.evaluate.evaluate_forecasts(tf, method="analog_v1", allow_holdout=False, *, period="validation")` returns Brier, baseline Brier, skill, quantile ECE, hit rate, band coverage, abstention/void counts and gate results. `period="holdout"` requires `allow_holdout=True`.
  - Scorecard rows carry an internal `n_clusters` (Parquet only, not in the API yet).
- **News:** the Python `NewsItem` is a pydantic model (`news/models.py`). A backtest must filter on `fetched_at` (as-of time), never on `published_at`.

### Wave 2 additions: expiry and trader-test fixes

**Expiry, in Python**
- `candly.core.expiry.expiry_info(instrument_id, d) -> ExpiryInfo | None`
  - Rule-based, for NSE/BSE, from `config/expiry.yaml`.
  - `ExpiryInfo` fields: `next_expiry` (date), `kind` (weekly/monthly/contract), `days_to_expiry` (trading days; 0 on the day), `is_expiry_day`, `is_monthly_expiry_day`.
- `candly.data.expiries.expiry_info(instrument_id, d) -> ExpiryInfo | None` (backend) is the single entry point for everyone else:
  - NSE/BSE: delegates to core.
  - MCX: uses the contract expiries from the Fyers symbol master, with `kind="contract"`.
  - INDIAVIX: None.

**Expiry, in the API**

```ts
type ExpiryInfo = { next: string /* YYYY-MM-DD, IST */; kind: "weekly" | "monthly" | "contract"; days_to_expiry: number; is_expiry_day: boolean };
// Instrument gains:        expiry: ExpiryInfo | null
// PatternSignal.context:   expiry_day: boolean | null; days_to_expiry: number | null
// ScannerRow gains:        expiry: ExpiryInfo | null; abstain_reason: string | null
// Forecast.drivers may include { name: "Expiry", ... } when the reference bar is on or near an expiry.
// LevelsResponse gains:    as_of: number (UNIX s of the bar the levels come from); stale: boolean
//                          (true when a newer session exists in any timeframe than the daily bar used)
```

**Data layer (backend wave 2)**
- `data.expiries`
  - `expiry_info(instrument_id, d)`
  - `known_mcx_expiries(instrument_id) -> list[date]`
  - `mcx_roll_dates(instrument_id) -> list[date]`
  - MCX expiries come from the live Fyers symbol master and are recorded in `data/expiries/mcx.json` as they are seen. So MCX expiry info starts on 2026-09-23. Masking historical rolls needs a dated MCX expiry list in config (to do).
  - An MCX contract expiry day sets `is_monthly_expiry_day = true`.
- `data.quality`
  - `missing_daily_sessions(instrument_id) -> list[date]`
  - `suspicious_bars(df, kind) -> bool mask`
  - `bar_count_anomalies(...)`
  - CLI `python -m candly.data.quality --report` → `data/quality/report.json`
- `data.store`
  - `load_ts(instrument_id, tf)`: ts column only.
  - `archive_source(source)`: moves series into `data/archive/<source>-<UTC>/`.
- **Ingest CLI**
  - `--archive-source yahoo`.
  - The 1D ingest re-reads the last 5 sessions and re-fetches up to 10 daily sessions that are missing while intraday data exists.

**Live expiries (daily check)**

Expiry dates change: SEBI reshuffles, holiday shifts and exchange circulars all move them. So live and future expiries come from the exchanges' own contract lists.
- `data.expiries.refresh_expiries()`
  - Runs daily at 08:30 IST (weekends too) and at scheduler start.
  - Downloads the Fyers NSE_FO, BSE_FO and MCX_COM masters.
  - Appends every listed date to `data/expiries/live.json` with a `first_seen` date. A date the exchange stops listing is kept but marked `withdrawn`.
  - Compares the next 3 dates with the rules and logs each mismatch once.
- Precedence:
  - Exchange dates are used from the first refresh until 3 days after the last good one.
  - Before that, or if the data is stale, NSE/BSE fall back to the rules in `config/expiry.yaml`, which also serve backtests.
  - MCX always uses stored exchange dates.
- Helpers: `expiry_with_source()`, `exchange_expiries()`, `expiry_check()`.
- API:

```ts
// Health gains:
expiry_check?: { status: "ok" | "mismatch" | "unavailable"; checked_at: number | null;
                 mismatches: { instrument: string; rules: string; exchange: string }[] };
// ExpiryInfo (in Instrument.expiry) gains:
source?: "exchange" | "rules";
```

**Fyers first sync (one-step setup)**

After the first successful Fyers login, the backend runs `jobs.sync.run_fyers_sync()` in the background:
1. refresh expiries
2. archive Yahoo series (move, never delete)
3. 1D backfill from 2005
4. 5m backfill from 2017-07-03
5. 15m/1h resampled from 5m
6. 1D gap fill
7. quality report
8. observed holidays → `data/derived/holidays_observed.json`
9. `core.calendar.reload_calendar()`
10. scorecards for 1D/1h/15m/5m

The run is resumable, one at a time, and keeps its state in `data/sync/state.json`.

```ts
// POST /api/sync/fyers (JSON) -> 202 SyncStatus; 409 already running; 415 not JSON;
//                                400 no keys / DATA_SOURCE=yahoo / Fyers not connected
// GET  /api/sync/status       -> SyncStatus & { steps: string[] }
// Step ids, in order: refresh_expiries, archive, backfill_1d, backfill_5m, build_15m_1h, fill_1d_gaps,
//                     quality_report, holidays, scorecards. Scheduled ingests pause while a sync runs.
// A Fyers login starts the sync when any series isn't pure Fyers, Fyers 1D is missing, or a run is unfinished.
type SyncStatus = { status: "idle" | "running" | "done" | "error"; step: string | null; progress: number | null /* 0-1 */;
                    message: string | null; started_at: number | null; finished_at: number | null };
// Health gains: sync?: SyncStatus
```

**Claude (llm) and alerts**
- `candly.llm`
  - `tag_pending_news()`: every 5 min, Haiku 4.5. It tags recent news with instruments, event type, sentiment, magnitude and a summary.
  - `explain_recent_calls(limit, tf)`: after 1D/1h forecast cycles, Opus 5.
  - `get_explanation(...)`: fills `Forecast.explanation` for non-abstaining forecasts, reading the cache only.
  - `write_brief(kind, facts)`.
  - Every function is a no-op without `ANTHROPIC_API_KEY`. Every call is logged with its cost to `data/db/llm.sqlite`, capped by `config/llm.yaml` `daily_budget_usd`. Text containing a number that isn't in the supplied facts is discarded.
- `candly.alerts`
  - `run_alert_checks()`: after every pipeline run. Covers new calls, stop hits, expiry today, and data paused/resumed.
  - Pre-market brief at 08:45 IST and post-market review at 16:15 IST, on trading days.
  - Everything is a no-op without Telegram keys. Deduplicated in `data/db/alerts.sqlite`. Settings live in `config/alerts.yaml`.

```ts
// POST /api/alerts/test   (JSON) -> { sent: boolean; detail: string }      415 if not JSON
// GET  /api/alerts/preview?kind=pre_market|post_market -> { kind: string; text: string }
```

**Option chains and the research universe (2026-09-24)**
- `data.options`: `take_snapshots()`, `load_chain(underlying, date)`, `load_summary(underlying, date)`.
  - Underlyings: NIFTY, BANKNIFTY and SENSEX, the 2 nearest expiries, ATM ± 15 strikes.
  - Scheduled every 5 minutes, 09:00–15:55 IST (second 50), plus a snapshot at 15:31:50.
  - Stored as `data/options/{UNDERLYING}/{date}.parquet` and `.../summary/{date}.parquet`. `ts` is our fetch time.
  - Summary fields: PCR by OI and by volume, max pain and ATM IV (all over the logged strikes only), and 25Δ skew using Fyers' delta.
  - `fyers.option_chain()` calls `GET https://api-t1.fyers.in/data/options-chain-v3` (symbol, strikecount ≤ 50, timestamp = expiry epoch, greeks = 1).
- Research universe: `config/universe_nifty200.yaml` (current constituents, so it carries survivorship bias).
  - Ingest takes `--universe nifty200`, `--max-per-minute N` (to share the Fyers cap with the API server), and prints `[n/N]` progress.
  - A nightly `universe_daily_ingest` job runs at 16:05 IST on weekdays.
- FII/DII flows are **not** collected. The exchanges' terms forbid automated collection, and no clean source exists.

**Signals and calls**
- `ScannerRow` excludes instruments with `tradable=false` (e.g. INDIAVIX).
- A directional call (`abstain=false`) always has a non-null `invalidation` at least `patterns.min_stop_atr` ATR from the reference close. Otherwise the forecast abstains.
- `Forecast` gains `trade: { entry: number; stop: number; target: number; reward_risk: number } | null`, non-null only for directional calls:
  - `entry` = reference close (fill at the next open);
  - `stop` = invalidation;
  - `target` = p50 of the last step.

  Position size is computed in the UI from the user's own capital and risk-% settings, as `qty = floor(capital × risk% / |entry − stop|)`.
- New abstain reasons: "unvalidated bucket", "horizon crosses session close", "edge below costs", "reward below risk".
- Scorecards are built **per exchange** and never cross exchanges.
  - Files in `data/derived`:
    - NSE: `scorecard_{tf}.parquet/.json`, `analogs_{tf}`, `validation_{tf}`, `validation_base_{tf}`.
    - BSE and MCX: the same names with `_BSE` / `_MCX` added.
  - `build_scorecard(tf, instruments=None, load=None, *, exchange=None, persist=True)` and `load_scorecard(tf, exchange="NSE")`.
  - `GET /api/scorecard` takes an optional `exchange` (default: the instrument's exchange, else NSE). It returns 400 on an unknown exchange or a mismatch. `meta` gains `exchange`.
- `research.evaluate` output includes:
  - `population`, `n_in_population`
  - `skill_ci`, `ci_level`
  - `calibration_p`, `calibration_sims`
  - `bootstrap_resamples`, `seed`
  - `gates` (certified_buckets, brier_skill, calibration) and `gate_details`
- Every abstain reason is `"<reason>"` or `"<reason>: <detail>"`. Match on the prefix, never on the full string.
- Research hooks:
  - `make_forecast(..., bucket_gate=True)`. Validation-period replays pass `bucket_gate=False`, because gating on validation stats while scoring that period would leak.
  - `compute_context(..., instrument_id=None)`: `rel_volume` is None for indices.
  - `cost_breakdown` includes `dp_charge`.
  - `costs.yaml` `reference_notional_inr` sizes the flat charges.
- `PatternSignal` rows can be filtered: `/api/patterns?...&directional_only=true&certified_only=true`.

### Grading definitions

These are used by the ledger and the Accuracy page.

- `direction_hit`: `sign(close[h] − ref_close) == sign(p_up − base_rate)`. It is null when abstaining.
- `brier`: `(p_up − y)²`, where `y = 1` if `close[h] > ref_close`. The baseline uses `base_rate` in place of `p_up`, and `skill = 1 − brier / brier_baseline`.
- `range_iou`: the overlap of the predicted and actual `[low, high]` divided by their union. `body_iou` is the same calculation over `[min(open, close), max(open, close)]`.
- `in_band_80`: `p10 ≤ actual close ≤ p90`.
- `match_score` (display only, never used as a model input): `100 × mean over steps of (0.4·range_iou + 0.3·max(0, 1 − close_err_atr) + 0.3·color_match)`.
- Abstained forecasts are graded but excluded from hit rate, Brier and calibration. They are counted in `n_abstained`.
- **Bars are matched by time, never by position.** A step whose predicted bar time never appears as a bar keeps the forecast `pending`. It becomes `void` at last target close + 7 days, and the reason is stored internally. Grading only ever updates rows that are still pending.

### Stage A v2 (pivot): range_v1, the expected candle

Pre-registered in `config/pivot.yaml` (PLAN.md §20a). Added by quant-engineer on 2026-09-24.

**Forecast**
- `GET /api/forecast?instrument=&tf=&steps=&method=`
  - `method` is optional: `range_v1` (the default) or `analog_v1`. Any other value returns 400.
  - Without a saved range model for the instrument's exchange and timeframe, the default falls back to `analog_v1`, and the response's `method` says which one ran. An explicit `method=range_v1` returns 503 in that case.
- A `range_v1` Forecast:
  - `ghost_candles` follow the p50 high, low and close of each step. Step 1 opens at the reference close and each later step opens at the previous ghost close; high and low are widened when needed so the candle stays a valid OHLC.
  - `bands` are the close's p10/p50/p90.
  - `expected_move_pct` is the last step's p50 close versus the reference close (signed).
  - `horizon_bars` is the number of steps (3).
  - `n_analogs` is 0.
  - Direction comes only from a **validated** regime_v1 call on the same daily bar (1D only). regime_v1 failed go/no-go #2, so today every range_v1 forecast has `abstain: true`, `abstain_reason: "direction unclear: range forecast only"`, and null `p_up`, `base_rate`, `confidence`, `invalidation` and `trade`. Its ghost candles and bands are still present.
  - `drivers`: "Expected range", "Volatility", "Expiry" (on or one day before an expiry), "Direction", and "Regime (N sessions)". The Regime driver says why no call is made and never shows an unvalidated probability.
- `ScannerRow` gains `expected_move_pct: number | null` (the same value as the forecast's).
- Scanner rows come from the forecasts the forecast cycle keeps in memory (`candly.forecast.latest`). A row is computed on request only when its kept forecast is missing or older than the last bar due.
  - range_v1 rows have `abstain: true`, `score: 0` and `direction: "neutral"`.
  - An intraday row appears only when a range model covers the instrument, because analog_v1 runs on 1D only.

**Ledger and accuracy**
- `StepGrade` gains `category: "same" | "close" | "wrong" | null`, following pivot.yaml `candle_accuracy`:
  - `wrong`: the actual close is outside p10–p90. This is checked first, so `same + close` equals band coverage.
  - `same`: |close − p50| ≤ 0.25 ATR, and the bar's high and low stay inside the ghost candle's high and low (the "predicted range box").
  - `close`: any other close inside the band.
  - null when the step has no band. Grades made before this change have no category.
- `AccuracyResponse` gains a top-level `category_shares: { same: number; close: number; wrong: number } | null`: shares from 0 to 1 over graded steps that have a category.
- `/api/ledger` and `/api/accuracy` accept `method=range_v1`. Accuracy still defaults to `analog_v1`.

**Python**
- `candly.forecast.make_range_forecast(instrument_id, tf, candles, now=None, *, steps=None, load=None, check_stale=True, model=None) -> Forecast`
  - raises `RangeUnavailable` when no saved model covers the instrument, and `ValueError` when there are no closed candles;
  - `load(instrument_id, tf)` supplies the context series (the market index and India VIX).
- `run_forecast_cycle(tf)` records range_v1 plus the baselines wherever a model covers the instrument, and analog_v1 on 1D only.
  - Counts gain `range_recorded`, `range_unavailable` and `seconds`.
  - The cycle fills `candly.forecast.latest`.
- `candly.api.routes.analytics.warm_scanner(tfs=("1D", "5m", "15m", "1h")) -> {tf: seconds}` is meant for a background thread at app startup. Without it, the first scanner request after a restart computes every row (8–11 s).
- `candly.research.pivot_config.load_pivot_config()` is the one strict reader of pivot.yaml, for both range_v1 and regime_v1.
- Training:
  - CLI `python -m candly.research.range_model [--exchange NSE ...] [--tf 1D ...]`;
  - models go to `data/models/range_v1/{EXCHANGE}_{tf}/` (27 LightGBM text files plus `meta.json`, which holds the config and code hashes);
  - walk-forward predictions go to `data/derived/range_v1/walk_forward_{EXCHANGE}_{tf}.npz`.
- Evaluation:
  - `candly.research.range_eval.evaluate_range(tf, exchange, period="validation" | "holdout", allow_holdout=False)`;
  - `period="holdout"` needs `allow_holdout=True` and uses the saved production model;
  - CLI `python -m candly.research.range_eval` writes `docs/test-reports/2026-09-24-range-v1-validation.{json,md}`.
