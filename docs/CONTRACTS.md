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
// GET  /api/auth/fyers/login     -> 307 redirect to the Fyers login page
// POST /api/auth/fyers/code      body { code: string }  (a raw auth_code OR the full redirect URL)
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
  ci_low: number; ci_high: number; p_value: number; q_value: number; posterior: number;
  expectancy_after_cost_pct: number | null;
  validation_n: number; validation_hit_rate: number | null;
  certified: boolean;
};
type ScorecardResponse = {
  meta: { tf: string; built_at: number | null; train_end: string; holdout_start: string; n_tests: number; fdr_alpha: number; horizons: number[] };
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
  calibration: { bin_low: number; bin_high: number; mean_pred: number; observed: number; n: number }[];
  rolling: { time: number; hit_rate: number | null; brier: number | null; match_score: number | null }[];  // over the last 30 graded forecasts
  by_group: { group_by: "instrument" | "tf" | "pattern" | "session_phase" | "vol_regime"; key: string; n: number; hit_rate: number | null; brier: number | null }[];
};
```

### Grading definitions

These are used by the ledger and the Accuracy page.

- `direction_hit`: `sign(close[h] − ref_close) == sign(p_up − base_rate)`. It is null when abstaining.
- `brier`: `(p_up − y)²`, where `y = 1` if `close[h] > ref_close`. The baseline uses `base_rate` in place of `p_up`, and `skill = 1 − brier / brier_baseline`.
- `range_iou`: the overlap of the predicted and actual `[low, high]` divided by their union. `body_iou` is the same calculation over `[min(open, close), max(open, close)]`.
- `in_band_80`: `p10 ≤ actual close ≤ p90`.
- `match_score` (display only, never used as a model input): `100 × mean over steps of (0.4·range_iou + 0.3·max(0, 1 − close_err_atr) + 0.3·color_match)`.
- Abstained forecasts are graded but excluded from hit rate, Brier and calibration. They are counted in `n_abstained`.
