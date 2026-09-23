# candly — Project Plan

Locked on 2026-09-23, when the Stage A build started. This file records what we decided and why. Items marked *(default)* are Claude's recommendation, not yet discussed in depth, and can be reopened. Evidence labels: **proven**, **plausible**, **speculative**.

## 1. What candly is

candly follows Indian markets (NSE, BSE, MCX). It detects candlestick patterns, both completed and still forming, and adds context: indicators, time of day, volatility regime, key price levels and news. Then it gives **probabilities** for the next candles:

- direction (the chance price is higher N candles from now),
- expected range, drawn as projected "ghost candles" with a 10–90% band,
- an invalidation level (the price that proves the idea wrong),
- a plain-language "why".

It is allowed to say **"no clear edge right now"** (abstain).

It is not an oracle. A real edge on the next candle is a small tilt, roughly 53–58% on selected setups, not 80%+.

**Success means** beating simple baselines out-of-sample, after trading costs, with calibrated probabilities (when it says 60%, it is right about 60% of the time). First in backtests, then in the live prediction ledger.

### Reality check

| Claim | Evidence |
|---|---|
| Next-candle direction on liquid instruments is close to a coin flip | proven |
| Volatility clusters, so the *size* of the next candles is forecastable even when direction is not | proven |
| Intraday volatility is U-shaped: highest near the open and the close | proven |
| Candlestick patterns alone have weak, unstable edge after costs; context carries most of any signal | plausible (we measure it on our own data) |
| Single indicator rules like "RSI < 30 = buy" make money after costs | speculative; the evidence is weak |
| An LLM can forecast prices directly | speculative. Its backtests are contaminated because the model has seen what happened next (that contamination is proven) |

## 2. Scope

| | Decision |
|---|---|
| Exchanges | NSE, BSE and MCX from day one |
| Timeframes | 5m, 15m, 1h, 1D |
| **Stage A** (now) | Analysis and alerts. No orders. |
| **Stage B** (later) | Automated trading: paper first, then live, SEBI-compliant |
| First validation slice | NSE daily on the watchlist stocks and indices. Its go/no-go comes before we trust intraday or MCX results |

Starting watchlist *(default; edit `config/watchlist.yaml`)*: Nifty 50, Bank Nifty, India VIX (context only), 10 liquid NSE stocks (Reliance, HDFC Bank, ICICI Bank, Infosys, TCS, SBI, Bharti Airtel, ITC, L&T, Axis Bank), Sensex, and MCX Crude Oil, Natural Gas, Gold, Silver (continuous front-month futures).

## 3. Environment

- Windows 10 laptop, VS Code + Claude Code, Python 3.12 (python.org), Node.js LTS, Git.
- **Seqrite Endpoint Security** kills shell processes that download programs: installers, zips with .exe/.dll inside, and Python packages with compiled parts. It once quarantined powershell.exe. Rules: never download binaries from the shell. Package installs happen in one planned session with Seqrite Behavior Detection paused by the user. The project, Python and Node folders are excluded.
- Stage B needs a static IP (SEBI rule, see §16), which a home connection normally does not have. Plan a VPS or an ISP static IP before Stage B.

## 4. Data

**Sources**

| Source | Role | Notes |
|---|---|---|
| Fyers API v3 | Primary: history and live candles for NSE, BSE, MCX; option chain | History: 100 days per request for intraday (minute data from 3 Jul 2017), 366 days per request for daily. Rate limits: 10/s, 200/min, 100,000/day; breaching the per-minute cap more than 3 times in a day blocks the account for the rest of that day, so we stay at 8/s and 150/min (checked 2026-09-23). `cont_flag` for continuous futures (verify). Needs a daily login token. |
| Kotak Neo | Stage B order execution; backup live quotes | Has a history endpoint, but users report 503/429 errors, so it is not our history source. TOTP login. |
| Yahoo Finance (yfinance) | Dev-only fallback until the Fyers keys arrive | NSE/BSE only. 5m/15m for the last 60 days only, 1h for the last 730 days. Unofficial and may break. Never used for final results. |
| Free RSS feeds | News (see §7) | |

**How we pull candles** *(default)*

- Fetch **5m** and build **15m** and **1h** from it, aligned to the session open. That means fewer API calls, and the timeframes always agree with each other.
- Fetch **1D** separately. NSE's official close is a volume-weighted average of the last 30 minutes, not the last 5m close, so daily bars must come from the daily endpoint.
- Backfill once, then update incrementally after every bar.

**Hygiene**

- Timestamps are stored in UTC and shown in IST. A candle's time is its **open** time.
- Checks on every save: OHLC consistency, no duplicate or out-of-order bars, bars only inside session hours.
- A missing-bar report per day, checked against the market calendar.
- Corporate actions (splits, bonuses): broker data is usually unadjusted. We detect suspicious overnight gaps and adjust from `config/corporate_actions.yaml` (to build).
- MCX: a continuous front-month series with roll dates marked, so no pattern ever spans a roll gap.
- Survivorship bias: the watchlist is today's large caps, which makes history look better than it was. Results on it are an upper bound, and every report says so.
- Calendar (`config/markets.yaml`): 2026 holidays; MCX closes at 23:30 during US daylight saving time and 23:55 otherwise; Muhurat session on 8 Nov 2026 (timing to be announced); expiry days are Tuesday for NSE index derivatives and Thursday for BSE (since 1 Sep 2025), moved to the previous trading day when they fall on a holiday.

**Storage**

- Candles: Parquet files, one per instrument and timeframe (`data/candles/{EXCHANGE}/{tf}/{SYMBOL}.parquet`), queried with pandas or DuckDB.
- Ledger, news, events and job runs: SQLite in WAL mode, under `data/db/`.
- **Log from day one**, because these can't be downloaded later: news with our fetch time, index option-chain snapshots (once the Fyers keys arrive), and every forecast.

**Charts as images:** raw OHLCV numbers are exact, while screenshots lose precision. Reading screenshots with Claude vision is not worth building *(default: skip)*.

## 5. Indicators

Computed from OHLCV and strictly **causal**: the value at candle *t* uses only candles up to *t*.

| Group | Indicators |
|---|---|
| Trend | EMA 9/20/50/200, SMA 20/50/200, Supertrend (10, 3), ADX/DMI (14) |
| Momentum | RSI (14), MACD (12, 26, 9), Stochastic (14, 3, 3) |
| Volatility | ATR (14), Bollinger Bands (20, 2), band width |
| Volume | Relative volume (intraday: vs the same time of day over the last 20 sessions; daily: vs the 20-day average), OBV, VWAP (intraday, resets daily) |
| Levels | Previous day high/low/close, classic pivots + CPR, recent swing highs/lows |

They are used in three ways:

1. **Context for patterns**, e.g. "hammer at support + RSI oversold + volume spike".
2. **Features for the model** (Phase 2).
3. **Chart overlays** that can be toggled in the UI.

Every indicator group has to earn its place in the model through ablation: switch it off, re-run walk-forward, and keep it only if out-of-sample results get worse without it. Groups that fail stay on the chart but leave the model.

## 6. Pattern engine

- Our own definitions, with thresholds in `config/patterns.yaml`. Thresholds are relative to ATR and to the candle's own range, so they work across instruments. TA-Lib is only a cross-check, because its thresholds are fixed and opaque.
- Starting set: hammer, inverted hammer, shooting star, hanging man, doji (standard, dragonfly, gravestone), bullish/bearish marubozu, bullish/bearish engulfing, piercing line, dark cloud cover, bullish/bearish harami, tweezer top/bottom, morning star, evening star, three white soldiers, three black crows, inside bar, outside bar.
- A pattern is **confirmed** once all its bars have closed. It is **forming** if it would complete should the current, still-open candle close right now. Forming signals are shown and alerted with a "forming" badge, never enter the scorecard, and the ledger tracks how often they actually complete.
- Every signal carries context tags: prior trend, volatility regime, location (near support/resistance, the previous day's high/low, VWAP), relative volume, higher-timeframe trend, session phase.
- Every signal carries an **invalidation level**: below the pattern's low for bullish reversals, above its high for bearish ones, with a buffer that is a fraction of ATR.

**Scorecard.** Built from our own history, not from textbook win rates.

- For each pattern × context bucket × timeframe (per symbol and pooled) and horizons of 1, 3 and 5 bars, it records:
  - sample size and hit rate,
  - the **base rate** (how often price rises anyway on that symbol and timeframe),
  - a Wilson 95% interval,
  - a binomial test against the base rate,
  - expectancy after costs.
- **Multiple testing:** Benjamini–Hochberg false-discovery control at q = 0.10 across *all* tests run. Every report states how many tests were run.
- **Shrinkage:** each bucket's success rate is a Beta-binomial posterior pulled towards the base rate, with a prior worth 20 observations. Small samples can't produce big claims.
- A bucket is **certified** only if n ≥ 30, q < 0.10, expectancy after costs is positive, and the edge points the same way in the validation period.

## 7. News and sentiment

- **Stage A sources (free, checked 2026-09-23):**
  - Economic Times (Markets, Stocks), Business Standard Markets, LiveMint Markets, Hindu BusinessLine Markets
  - NSE corporate announcements, NSE financial results, RBI press releases
  - a per-stock Google News query

  Moneycontrol RSS is stale (newest item April 2024) and Financial Express returned nothing, so both are dropped.
- Pipeline: fetch every 5 minutes → dedupe → map to instruments (name/alias dictionary) → relevance → sentiment and event type → a time-decayed score per instrument. Event types: results, rating change, order win, regulatory action, deal, management change, macro/RBI.
- **Timestamps:** every item stores the publisher's time *and* our fetch time. Backtests may only use an item from its **fetch time** onwards. Items older than the start of our logging are flagged and can only be used with a conservative next-session lag.
- Until Claude is wired in, fallback sentiment = a finance keyword lexicon plus regex rules for event types.
- Social media is noisy and hard to get legally in India, so it is skipped in Stage A *(default)*.
- Paid news APIs only if two weeks of RSS logging show gaps that matter.

## 8. Claude API: where it fits

| Job | Model | Why |
|---|---|---|
| Bulk news tagging into structured JSON (instruments, event type, sentiment, magnitude) | `claude-haiku-4-5` | You asked for smaller models on bulk work; high volume, simple extraction |
| Explanations, weighing conflicting signals, pre-market brief, post-market review | `claude-opus-5` | Low volume, quality matters |

- Claude **never produces the probabilities**; the statistical model does. Claude explains, adds context and flags conflicts. A Claude-assisted variant can be tested later, on forward data only.
- Structured outputs (`messages.parse` with a Pydantic schema), prompt caching of the fixed system prompt, and the Batch API (50% cheaper) for backfills.
- **Leakage:** Claude's training data includes what happened after old news, so LLM-derived features are only evaluated on news published after the model's training cutoff, and in forward testing.
- Every prompt and response is logged with its cost.
- Load the `claude-api` skill before writing any Claude API code; model IDs and API shapes change.

## 9. Forecasting

- **Phase 1, analog baseline:** find past situations in the same pattern/context bucket and take their next-N-bar outcomes. That gives p(up), a median path (the ghost candles) and 10/50/90% bands, all computed in ATR units and converted back to price.
- **Baselines it must beat:**
  - base rate (always predict the historical up-probability),
  - persistence (the next candle has the same direction as the last),
  - a random walk with historical-volatility bands.
- **Phase 2:** LightGBM on indicators, context and pattern scores, trained walk-forward, with quantile objectives for ranges. Deep sequence models only if they beat LightGBM out-of-sample.
- Pattern score, model and sentiment are combined by a learned stacking layer trained on out-of-fold predictions, never by hand-picked weights.
- **Abstain** when there are fewer than 30 analogs, when |p(up) − base rate| < 0.03, when data is stale, or when the market for that instrument is closed.

## 10. Expected vs actual candles (grading)

Every forecast is written to the **prediction ledger** at bar close, *before* the outcome exists. When the target bars close, it is graded automatically:

| Metric | Meaning |
|---|---|
| Direction hit | Did price end on the predicted side at the horizon? |
| Brier score | Squared error of the probability (lower is better). Compared with the base-rate baseline, it gives a skill score |
| Calibration (ECE) | Forecasts grouped by probability: does "60%" come true about 60% of the time? |
| Close / high / low error | In % and in ATR units, per step |
| Range overlap (IoU) | Overlap between the predicted and actual high–low range, 0–1 |
| Body overlap, colour match | Did the predicted body overlap the real one, and was the colour right? |
| Band coverage | Share of actual closes inside the 10–90% band (target ≈ 80%) |
| Match score | A 0–100 summary for the UI; the components are always shown too |

Results are broken down by instrument, timeframe, pattern, session phase and volatility regime. They appear as a rolling chart and as a table of predicted and actual candles side by side.

## 11. Learning from past records ("memory")

1. **The ledger is the memory.** Every forecast and its outcome is stored permanently.
2. **The scorecard updates** after every graded outcome, through the Beta-binomial posterior. It learns slowly and skeptically, so one lucky week can't flip it.
3. **Recalibration:** once there are enough graded forecasts, a calibration map (isotonic regression) is refit nightly on recent forecasts.
4. **Champion/challenger retraining (Phase 2):** the model is retrained weekly on a rolling window. The new model replaces the old one only if it does better on the most recent out-of-sample period. Every model version is saved with its metrics.
5. **Claude recall (Phase 4):** when explaining a new signal, Claude is shown how similar past setups actually played out ("last 14 similar setups: 8 worked, median move +0.6 ATR").
6. **What we won't do:** change the model after every trade. That just chases noise.

## 12. Validation protocol (non-negotiable)

- **Final holdout:** everything from **2025-10-01** onwards is locked. It is used once per go/no-go decision and never for tuning.
- **Fixed train/validation split** (revised 2026-09-23 after the quant audit, before any real run):
  - Headline statistics use bars before a fixed `train_end`: 2019-01-01 for daily, 2023-01-01 for intraday.
  - Validation is the walk-forward windows from `train_end` up to the holdout.
  - The dates are fixed so that a deeper backfill can't quietly move the split.
- **Cluster-robust significance:** overlapping horizons, clustered patterns and correlated instruments on the same day inflate naive p-values, which the audit measured at about 1.2–1.6× on pooled rows.
  - Events whose outcome windows overlap in time count as one cluster, and certification needs ≥ 30 clusters.
  - Context buckets are compared with their own bucket's base rate, not the unconditional one.
- **Walk-forward** on data before the holdout: expanding training window and 6-month test windows, with a purge and embargo of 10 bars between train and test so overlapping labels can't leak.
- **Lookahead audit:** every feature has a truncation test. Deleting or changing future candles must not change any past feature value. The quant-auditor agent reviews every change to features, labels or backtests.
- **Costs** from `config/costs.yaml`: brokerage, STT/CTT, exchange charges, SEBI fee, stamp duty, GST, plus slippage. F&O STT rose on 2026-04-01: futures 0.05%, options 0.15% of premium.
- **Gaps and circuit limits:** fills happen at the next available price, never at a price that didn't trade.
- **Pre-registration:** what we test and how we judge it is written in `config/research.yaml` *before* looking at results. Any later change is recorded here with a reason.
- **Paper trading** (a forward test through the ledger) comes before any real money.

## 13. Output and UX

- **Chart:**
  - candles and volume
  - pattern markers (forming ones hollow)
  - indicator overlays and key levels
  - ghost candles with a 10–90% cone, and the invalidation line
  - a "why" panel: probability, patterns with their scorecard stats, context, news, abstain reason
- **Scanner:** the watchlist ranked by how strong each setup is right now, per timeframe.
- **Scorecard page:** the pattern statistics table. The Phase 1 go/no-go is decided here.
- **Accuracy page:** predicted vs actual, calibration chart, rolling accuracy, tables by group.
- **News page:** a tagged, timestamped feed.
- **Alerts (Telegram):** new certified signal, forming signal close to completing, invalidation hit. A **pre-market brief** at 08:45 IST and a **post-market review** at 16:15 IST (predicted vs actual).
- **Risk helper on alerts:** stop at the invalidation level, a suggested position size from risk per trade (default 0.5% of capital, set by you), and a daily loss limit.

## 14. Architecture

```
Scheduler (market-calendar aware)
  ├─ Ingestion: Fyers / Yahoo (dev) candles ─► clean ─► Parquet
  ├─ News RSS ─► dedupe ─► map ─► sentiment ─► SQLite   (logged from day 1)
  ├─ Indicators ─┐
  ├─ Patterns ───┼─► Context ─► Scorecard ─► Forecast (analog → LightGBM) ─► abstain?
  ├─ Levels ─────┘                                  ├─► Prediction ledger ─► grader ─► accuracy + learning
  │                                                 └─► Claude synthesis (explains only)
  └─ FastAPI ─► React dashboard · Telegram alerts · briefs
```

| Layer | Stack |
|---|---|
| Backend | Python 3.12, pandas, NumPy, SciPy, PyArrow (Parquet), DuckDB, SQLite, FastAPI + Uvicorn, APScheduler, httpx, feedparser, pydantic-settings, yfinance (dev) |
| ML (Phase 2) | LightGBM, scikit-learn (calibration) |
| Frontend | Vite + React + TypeScript, Tailwind CSS, TradingView Lightweight Charts, TanStack Query, React Router |
| Quality | pytest, ruff, Vitest, ESLint |

- Every module can be switched off, so its value can be measured.
- Secrets live only in `.env`, never committed and never logged.
- The contracts between backend, quant and frontend are in `docs/CONTRACTS.md`.

## 15. Team (Claude Code subagents)

Defined in `.claude/agents/`:

| Agent | Owns |
|---|---|
| backend-engineer | Data platform: broker adapters, storage, news ingestion, scheduler, platform API routes |
| quant-engineer | Indicators, patterns, context, scorecard, forecasting, ledger and grading, analytics API routes |
| frontend-engineer | React dashboard |
| quant-auditor (read-only) | Lookahead, leakage, overfitting, cost realism, statistical claims |
| reviewer (read-only) | Bugs, security, contract drift, test gaps |
| trader-tester | Acceptance testing as a 20+ year Indian-markets trader and backend engineer: data truth, pattern and level sanity, costs, calendar realism, usefulness, API/job failure modes |

The main Claude session is the lead. It owns this plan, the contracts and the `core` package; it splits the work, integrates it, and runs the final checks.

## 16. Stage B: automated trading (not started)

- **SEBI retail algo framework,** mandatory since 2026-04-01:
  - orders only through the broker's API,
  - a unique exchange **Algo ID** on every order,
  - **static IP whitelisting**,
  - a kill switch, and broker-side logs kept for 5 years.

  Personal-use algos below the orders-per-second threshold are registered through the broker. Re-check the current rules before starting Stage B.
- Our own safeguards: paper mode by default, per-order and daily loss limits, maximum position size, an order-rate limiter, a kill switch, and every order linked to its ledger forecast.

## 17. Keys you need to provide

| Key | Needed for | When |
|---|---|---|
| Fyers App ID + Secret, redirect URL, your Fyers client ID | Real candles for all three exchanges | Now (until then, Yahoo covers only NSE/BSE) |
| Fyers PIN + TOTP secret | Automatic daily login | Optional; otherwise one browser login per day |
| Anthropic API key | News tagging, explanations | Phase 3 |
| Telegram bot token + chat ID | Alerts | Phase 4 |
| Kotak Neo consumer key/secret, mobile, UCC, MPIN, TOTP secret | Order execution | Stage B |
| Paid news API | Only if RSS proves insufficient | Later |

Put them in `.env` (a copy of `.env.example`). Never paste keys into the chat.

## 18. Budget (estimates)

| Item | ₹/month |
|---|---|
| Fyers / Kotak Neo APIs | 0 (verify that they are free for account holders) |
| Claude Haiku tagging (~1,500 headlines/day, batched and cached) | ~2,000 |
| Claude Opus explanations (~20/day) + two daily briefs | ~4,500 |
| Hosting | 0 on the laptop (a VPS is about 500–1,500 when needed) |
| **Total** | **~6,000–8,000** |

Claude prices as listed on 2026-09-23: Haiku 4.5 at $1/$5 and Opus 5 at $5/$25 per million input/output tokens. Treat this as R&D spend until the ledger shows the tool pays for itself.

## 19. Roadmap and go/no-go gates

| Phase | Content | Time (full-time) | Gate |
|---|---|---|---|
| 0 | Setup, foundation, contracts, agent team | 3–5 days | Tests green; data flowing (Yahoo first, then Fyers) |
| 1 | Candles for all instruments, indicators, patterns, scorecard, analog forecast, ledger and grading, news logging, dashboard v1 | 3–4 weeks | **Go/no-go #1** on NSE daily (below) |
| 2 | Intraday and MCX, context features, LightGBM, walk-forward, ablations | 3–4 weeks | The model beats the analog baseline out-of-sample, after costs |
| 3 | News pipeline with Claude tagging | 1–2 weeks | News features improve forward results, or they get switched off |
| 4 | Claude explanations, Telegram alerts, briefs | 1–2 weeks | Works end-to-end on live data |
| 5 | Paper trading, monitoring, retraining | 4–8 weeks minimum | The forward ledger matches the backtest within tolerance |
| Stage B | Automated trading | — | Only after Phase 5 passes |

That is about 14–18 weeks to a working alerts tool, and 5–6 months before trusting it with real money.

**Go/no-go #1 (NSE daily)** passes only if all three hold:

- at least one certified pattern/context bucket,
- the analog forecast's Brier skill against the base rate is above 0 on validation,
- calibration error (ECE) is below 0.03.

If it fails, we stop and rethink before building on it. Likely pivots: forecast ranges and volatility (which are forecastable) instead of direction, or drop patterns in favour of regime and trend signals.

## 20. Open questions

- Fyers symbol formats, and how `cont_flag` behaves for MCX continuous futures. Verify when the keys arrive.
- MCX holiday details per session: some NSE holidays keep the MCX evening session open. Verify against MCX circulars.
- Brokerage on your exact Fyers and Kotak plans (values marked `verify` in `config/costs.yaml`).
- Your capital and risk per trade, for the position-size helper.
