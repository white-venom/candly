import type {
  AccuracyResponse,
  Candle,
  CandlesResponse,
  ExpiryInfo,
  Forecast,
  Health,
  IndicatorInfo,
  Instrument,
  LedgerEntry,
  LevelsResponse,
  PatternSignal,
  ScannerRow,
} from "../api/types";

/** 2026-09-23 09:15 IST, the NSE session open, as UTC seconds. */
export const T0 = Date.UTC(2026, 8, 23, 3, 45) / 1000;
export const DAY = 86400;

export const health: Health = {
  status: "ok",
  version: "0.1.0",
  time: T0,
  data_source: "yahoo",
  keys: { fyers: true, fyers_connected: false, kotak_neo: false, anthropic: false, telegram: false },
  markets: [
    { exchange: "NSE", open: true, phase: "regular" },
    { exchange: "BSE", open: true, phase: "regular" },
    { exchange: "MCX", open: false, phase: "closed" },
  ],
  ingest: { status: "ok", reason: null },
};

/** Monthly stock expiry on the last Tuesday of September 2026, four trading days after T0. */
export const monthlyExpiry: ExpiryInfo = { next: "2026-09-29", kind: "monthly", days_to_expiry: 4, is_expiry_day: false };

export const instruments: Instrument[] = [
  {
    id: "NSE:RELIANCE",
    exchange: "NSE",
    symbol: "RELIANCE",
    name: "Reliance Industries",
    kind: "equity",
    tradable: true,
    timeframes: ["5m", "15m", "1h", "1D"],
    data: { "1D": { bars: 3, first: T0 - 2 * DAY, last: T0 } },
    expiry: monthlyExpiry,
  },
  {
    id: "MCX:CRUDEOIL",
    exchange: "MCX",
    symbol: "CRUDEOIL",
    name: "Crude Oil",
    kind: "future",
    tradable: true,
    timeframes: ["1D"],
    data: {},
    expiry: { next: "2026-10-19", kind: "contract", days_to_expiry: 17, is_expiry_day: false },
  },
];

export const nifty: Instrument = {
  id: "NSE:NIFTY50",
  exchange: "NSE",
  symbol: "NIFTY50",
  name: "Nifty 50",
  kind: "index",
  tradable: true,
  timeframes: ["5m", "15m", "1h", "1D"],
  data: { "1D": { bars: 3, first: T0 - 2 * DAY, last: T0 } },
  expiry: { next: "2026-09-24", kind: "weekly", days_to_expiry: 1, is_expiry_day: false },
};

/** Health while Fyers isn't connected, so data updates are on hold. */
export const pausedHealth: Health = {
  ...health,
  ingest: { status: "blocked", reason: "Fyers not connected — log in to resume data updates" },
};

export function candle(time: number, open: number, close: number, volume = 1000): Candle {
  return { time, open, high: Math.max(open, close) + 2, low: Math.min(open, close) - 2, close, volume };
}

export const candles: CandlesResponse = {
  instrument: "NSE:RELIANCE",
  tf: "1D",
  source: "yahoo",
  candles: [candle(T0 - 2 * DAY, 100, 104), candle(T0 - DAY, 104, 101), candle(T0, 101, 103)],
  forming: null,
};

export const forecast: Forecast = {
  instrument: "NSE:RELIANCE",
  tf: "1D",
  method: "analog_v1",
  made_at: T0 + 6 * 3600,
  ref_time: T0,
  ref_close: 103,
  horizon_bars: 3,
  p_up: 0.56,
  p_up_ci: [0.51, 0.61],
  base_rate: 0.52,
  abstain: false,
  abstain_reason: null,
  confidence: "medium",
  expected_move_pct: 0.8,
  ghost_candles: [candle(T0 + DAY, 103, 104, 0), candle(T0 + 2 * DAY, 104, 103.5, 0), candle(T0 + 3 * DAY, 103.5, 104.5, 0)],
  bands: [
    { time: T0 + DAY, p10: 101, p50: 104, p90: 106 },
    { time: T0 + 2 * DAY, p10: 100, p50: 103.5, p90: 107 },
    { time: T0 + 3 * DAY, p10: 99, p50: 104.5, p90: 109 },
  ],
  invalidation: 98.5,
  drivers: [{ name: "Hammer at support", effect: "bullish", detail: "n=64, hit 58%" }],
  n_analogs: 64,
  explanation: null,
  // entry = ref close, stop = invalidation, target = last p50; (104.5 − 103) / (103 − 98.5) = 0.33
  trade: { entry: 103, stop: 98.5, target: 104.5, reward_risk: 0.33 },
};

export const abstainingForecast: Forecast = {
  ...forecast,
  p_up: 0.51,
  base_rate: 0.505,
  abstain: true,
  abstain_reason: "|p(up) − base rate| < 0.03",
  confidence: null,
  trade: null,
};

export const levels: LevelsResponse = {
  instrument: "NSE:RELIANCE",
  tf: "1D",
  levels: [{ price: 110, label: "Prev day high", kind: "pdh" }],
  as_of: T0,
  stale: false,
};

export function signal(overrides: Partial<PatternSignal> = {}): PatternSignal {
  const time = overrides.time ?? T0;
  const pattern = overrides.pattern ?? "hammer";
  return {
    id: `NSE:RELIANCE|1D|${time}|${pattern}`,
    instrument: "NSE:RELIANCE",
    tf: "1D",
    time,
    pattern,
    label: "Hammer",
    direction: "bullish",
    state: "confirmed",
    bars: 1,
    invalidation: 98.5,
    context: {
      trend: "down",
      vol_regime: "normal",
      session_phase: null,
      rel_volume: 1.4,
      near_level: "pdl",
      rsi14: 31,
      expiry_day: false,
      days_to_expiry: 4,
    },
    stats: {
      horizon_bars: 3,
      n: 64,
      hit_rate: 0.58,
      base_rate: 0.52,
      ci_low: 0.46,
      ci_high: 0.69,
      posterior: 0.55,
      q_value: 0.08,
      expectancy_after_cost_pct: 0.12,
      certified: false,
    },
    ...overrides,
  };
}

export const catalog: IndicatorInfo[] = [
  { name: "ema20", label: "EMA 20", pane: "price", group: "trend" },
  { name: "ema50", label: "EMA 50", pane: "price", group: "trend" },
  { name: "rsi14", label: "RSI 14", pane: "oscillator", group: "momentum" },
  { name: "macd", label: "MACD", pane: "oscillator", group: "momentum" },
];

export function scannerRow(overrides: Partial<ScannerRow>): ScannerRow {
  return {
    instrument: "NSE:RELIANCE",
    name: "Reliance Industries",
    tf: "1D",
    time: T0,
    last_close: 103,
    change_pct: 1.2,
    p_up: 0.56,
    base_rate: 0.52,
    abstain: false,
    score: 0.04,
    direction: "bullish",
    top_signal: { label: "Hammer", state: "confirmed", certified: false },
    rel_volume: 1.4,
    trend: "down",
    expiry: monthlyExpiry,
    abstain_reason: null,
    ...overrides,
  };
}

type CalibrationBin = AccuracyResponse["calibration"][number];

/** The backend's 10 bins; any bin not filled in is empty (n = 0, nulls), as the API sends it. */
export function calibrationBins(filled: Record<number, Pick<CalibrationBin, "mean_pred" | "observed" | "n">>): CalibrationBin[] {
  return Array.from({ length: 10 }, (_, i) => ({
    bin_low: i / 10,
    bin_high: (i + 1) / 10,
    ...(filled[i] ?? { mean_pred: null, observed: null, n: 0 }),
  }));
}

export const accuracy: AccuracyResponse = {
  summary: {
    n_forecasts: 40,
    n_graded: 36,
    n_abstained: 12,
    direction_hit_rate: 0.54,
    brier: 0.2461,
    brier_baseline: 0.2497,
    skill: 0.0144,
    ece: 0.021,
    band_coverage_80: 0.78,
    mean_match_score: 61.2,
    mean_close_err_atr: 0.72,
  },
  calibration: calibrationBins({ 4: { mean_pred: 0.48, observed: 0.46, n: 10 }, 5: { mean_pred: 0.53, observed: 0.55, n: 14 } }),
  rolling: [
    { time: T0 - DAY, hit_rate: 0.53, brier: 0.247, match_score: 60 },
    { time: T0, hit_rate: 0.54, brier: 0.246, match_score: 61 },
  ],
  by_group: [{ group_by: "instrument", key: "NSE:RELIANCE", n: 36, hit_rate: 0.54, brier: 0.2461 }],
};

export const ledgerEntry: LedgerEntry = {
  id: 1,
  instrument: "NSE:RELIANCE",
  tf: "1D",
  method: "analog_v1",
  made_at: T0,
  ref_time: T0,
  ref_close: 103,
  horizon_bars: 1,
  p_up: 0.56,
  abstain: false,
  predicted: [candle(T0 + DAY, 103, 104, 0)],
  bands: [{ time: T0 + DAY, p10: 101, p50: 104, p90: 106 }],
  actual: [candle(T0 + DAY, 103, 102.5)],
  status: "graded",
  grade: {
    direction_hit: false,
    brier: 0.3136,
    match_score: 48,
    steps: [
      {
        step: 1,
        close_err_pct: -1.44,
        close_err_atr: 0.8,
        high_err_atr: 0.1,
        low_err_atr: 0.2,
        range_iou: 0.55,
        body_iou: 0,
        color_match: false,
        in_band_80: true,
      },
    ],
  },
};
