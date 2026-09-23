// Mirrors docs/CONTRACTS.md §4. Change the contract first, then this file.

export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };
export type Direction = "bullish" | "bearish" | "neutral";
export type Band = { time: number; p10: number; p50: number; p90: number };

// ---- Platform routes ----

// GET /api/health
export type Health = {
  status: "ok";
  version: string;
  time: number;
  data_source: "fyers" | "yahoo";
  keys: { fyers: boolean; fyers_connected: boolean; kotak_neo: boolean; anthropic: boolean; telegram: boolean };
  markets: { exchange: "NSE" | "BSE" | "MCX"; open: boolean; phase: string }[];
  // Older backends omit it; treat that as "ok".
  ingest?: { status: "ok" | "blocked"; reason: string | null };
};

// GET /api/instruments -> Instrument[]
export type Instrument = {
  id: string; exchange: "NSE" | "BSE" | "MCX"; symbol: string; name: string;
  kind: "equity" | "index" | "future"; tradable: boolean; timeframes: string[];
  data: Record<string, { bars: number; first: number | null; last: number | null }>;
};

// GET /api/candles?instrument=&tf=&limit=500&end=<unix>
export type CandlesResponse = { instrument: string; tf: string; source: string; candles: Candle[]; forming: Candle | null };

// GET /api/news?instrument=&limit=50 -> NewsItem[], newest first
export type NewsItem = {
  id: string; title: string; url: string; source: string;
  published_at: number | null; fetched_at: number;
  instruments: string[];
  sentiment: number | null;
  sentiment_method: "lexicon" | "claude" | null;
  event_type: string | null; summary: string | null;
};

// POST /api/auth/fyers/code body; the contract leaves these two shapes unnamed.
export type FyersCodeRequest = { code: string };
// POST /api/auth/fyers/code and GET /api/auth/fyers/status
export type FyersStatus = { connected: boolean; expires_at: number | null };

// ---- Analytics routes ----

// GET /api/indicators/catalog
export type IndicatorInfo = { name: string; label: string; pane: "price" | "oscillator" | "volume"; group: "trend" | "momentum" | "volatility" | "volume" };

// GET /api/indicators?instrument=&tf=&names=&limit=500
export type IndicatorSeries = { name: string; label: string; pane: "price" | "oscillator" | "volume"; points: { time: number; value: number | null }[] };
export type IndicatorsResponse = { instrument: string; tf: string; series: IndicatorSeries[] };

// GET /api/patterns?instrument=&tf=&limit=200 -> PatternSignal[], newest first
export type ScoreStats = {
  horizon_bars: number; n: number; hit_rate: number; base_rate: number;
  ci_low: number; ci_high: number; posterior: number; q_value: number | null;
  expectancy_after_cost_pct: number | null; certified: boolean;
};
export type PatternSignal = {
  id: string;
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
export type Level = {
  price: number; label: string;
  kind: "pdh" | "pdl" | "pdc" | "swing_high" | "swing_low" | "vwap" | "pivot" | "r1" | "r2" | "s1" | "s2" | "cpr_top" | "cpr_bottom";
};
export type LevelsResponse = { instrument: string; tf: string; levels: Level[] };

// GET /api/forecast?instrument=&tf=&steps=3
export type Forecast = {
  instrument: string; tf: string; method: string;
  made_at: number; ref_time: number; ref_close: number; horizon_bars: number;
  p_up: number | null; p_up_ci: [number, number] | null; base_rate: number | null;
  abstain: boolean; abstain_reason: string | null; confidence: "low" | "medium" | "high" | null;
  expected_move_pct: number | null;
  ghost_candles: Candle[];
  bands: Band[];
  invalidation: number | null;
  drivers: { name: string; effect: Direction; detail: string }[];
  n_analogs: number;
  explanation: string | null;
};

// GET /api/scanner?tf=1D -> ScannerRow[], sorted by score, descending
export type ScannerRow = {
  instrument: string; name: string; tf: string; time: number;
  last_close: number; change_pct: number;
  p_up: number | null; base_rate: number | null; abstain: boolean;
  score: number;
  direction: Direction;
  top_signal: { label: string; state: "confirmed" | "forming"; certified: boolean } | null;
  rel_volume: number | null; trend: "up" | "down" | "sideways" | null;
};

// GET /api/scorecard?tf=1D&instrument=&pattern=&certified_only=false
export type ScorecardRow = {
  pattern: string; label: string; direction: Direction;
  instrument: string;
  context: string;
  horizon_bars: number; n: number; hits: number; hit_rate: number; base_rate: number;
  ci_low: number; ci_high: number; p_value: number; q_value: number; posterior: number;
  expectancy_after_cost_pct: number | null;
  validation_n: number; validation_hit_rate: number | null;
  certified: boolean;
};
export type ScorecardResponse = {
  meta: { tf: string; built_at: number | null; train_end: string; holdout_start: string; n_tests: number; fdr_alpha: number; horizons: number[] };
  rows: ScorecardRow[];
};

// GET /api/ledger?instrument=&tf=&status=&method=&limit=100 -> LedgerEntry[], newest first (all methods unless `method` is given)
export type StepGrade = {
  step: number; close_err_pct: number; close_err_atr: number; high_err_atr: number; low_err_atr: number;
  range_iou: number; body_iou: number; color_match: boolean; in_band_80: boolean;
};
export type LedgerEntry = {
  id: number; instrument: string; tf: string; method: string;
  made_at: number; ref_time: number; ref_close: number; horizon_bars: number;
  p_up: number | null; abstain: boolean;
  predicted: Candle[]; bands: Band[];
  actual: Candle[];
  status: "pending" | "graded" | "void";
  grade: { direction_hit: boolean | null; brier: number | null; match_score: number; steps: StepGrade[] } | null;
};

// GET /api/accuracy?instrument=&tf=&days=90&method=analog_v1
export type AccuracyResponse = {
  summary: {
    n_forecasts: number; n_graded: number; n_abstained: number;
    direction_hit_rate: number | null; brier: number | null; brier_baseline: number | null; skill: number | null;
    ece: number | null; band_coverage_80: number | null; mean_match_score: number | null; mean_close_err_atr: number | null;
  };
  // Always 10 bins; empty ones have n = 0 and null mean_pred / observed.
  calibration: { bin_low: number; bin_high: number; mean_pred: number | null; observed: number | null; n: number }[];
  rolling: { time: number; hit_rate: number | null; brier: number | null; match_score: number | null }[];
  by_group: { group_by: "instrument" | "tf" | "pattern" | "session_phase" | "vol_regime"; key: string; n: number; hit_rate: number | null; brier: number | null }[];
};
