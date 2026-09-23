# First acceptance run — 2026-09-23

Tester: trader-tester. Code: commit c0593aa, API at http://127.0.0.1:8000 (scheduler off).
Data: Yahoo dev data only (NSE/BSE 1D since 2005, 1h since 2024-09-30). No MCX data, no scorecard (503, expected), empty ledger, `ingest: blocked` (Fyers not logged in, expected).
Run at 2026-09-23 about 13:15 IST, during the NSE session. Times are IST unless marked UTC. Scratch scripts are not committed; every number below can be reproduced from `data/candles` and the GET endpoints. The key invariants are encoded in `backend/tests/acceptance/` (§11).

## 1. Verdict (plain trader language)

**I'd trust candly today for the numbers:** daily closes, split/bonus-adjusted history, and every indicator and pivot level on the chart. They match the exchange closes and textbook formulas to the paisa.

**I would not yet trust it for trade calls or intraday levels:**
- Index daily data is one session behind (22 Sep missing), and the levels panel still labels 21 Sep's high/low/close as "previous day" without a warning.
- The Sunday 1 Feb 2026 Budget session is missing from every series.
- Stops on inverted-hammer and hanging-man signals sit inside normal noise.
- The only two live calls (RELIANCE bullish, AXISBANK bearish) come with no stop at all.
- After realistic costs, a 1h edge needs a hit rate of about 61–63% to break even, far above the 53–58% the plan hopes for.

Treat the dashboard as a well-computed chart plus a pattern scanner, not a signal service, until the fixes below and the Fyers backfill are in.

## 2. Checks table

| Area | Check | Result | Evidence |
|---|---|---|---|
| Data | 1D closes vs official closes (16 checks, 2020–2026) | pass | §3.1: all exact |
| Data | Split/bonus adjustment (RELIANCE 2017/2024, HDFCBANK 2011/2019/2025, TCS 2009/2018) | pass | §3.2 |
| Data | 1D freshness: every 1h session has a 1D bar | **fail** | NIFTY50, BANKNIFTY, SENSEX, INDIAVIX and LT 1D end 2026-09-21; 1h has 2026-09-22 |
| Data | Special sessions present (Budget Sun 2026-02-01) | **fail** | absent from every 1D and 1h series; calendar says closed |
| Data | No bars outside the session grid, none on 2026 holidays, 1D ts = 09:15 | pass | a1/a2 scans of all 28 series |
| Data | 1h bars per session (7 expected, last one 15 min) | warn | NIFTY 485/489 full; TCS 473/488 full; stocks lost the 15:15 bar on many recent days (TCS: 11 sessions since 2026-08-11) |
| Data | 1h volume | **fail** | 09:15 bar volume = 0 on 425/487 RELIANCE sessions; today's live 09:15 gets rel_volume 25.4× |
| Data | 15:15 1h bar is a real bar | warn | last 25 sessions: 23–25 of the 15:15 bars have body = range (synthetic open→official-close stub) |
| Data | Spike/bad-bar scan | **fail** | LT 2006-09-27 at half price: gaps −68.5% then +73.3% |
| Data | Index volume | warn | BANKNIFTY 1D volume zero on 39–98% of days in 2015–2021; index rel_volume is shown anyway |
| Patterns | 32 recent signals inspected by eye (5 instruments, 1D + 1h) | pass (warn) | 28 true-looking, 4 doubtful (§4.1) |
| Patterns | Signal frequency | warn | 1D: 8.8 signals/instrument/month (36% of bars), 76% neutral; 1h: 80/month (46% of bars) |
| Patterns | Confirmed only at bar close | pass | latest 1D signal 2026-09-22; latest 1h 10:15 bar (closed 11:15); no forming rows without a live feed |
| Levels | PDH/PDL/PDC, pivot, R1/R2/S1/S2, CPR recomputed by hand | pass | diff 0.000000 on NIFTY50/RELIANCE/SENSEX 1D and NIFTY50/RELIANCE 1h |
| Indicators | RSI14 (Wilder), ATR14, MACD(12,26,9), Supertrend(10,3) | pass | independent SMA-seeded implementation, rel. diff < 1e-15 |
| Levels | "Prev day" levels are from the previous session | **fail** | NIFTY 1D levels use 2026-09-21 on 2026-09-23 (PDC 23,414.30); 1h levels say PDC 23,329.00 |
| Forecast | Abstains when it should | pass | all 1h abstain (stale, ingest blocked); indices 1D abstain (stale); HDFCBANK too few analogs; 5 below min edge |
| Forecast | Invalidation present and sane (0.3–3 ATR) | **fail** | both live calls have `invalidation: null`; pattern stops: inverted hammer/hanging man 66–70% < 0.3 ATR |
| Forecast | Ghost candles look real | warn | ranges fine (0.74–0.90 ATR vs real median 0.80–0.88); bodies 0.01–0.08 ATR vs real 0.35–0.39 (all dojis) |
| Forecast | Probability not overconfident | pass (warn) | RELIANCE p_up 0.554, CI [0.505, 0.602], label "low"; but the bucket (trend=down, any bar) is unvalidated |
| Costs | costs.yaml vs 2026 rates | warn | statutory rates match; Fyers delivery brokerage is ₹20/0.3% (config: free); clearing charges missing |
| Costs | Would a 1h intraday edge survive? | **fail** | round trip 0.142% at ₹1 lakh; breakeven hit rate 0.61–0.63 for a 3-bar 1h call |
| Calendar | 2026 holidays vs NSE list | pass | 16/16 dates match |
| Calendar | Expiry rules incl. holiday shifts | pass | 12/12 cases (NSE Tue, BSE Thu, monthly last) |
| Calendar | MCX close 23:30 (US DST) / 23:55 | pass | switches on 2026-03-09 and 2026-11-02 |
| Calendar | MCX evening sessions on NSE holidays | warn | 11 of 2026 holidays keep MCX evening open; config marks them fully closed |
| Calendar | Muhurat | warn | 2026-11-08 timing null → closed (correct); historical Muhurat bars in stock 1D but not index 1D |
| Engineering | Contract shapes, every GET endpoint | pass | 0 key mismatches (b7) |
| Engineering | Error codes 400/404/503 with `{detail}` | pass | 30/30 cases |
| Engineering | Host header enforcement | pass | evil.example.com, 127.0.0.1.nip.io, attacker.localhost → 400 |
| Engineering | Latency | pass (warn) | chart endpoints 15–140 ms warm; indicators all-series limit 5000 = 650 ms; scanner 1D 0.9 s median (5.0 s worst), 1h 1.6 s |
| Engineering | `ingest: blocked` reporting | pass | status blocked, reason "Fyers not connected — log in to resume data updates" |
| Security | Secrets in `/api/*` responses and `data/logs/*.log` | pass | 0 hits in 80 saved responses and 5 log files (JWT, bearer, auth_code, access_token, sk-ant, .env values) |
| Security | Nothing can place orders | pass | only non-GET route is POST /api/auth/fyers/code |

## 3. Data truth

### 3.1 Daily closes vs official closes

| Instrument | Date | Stored close | Official close | Source | Result |
|---|---|---|---|---|---|
| NIFTY50 | 2026-09-21 | 23,414.30 | 23,414.30 | Business Standard close report | pass |
| NIFTY50 | 2026-09-18 | 23,346.40 | 23,346.40 (23,414.30 − 67.90) | Business Standard | pass |
| NIFTY50 | 2026-02-02 | 25,088.40 | 25,088.40 | ETV Bharat (PTI) | pass |
| NIFTY50 | 2024-06-04 | 21,884.50 | 21,884.50 | Business Standard | pass |
| NIFTY50 | 2020-03-23 | 7,610.25 | 7,610.25 | Zee News | pass |
| SENSEX | 2026-09-21 | 74,858.99 | 74,858.99 | Business Standard / BBN Times | pass |
| SENSEX | 2026-09-18 | 74,294.96 | 74,294.96 (74,858.99 − 564.03) | Business Standard | pass |
| SENSEX | 2026-02-02 | 81,666.46 | 81,666.46 | ETV Bharat (PTI) | pass |
| SENSEX | 2024-06-04 | 72,079.05 | 72,079.05 | Business Standard | pass |
| SENSEX | 2020-03-23 | 25,981.24 | 25,981.24 | Zee News | pass |
| BANKNIFTY | 2026-09-21 | 56,470.65 | 56,470.65 (56,215.55 + 255.10) | Business Standard | pass |
| RELIANCE | 2026-09-22 | H 1251.9 L 1237.4 C 1240.4 | H 1251.9 L 1237.4 C 1240.4 | INDmoney / Tickertape | pass |
| HDFCBANK | 2026-09-22 | 738.60 | 738.60 | 5paisa | pass |
| HDFCBANK | 2026-09-21 | 739.50 | ₹740 (rounded prev close) | 5paisa | pass |
| TCS | 2026-09-22 | 2,105.00 | 2,105.00 | 5paisa | pass |
| TCS | 2026-09-21 | 2,128.70 | 2,128.6 (2,105 / (1 − 1.11%)) | 5paisa | pass |

Yahoo's daily close is the official (VWAP-based) exchange close on every date checked.

**The 1h bars are a different story.** The close of the last 1h bar is the last traded price, not the official close. Measured over about 485 full sessions, the gap between the 1h session close and the official 1D close was:

- median 4–5 bp on NIFTY50, BANKNIFTY and the stocks;
- up to 50 bp on NIFTY and 101 bp on INFY;
- exactly 0 on SENSEX.

For the most recent sessions Yahoo's 15:15 bar ends exactly on the official close (NIFTY 2026-09-22 1h close 23,329.00 = official). See §3.3 D6.

### 3.2 Splits and bonuses

| Event | Stored | Official | Result |
|---|---|---|---|
| RELIANCE 1:1 bonus, ex 2024-10-28 | 2024-10-25 close 1,327.85; 2024-10-28 open 1,337.00 | 2,655.70 / 2 = 1,327.85; open 1,337.00 (Angel One) | pass |
| HDFCBANK 1:1 bonus, ex 2025-08-26 | 2025-08-25 close 982.05 | BSE 1,964.50 / 2 = 982.25 (HDFC Sky, Business Today) | pass (2 bp, NSE vs BSE) |
| TCS 1:1 bonus 2018-05-31 and 2009-06-16 | 2018-05-30 → 05-31 open −1.3%; 2009-06-15 → 06-16 open −1.5% | adjusted series | pass |
| HDFCBANK splits 2019-09-19 and 2011-07-14 | open gaps +0.6% / +0.2% | adjusted series | pass |
| RELIANCE 1:1 bonus 2017-09-07 | open gap +0.04% | adjusted series | pass |

Apart from the LT bar below, the largest overnight gaps on the 10 stocks are all real 2005–2010 events of at most about 20%.

### 3.3 Data defects found

- **D1. Index and LT 1D one session stale.**
  - NIFTY50, BANKNIFTY, SENSEX, INDIAVIX and LT 1D end on 2026-09-21. The other stocks end on 2026-09-22, and the 1h series of all of them have 2026-09-22 (7 bars) and 2026-09-23.
  - The ingest log at 12:14 IST shows Yahoo returned no 2026-09-22 daily row for these five.
  - The real 2026-09-22 closes, missing from the store, are NIFTY 23,329.00, SENSEX 74,529.08 and BANKNIFTY 56,215.55 (Business Standard).
- **D2. Sunday 2026-02-01 Union Budget session missing everywhere.**
  - That day NSE, BSE and MCX ran a full 09:15–15:30 session (ICICIdirect, Business Standard). NIFTY closed 24,825.45, −1.96%.
  - No stored 1D or 1h series has that date. The 1h series also lack 2026-02-02.
  - `markets.yaml` has no special session for the date, so `is_trading_day(NSE, 2026-02-01)` is False and the missing-bar report cannot flag it.
  - The 1D chart therefore shows a fake −2.1% gap from 01-30 (25,320.65) to the 02-02 open (24,796.50).
  - The Saturday 2025-02-01 Budget session is in NIFTY 1D but missing from BANKNIFTY and SENSEX 1D (both have it in 1h).
  - Other real weekend sessions are also missing from every stored 1D series: the full Saturday session on 2024-01-20 (Zerodha bulletin, Zee Business), and the Budget Saturdays 2020-02-01 and 2015-02-28.
  - INDIAVIX 1D also lacks 2025-01-01 and 2026-01-01, which its 1h series has.
  - Yahoo drops weekend and special sessions unevenly across instruments.
- **D3. LT 2006-09-27 bad bar.**
  - Stored bar: O 146.67 H 147.15 L 143.52 C 143.87, between 2006-09-26 C 291.03 and 2006-09-28 O 299.31.
  - Stored as fact, this makes a −68.5% gap followed by a +73.3% gap inside the 1D training period. ATR(14) and the 252-day vol regime stay distorted for weeks, and labels and patterns around it are fake.
  - `clean.py` has no spike filter (PLAN §4 "detect suspicious overnight gaps" is not built).
- **D4. The 09:15 1h volume is zero on most historical sessions.**
  - Zero-volume 09:15 bars: RELIANCE 425 of 487 sessions, TCS 424 of 488 (the only other zero-volume slot is 15:15, with 13–16 bars).
  - Today's live 09:15 bar has volume, so `rel_volume` for RELIANCE 2026-09-23 09:15 = **25.4×**, a fake volume spike. The 09:15 slot's rel_volume is NaN on 28–35% of sessions and 0 at the median.
  - The historical session VWAP ignores the busiest hour: median 7–8 bp and p90 23–29 bp away from a VWAP that includes it.
- **D5. Missing 15:15 1h bars on recent stock sessions.** TCS lacks the 15:15 bar on 11 sessions between 2026-08-11 and 2026-09-18, RELIANCE on 4 and HDFCBANK on 2. They are most likely flat zero-volume stubs that the new cleaner dropped (`clean.py:27` `fake_bar_mask`: "any flat bar with zero volume" for equities). Other gaps:
  - 2026-04-20: 2–3 bars on every instrument;
  - 2026-01-13: NIFTY missing 12:15;
  - 2025-09-16: TCS, HDFCBANK and SENSEX missing mid-day bars.
- **D6. The 15:15 bar is a synthetic stub on recent sessions.** In the last 25 sessions, 23 of NIFTY's 15:15 bars have body == range, as do 25 of RELIANCE's and 24 of TCS's; in earlier history the share is about 5–7%. The stub opens at the 15:15 price and closes at the official close.
- **D7. Muhurat and special sessions are inconsistent across instruments.**
  - Stocks' 1D has Muhurat bars for 2019-10-27, 2020-11-14, 2021-11-04, 2022-10-24, 2024-11-01 and 2025-10-21 (ranges 0.24–1.2%).
  - NIFTY and SENSEX 1D lack the 2019 and 2020 ones, and 2023-11-12 is absent everywhere.
  - 1h has 2025-10-21 (2 bars) but not 2024-11-01.
- **D8. Index volume.**
  - BANKNIFTY 1D volume is zero on 39–98% of days per year in 2015–2021, and NIFTY 1D volume is not exchange turnover.
  - Index `rel_volume` is still shown: the 1D scanner shows NIFTY relvol 0.71.
- **D9. Clipped index wicks in 2026.** 2026 index daily bars have low == close on 2–3% of days, against 0% in every earlier year. Wicks are slightly clipped, for example NIFTY 2026-09-15 L = C = 23,118.60. This is minor for patterns.

## 4. Patterns

### 4.1 Recent signals inspected against the raw candles

32 signals, the newest on each of NIFTY50, RELIANCE, HDFCBANK, TCS and BANKNIFTY 1D, plus RELIANCE and NIFTY50 1h. For each I judged the shape and the context (prior move over 5 bars in ATR, trend).

| Pattern | True-looking | Doubtful | Notes |
|---|---|---|---|
| Inside bar | 7 | 0 | all clean, e.g. TCS 2026-09-22 (H 2135.2 < 2145.0, L 2098.2 > 2075.3) |
| Outside bar | 3 | 3 | TCS 1D 2026-09-11 is "outside" by only 0.6 points (H 2232.6 vs 2232.0); RELIANCE 1h 2026-09-22 09:15 and 2026-09-23 09:15 are "outside" a 15-minute 15:15 stub across the overnight gap (stub range 0.14 ATR) |
| Doji | 2 | 0 | NIFTY 2026-09-16 body/range 0.09; RELIANCE 2026-09-17 0.06 |
| Inverted hammer | 4 | 0 | shapes correct after declines (RELIANCE 2026-09-16, TCS 2026-09-17, TCS 2026-09-11 body 0.32 is borderline) |
| Shooting star / gravestone doji | 2 | 0 | HDFCBANK 2026-09-22: upper wick 0.98 of range after +2.3 ATR rise, at CPR top. The best signal of the set |
| Bullish harami | 2 | 1 | RELIANCE 2026-09-16: the "bullish" second candle is red with a 0.80 upper wick (sellers). The code does not require a bullish second body |
| Bearish harami | 1 | 0 | HDFCBANK 2026-09-22: body inside the body, but the high breaks the mother bar's high. A trader reads it as a shooting star |
| Tweezer bottom | 2 | 0 | NIFTY 2026-09-16 (lows 23,118.60/23,116.10), BANKNIFTY 2026-09-16 (55,794.75/55,812.20). Both depend on the clipped 9-15 wick (D9) |
| Bearish marubozu | 2 | 0 | NIFTY 2026-09-15 body 0.96 of range, 2.67 ATR; TCS 2026-09-18 |
| Bullish marubozu (1h) | 2 | 0 | RELIANCE and NIFTY 2026-09-23 10:15 |
| Bullish engulfing (1h) | 1 | 0 | NIFTY 2026-09-22 13:15, valid but engulfs a 0.3 ATR body |
| **Total** | **28** | **4** | |

Definitions are right. The problems are in how signals are reported:

- **Stacking.** 1.2 patterns per signalling bar. HDFCBANK 2026-09-22 raises a bearish harami, a shooting star and a gravestone doji on one candle, and RELIANCE 2026-09-16 raises an inside bar, a bullish harami and an inverted hammer.
- **Session boundaries.** 1h two-bar patterns are compared across the overnight gap against the 15-minute 15:15 stub.
- **The 15:15 slot is the noisiest.** It produces the most 1h signals of any slot: 4,489 against 2,976–4,021 for the others.

### 4.2 Frequency (since 2024-10-01, 13 NSE/BSE instruments)

| TF | Signals / instrument / month | Directional / month | Bars with any signal | Bars with a directional signal |
|---|---|---|---|---|
| 1D | 8.8 (7.6–9.4) | 2.1 (1.9–2.5) | 36% | 9% |
| 1h | 80 (76–85) | 21 | 46% | 13% |

- 1D mix: inside bar 30%, outside bar 24%, doji 22%. Everything else is 2.4% or less per pattern.
- Neutral patterns are three quarters of the flow and carry no invalidation.
- On 1D, two directional signals a month per name is a sane rate. On 1h, 21 a month per name across 13 names is about 270 alerts a month: alert fatigue.

## 5. Levels and indicators

I wrote independent implementations with no candly code, using TradingView-style seeds:

- RMA (Wilder smoothing) seeded with an SMA;
- EMA seeded with an SMA;
- Supertrend with final-band carry-forward.

I then compared them with `/api/indicators` and `/api/levels` on the last bar.

| Series | NIFTY50 1D (2026-09-21) | RELIANCE 1D (2026-09-22) | SENSEX 1D | NIFTY50 1h (09-23 10:15) | RELIANCE 1h |
|---|---|---|---|---|---|
| RSI14 | 36.8864 = | 38.5834 = | 37.9677 = | 57.5650 = | 52.2424 = |
| ATR14 | 186.4253 = | 20.4808 = | 643.1634 = | 60.1162 = | 6.2231 = |
| MACD / signal / hist | −246.10 / −209.15 / −36.95 = | −18.13 / −12.97 / −5.16 = | = | 11.03 / 13.12 / −2.09 = | = |
| Supertrend(10,3) | 23,804.60, down = | 1,298.86, down = | 76,279.78, down = | 23,308.81, up = | 1,254.49, down = |
| Pivot / R1 / S1 / R2 / S2 | 23,398.63 / 23,482.47 / 23,330.47 / 23,550.63 / 23,246.63 = | 1,243.23 / 1,249.07 / 1,234.57 / 1,257.73 / 1,228.73 = | = | 23,367.98 / 23,450.02 / 23,246.97 / 23,571.03 / 23,164.93 = | = |
| CPR top / bottom | 23,406.47 / 23,390.80 = | 1,244.65 / 1,241.82 = | = | 23,387.47 / 23,348.49 = | = |
| VWAP (1h, today) | n/a (index, no volume, omitted) | — | — | omitted | 1,243.3278 = |

"=" means the absolute difference was 0.000000 (relative < 1e-15).

**Convention notes against TradingView and broker charts:**

- **RSI and ATR.** Wilder RMA, the same as TradingView `ta.rsi`/`ta.atr`. candly seeds with the first value, not an SMA. The two agree once there are more than about 100 bars; on short histories they would differ.
- **MACD.** EMA 12/26 with a 9-EMA signal, the same as TradingView.
- **Supertrend.** hl2 ± 3 × RMA-ATR(10), flipping on close, the same as the TradingView built-in.
- **Pivots.** Classic/"Traditional" (R2/S2 = P ± (H−L)). CPR is TC = 2P − BC and BC = (H+L)/2, swapped when TC < BC.
- **Intraday PDH/PDL/PDC** come from 1h bars (last trade), not the official daily bar. TradingView pivots on an intraday chart use the daily bar.
  - Example: RELIANCE PDL from 1h is 1,237.70, but the official 2026-09-22 low is 1,237.40.
  - The PDC error was up to 50–100 bp on older sessions (§3.1).
- **VWAP.** hlc3 × volume per 1h bar, the same as TradingView on a 1h chart. A broker VWAP is tick-based. Historical sessions are also biased by D4.
- **The 1D and 1h level sets disagree.** On 2026-09-23 the 1D levels for NIFTY say PDC 23,414.30 (the 21 Sep bar, because of D1) while the 1h levels say PDC 23,329.00 (22 Sep). A trader sees two different "previous day closes".

## 6. Forecasts and scanner from a trader's view

State on 2026-09-23 about 13:17 IST:
- 1h: all 14 instruments abstain with "stale data" (ingest blocked). Correct.
- 1D: indices and LT abstain as stale (D1); HDFCBANK has too few analogs (18); ICICIBANK, INFY, SBIN, TCS, BHARTIARTL and ITC are below the minimum edge.
- **Two live calls:**
  - RELIANCE bullish: p_up 0.554 vs base 0.520, CI [0.505, 0.602], confidence low, 1,163 analogs "trend=down, any bar";
  - AXISBANK bearish: p_up 0.497 vs 0.529, outside bar.

- **Invalidation.** Both live calls have `invalidation: null`. The forecast only takes a stop from a directional pattern in the call's direction (`forecast/analog.py:277`). With no pattern, or only a neutral one, there is no stop, so a trader gets a direction with nowhere to be wrong.
  - Pattern-level stops (pattern extreme ± 0.1 ATR), distance from the signal close in ATR, all 13 instruments and full history:

    | Pattern | 1D median | 1D share < 0.3 ATR | 1h median | 1h share < 0.3 ATR |
    |---|---|---|---|---|
    | Inverted hammer | 0.25 | **66%** | 0.23 | **68%** |
    | Hanging man | 0.25 | **69%** | 0.22 | **70%** |
    | Tweezer top/bottom, harami | 0.56–0.73 | 2–8% | | |
    | Engulfing, marubozu, stars | 1.0–1.5 | 0% | | |
    | Three soldiers/crows | 2.1–2.7 | 0% | | 30–36% above 3 ATR on 1h (too wide) |

    No stop is ever on the wrong side of the close.
- **Ghost candles.** The ranges look real: 0.74–0.90 ATR per step against a real median of 0.80–0.88 ATR (last 250 bars). The p10–p90 band widens properly, from 1.4–1.7 ATR at step 1 to 2.5–3.0 ATR at step 3. But the bodies are 0.01–0.08 ATR against a real median of 0.35–0.39 ATR: every ghost candle is a doji, because O/H/L/C medians are taken independently. That reads as "indecision" on the chart, which is not what the model says.
- **Probability.** Not overconfident on its face: 55.4% with a ±5-point CI and a "low" label. But the call is a pure mean-reversion bet on the whole trend=down bucket (any bar), which no scorecard has validated yet, and it contradicts the "Trend: bearish" driver shown right under it with no explanation. I would not show a direction from an unvalidated bucket.
- **Abstaining.** Correct and disciplined: stale data, too few analogs and minimum edge all work. Weakness: the scanner row has no `abstain_reason`, so "stale" and "no edge" look identical (score 0, neutral).
- **The "why".** Analog count, hits, posterior against base rate, trend (ADX), vol percentile, and nearby level. That is useful to a quant but thin for a trader. Problems:
  - internal names ("near cpr_bottom");
  - "21th percentile";
  - no mention that the call is counter-trend.
- **Missing for a trader:**
  - entry (next open? limit?);
  - a stop for every call;
  - target(s) derived from the bands;
  - R:R;
  - position size from risk per trade (PLAN §13 "risk helper");
  - validity time ("valid until 3 bars");
  - the cost hurdle;
  - an expiry-day or event flag.
- **Scanner.**
  - It ranks rows with different reference dates together (indices 09-21, stocks 09-22) and shows the stale day's `change_pct` as if current.
  - It includes INDIAVIX (`tradable: false`).
  - MCX is silently absent.
  - Latency is 0.9–1.6 s median.

## 7. Costs

`config/costs.yaml` was checked against the Fyers charges page and exchange circular summaries (2026):

| Item | Config | Current | Result |
|---|---|---|---|
| Equity intraday STT | 0.025% sell | 0.025% sell | pass |
| Equity delivery STT | 0.1% buy + sell | 0.1% buy + sell | pass |
| Futures STT | 0.05% sell | 0.05% sell (from 2026-04-01) | pass |
| Options STT | 0.15% premium sell | 0.15% | pass |
| NSE exchange txn, cash | 0.00297% | 0.00297% + clearing = 0.0030699% (Fyers) | warn (clearing missing, −0.0001%) |
| NSE futures / options txn | 0.00173% / 0.03503% | 0.0018299% / 0.0355299% incl. clearing | warn |
| Stamp duty | 0.003% / 0.015% / 0.002% / 0.003% buy | same | pass |
| SEBI fee | ₹10/crore | ₹10/crore | pass |
| GST | 18% on brokerage + txn + SEBI | 18% on brokerage + txn + clearing + SEBI + IPFT | pass (IPFT negligible) |
| MCX CTT / txn / stamp | 0.01% sell / 0.0021% / 0.002% buy | 0.01% / 0.0021% / 0.002% | pass |
| Fyers intraday / futures brokerage | ₹20 or 0.03% | ₹20 or 0.03% | pass |
| **Fyers delivery brokerage** | **free** | **₹20 or 0.3% per order** + DP ₹12.5 + GST per sell | **fail**: understates the round trip by 0.055% at ₹1 lakh, 0.011% at ₹5 lakh |

Round trip from candly's own `cost_breakdown`:

| Trade | Notional | Total | Excluding slippage |
|---|---|---|---|
| Equity intraday | ₹1 lakh | 0.142% | 0.082% |
| Equity intraday | ₹5 lakh | 0.105% | |
| Equity delivery | ₹1 lakh | 0.282% (0.337% with the Fyers fix) | |
| NIFTY futures intraday | ₹15.2 lakh | 0.099% | |
| MCX futures | ₹6 lakh | 0.085% | |

**Would a 1h intraday edge survive? No.**
- A 3-bar 1h call on RELIANCE, HDFCBANK, TCS or SBIN has E|ret| 0.56–0.68%. The gross edge of a directional call is (2p−1)·E|ret|, so breakeven needs p = **0.61–0.63** (1-bar: 0.71–0.74). At p = 0.55 the gross edge is 0.06% against a 0.142% cost.
- 1D, 3-day delivery: E|ret| 1.6–1.9%, breakeven p = 0.57–0.59.
- A 3-bar 1h horizon starting at 13:15 or later ends the next day, so it is not an MIS (intraday) trade. Intraday cost rates don't apply to it.

## 8. Calendar realism

- **2026 holidays.** All 16 dates in `markets.yaml` match the published NSE/BSE list (Zerodha holiday calendar, NSE circular CMTR/71775).
- **Expiry rules.** 12/12 checks pass, including holiday shifts:
  - NSE weekly Tue: 03-03 → 03-02, 04-14 → 04-13, 10-20 → 10-19, 11-10 → 11-09;
  - NSE monthly: last Tue 03-31 → 03-30, 11-24 → 11-23;
  - BSE Thu: 01-15 → 01-14, 03-26 → 03-25; BSE monthly 05-28 → 05-27.

  Two gaps:
  - Nothing in features, context or forecasts uses expiry days. A trader wants an "expiry day" tag.
  - BANKNIFTY has had monthly expiries only since Nov 2024, but the rule is per exchange.
- **Special sessions.**
  - Budget Sunday 2026-02-01 is missing from `special_sessions` (D2).
  - Muhurat 2026-11-08 has null timing and is treated as closed until the circular. That is correct and conservative.
  - Historical Muhurat and Budget sessions are not listed, so they are neither expected nor tagged.
- **MCX.**
  - The close switches between 23:30 (US DST) and 23:55 correctly: 2026-03-06 23:55, 03-09 23:30, 10-30 23:30, 11-02 23:55.
  - Phases at 23:29/23:30 and 23:54/23:55 are correct.
  - 11 of the 2026 holidays keep the MCX evening session open, but config marks every holiday fully closed for MCX (already a PLAN open question). Once Fyers MCX data arrives, those evenings will be "closed" for the stale check, the ghost-candle times and the health phase.
- **The 15:15 1h bar.** `bar_close_time(15:15, 1h)` is 15:30, as the contract says. Phases at 15:29 are "close/open" and at 15:30 "closed", both correct.
- **Gap and circuit days.** Nothing assumes fills yet (no backtest was run). `trade_ret` in `labels.py` enters at the next open, which respects gaps.

## 9. Engineering

- **Contract shapes.** Every GET endpoint returns exactly the keys in docs/CONTRACTS.md: health, instruments, candles, news, catalog, indicators, patterns (+ context, id format, newest first), levels, forecast (+ bands, drivers, ghost volume 0), scanner (sorted by score), accuracy (10 calibration bins), ledger. There are no extra or missing keys.
- **Errors.** 30/30 cases return the right code with `{detail: string}`:
  - bad tf, limit and steps, unknown indicator, missing params and bad status → 400;
  - unknown instrument → 404;
  - MCX or 5m with no data, and the scorecard → 503.

  One cosmetic difference: platform routes say `unknown instrument NSE:NOPE` while analytics routes quote the id.
- **Host header.** `127.0.0.1:8000` and `localhost:8000` → 200. `evil.example.com`, `127.0.0.1.nip.io` and `attacker.localhost` → 400.
- **Latency.** Warm medians over 5 calls:

  | Endpoint | Median |
  |---|---|
  | candles 1D, 5000 bars | 23 ms (cold first call 350–374 ms) |
  | indicators, 2 series | 20 ms |
  | **indicators, all 26 series, limit 5000** | **647 ms** |
  | patterns 1D, limit 5000 | 378 ms (max 767 ms) |
  | patterns 1h | 138 ms |
  | levels | 18–28 ms |
  | forecast 1D / 1h | 66 / 118 ms |
  | **scanner 1D** | **914 ms (worst 4,970 ms)** |
  | scanner 1h | 1,634 ms |

  The scanner recomputes context and patterns for all 14 instruments on every call and has no cache.
- **`ingest: blocked` reporting.** Health shows `data_source: fyers`, `fyers_connected: false` and ingest blocked with the right reason. Forecasts respond by abstaining as stale. Candles, levels and patterns do not say they are stale (see F1).
- **Secrets.** 5 files under `data/logs` and 80 saved API responses were scanned for JWTs, bearer tokens, `auth_code=`, `access_token`, `sk-ant-` and every `.env` value of 6 or more characters. There were 0 hits, and `data/secrets` holds no token yet. No values were printed.
- **Orders.** The OpenAPI spec has no order, position or trade routes. The only non-GET route is POST /api/auth/fyers/code.

## 10. Findings ranked by severity

| # | Sev | Finding | What a trader loses | Where | Suggested fix |
|---|---|---|---|---|---|
| F1 | High | Stale 1D series served as "previous day" levels with no flag (D1). NIFTY 1D levels on 09-23 use 09-21; the 1h view says a different PDC | Trades pivots, CPR and PDH/PDL that are a day old: 85 pts off on NIFTY PDC | `features/levels.py:131` `key_levels`, `api/routes/analytics.py:307`; ingest | Add `as_of`/`stale` to LevelsResponse, PatternSignal and ScannerRow (a contract change) and show it. Re-fetch the last 5 daily bars on every ingest. Cross-check the 1D vs 1h session set after ingest |
| F2 | High | Budget Sunday 2026-02-01 session missing from data and calendar (D2). Also missing: 2025-02-01 from BANKNIFTY and SENSEX 1D, and 2024-01-20, 2020-02-01 and 2015-02-28 from every 1D series | A fake −2.1% "gap" and wrong patterns around the year's biggest event day | `config/markets.yaml` special_sessions | Add 2026-02-01 (09:15–15:30, NSE/BSE/MCX) and past special sessions. Re-ingest from Fyers. Add a cross-instrument session-set check |
| F3 | High | Inverted hammer and hanging man stops inside noise: 66–70% within 0.3 ATR | Stopped out by the next tick; the pattern's stats will look worse than the idea | `patterns/__init__.py:333-336`, `patterns.yaml` `buffer_atr` | Floor the stop distance (e.g. ≥ 0.5 ATR from close), or place it beyond the confirmation bar |
| F4 | High | Live calls without any stop (both non-abstaining forecasts `invalidation: null`), from an unvalidated trend bucket | A direction with no risk definition; can't size a position | `forecast/analog.py:250-260, 277` | Abstain unless an invalidation exists (or use an ATR stop), and require a validated/certified bucket before showing a direction |
| F5 | High | 1h intraday edge can't beat costs: breakeven 61–63% vs the plan's 53–58% | Every 1h call loses money after costs | research protocol, forecast | Show the cost hurdle on each call and gate 1h calls on expected move after costs. Horizons crossing 15:30 are not intraday trades |
| F6 | Med | 09:15 1h volume = 0 historically → fake 25× rel_volume today; VWAP excludes the opening hour (D4) | False "volume spike" confirmations; VWAP level 7–29 bp off | `indicators/functions.py:119-137`, yahoo adapter | Treat zero volume on liquid stocks as missing (NaN), exclude it from baselines and VWAP. Recheck on Fyers data |
| F7 | Med | 15:15 1h stubs and lost 15:15 bars (D5, D6); 1h two-bar patterns measured against them across the overnight gap | Noisy 1h signals at the close and open; wrong 1h PDC on older days | `data/clean.py:27` `fake_bar_mask`, pattern engine | Don't drop stubs on traded days without a missing-bar record. Skip two-bar patterns whose first bar is a partial (15-minute) bar. Take intraday PDH/PDL/PDC from the 1D bar |
| F8 | Med | LT 2006-09-27 half-price bar (D3) | Fake −68%/+73% gaps in training data | `data/clean.py` | Spike-reversal filter (\|gap\| > 25% that reverses the next day) plus the PLAN corporate-actions check |
| F9 | Med | Ghost candles are all dojis (bodies 0.01–0.08 ATR vs 0.35–0.39 real) | The chart says "indecision" when the model doesn't | `forecast/analog.py:262-275` | Use a medoid analog path, or draw only the band and the p50 close |
| F10 | Med | Pattern noise: 36% of 1D bars and 46% of 1h bars flagged; up to 3 patterns per candle; outside bar with a 0.6-pt exceedance | Alert fatigue (about 270 1h alerts a month) | `patterns/__init__.py:225`, alerts | Collapse the patterns on one bar into one signal, keep neutral patterns off alerts, add a minimum exceedance (≥ 0.05 ATR) for outside bars, require a bullish second body for harami |
| F11 | Med | Fyers delivery brokerage wrong in costs.yaml (free vs ₹20/0.3%); DP and clearing charges missing | 1D expectancy overstated by about 0.05% per trade at ₹1 lakh | `config/costs.yaml:7` | `delivery_free: false`, add `dp_per_sell_inr: 12.5`, and add clearing to txn |
| F12 | Med | Scanner mixes reference dates, has no abstain reason, shows the stale day's change, and ranks INDIAVIX | Can't tell stale from no-edge; a VIX "signal" | `api/routes/analytics.py:344-395` | Add `abstain_reason`/`stale` to ScannerRow (contract) and skip `tradable: false` |
| F13 | Low | MCX evening sessions on 11 NSE holidays treated as closed | Wrong phases and ghost times for MCX on those evenings | `config/markets.yaml` | Per-exchange holiday session scopes (morning/evening) |
| F14 | Low | Muhurat and index-volume quirks (D7, D8); expiry days not used as context | Tiny Muhurat bars create fake doji/inside bars; index rel_volume is noise | clean/context | Tag Muhurat, hide rel_volume for indices, add an `expiry_day` context tag (BANKNIFTY monthly only) |
| F15 | Low | Latency: all-series indicators 650 ms, scanner up to 5 s | A sluggish dashboard | analytics routes | Default to the selected series; cache the scanner per bar close |
| F16 | Low | Wording: "21th percentile", internal level names in the "why", 404 detail format differs | Polish | `forecast/analog.py:336-348` | Use ordinal formatting and LEVEL_LABELS |

## 11. Acceptance tests added

All tests live in `backend/tests/acceptance/`.

- Stored-data tests read `<repo>/data/candles` directly, because the suite conftest redirects DATA_DIR, and they skip when there is no data.
- Live-API tests are `@pytest.mark.network`, so they are excluded by default and skip if the API is down.
- Known defects are `xfail` with the finding id in the reason, so the suite stays green and shows XPASS when a defect is fixed. xfails on config or synthetic inputs are `strict=True`; the ones that depend on data are `strict=False`.

| File | Tests | What it guards |
|---|---|---|
| `acceptance_helpers.py`, `conftest.py` | helpers | Real-data loader; independent RSI/ATR/MACD/Supertrend/pivot formulas (TradingView SMA seeds); secret scanner that reports names only |
| `test_acc_data.py` | 14 official closes, 7 split/bonus ex-dates, OHLC sanity, 1D stamped 09:15, no holiday bars, weekend bars only on known special sessions, 1h grid ≤ 7 bars, ≥ 95% complete 1h sessions, no new spike-reversal bars | Data truth |
| | xfail: LT spike (D3), 1D covers every 1h session (D1/D2), calendar knows 2026-02-01 (strict), Budget Sunday stored (D2), 09:15 volume (D4), 15:15 stubs (D6) | Known data defects |
| `test_acc_levels.py` | RSI14/ATR14/MACD/Supertrend on 5 series (last 50 bars, rtol 1e-6); PDH/PDL/PDC, pivots and CPR vs formulas on 5 series; session VWAP | Levels and indicators match formulas on stored data |
| `test_acc_trader.py` | pattern stops on the right side and ≤ 3 ATR for ≥ 95%; ghost ranges within 0.4–1.6× real range, bands ordered and widening; 1D directional rate 0.5–6 per month and < 50% of bars flagged; intraday round trip 0.10–0.20% | Stops, ghost candles, noise, costs |
| | xfail: stops ≥ 0.3 ATR for 95% (F3); synthetic inverted hammer / hanging man stop (F3, strict); ghost bodies (F9); every call has a stop (F4); 09:15 rel_volume artifact (F6); Fyers delivery brokerage (F11, strict) | Known trader-facing defects |
| `test_acc_security.py` | no JWT/bearer/auth_code/access_token/sk-ant or `.env` value in `data/logs/*.log*` | Secrets in logs |
| `test_acc_api_live.py` (network) | contract keys for every GET endpoint; 13 error-code cases; 503 not 500 for empty series; 3 foreign Host headers → 400; no order routes and only one write route; ingest banner matches the Fyers state; stale scanner rows abstain; latency budgets (chart < 500 ms, scanner < 3 s); no secrets in responses | API contract, failure modes, security |
| | xfail: 1D levels from the last completed session (F1) | Stale levels |

Results on 2026-09-23:

- `backend\.venv\Scripts\python -m pytest backend -q` → **459 passed, 29 deselected, 14 xfailed** (green).
- `pytest backend/tests/acceptance -m network` → **28 passed, 1 xfailed**.
- `--runxfail` confirms that every xfail fails for its stated reason, for example "NSE:RELIANCE: opening-bar rel_volume up to 25.4x", "1D levels built from an old session for NIFTY50, BANKNIFTY, INDIAVIX, LT, SENSEX", and "mean ghost body 0.02 ATR".
- `ruff check backend/tests/acceptance` is clean.

## Sources

- Business Standard, 21 Sep 2026 close: https://www.business-standard.com/amp/markets/capital-market-news/sensex-settles-564-pts-higher-nifty-ends-above-23-400-level-126092100713_1.html
- Business Standard, 22 Sep 2026 close: https://www.business-standard.com/amp/markets/capital-market-news/sensex-settles-330-pts-lower-nifty-ends-below-23-350-126092200798_1.html
- BBN Times, Sensex 74,858.99: https://www.bbntimes.com/global-economy/bse-sensex-today-index-gains-564-points-to-74-858-99-as-oil-retreat-offers-relief-after-six-weekly-losses
- RELIANCE 22 Sep 2026: https://www.indmoney.com/stocks/reliance-industries-ltd-share-price , https://www.tickertape.in/stocks/reliance-industries-RELI
- HDFC Bank 22 Sep 2026: https://www.5paisa.com/blog/hdfc-bank-stock-update-22-sep-26
- TCS 22 Sep 2026: https://www.5paisa.com/blog/tata-consultancy-services-stock-update-22-sep-26
- 4 Jun 2024: https://www.business-standard.com/markets/news/stock-market-live-updates-june-4-sensex-nifty-lok-sabha-election-results-2024-psu-stocks-nse-124060400081_1.html
- 23 Mar 2020: https://zeenews.india.com/markets/sensex-tanks-3934-72-points-nifty-ends-at-7610-25-amid-global-fears-of-prolonged-recession-2271137.html
- RELIANCE bonus 2024: https://www.angelone.in/news/share-market/reliance-share-price-in-focus-28-october
- HDFC Bank bonus 2025: https://hdfcsky.com/news/hdfc-bank-to-issue-11-bonus-shares-ex-date-on-august-26 , https://www.businesstoday.in/amp/markets/stocks/story/hdfc-bank-share-price-plunges-50-as-stock-turns-ex-bonus-heres-why-491044-2025-08-26
- Saturday session 2024-01-20: https://zerodha.com/marketintel/bulletin/367702/live-trading-session-on-saturday-january-20-2024 , https://www.zeebiz.com/markets/stocks/live-updates-share-market-saturday-session-jan-20-live-nse-bse-nifty50-bank-nifty-sensex-q3-fy24-results-market-holiday-on-jan-22-for-ram-temple-inauguration-272904
- Budget Sunday session 2026: https://www.business-standard.com/markets/news/stock-markets-nse-bse-to-stay-open-on-sunday-february-1-for-union-budget-2026-nirmala-sitharaman-126013000559_1.html , https://upstox.com/news/market-news/latest-updates/nse-bse-mcx-and-ncdex-to-remain-open-on-sunday-february-1-for-union-budget-2026/article-188644/
- 1 Feb 2026 close: https://upstox.com/news/market-news/trading/stock-market-nifty-50-sensex-live-updates-on-sunday-february-1-union-budget-2026/liveblog-188661/ ; 2 Feb 2026 close: https://www.etvbharat.com/en/business/stock-markets-today-bse-sensex-nse-nifty-latest-updates-february-2-monday-enn26020201092
- 2026 holidays: https://zerodha.com/marketintel/holiday-calendar/ , https://www.nseindia.com/resources/exchange-communication-holidays
- MCX 2026 holidays: https://www.icicidirect.com/ilearn/commodity/articles/mcx-holiday-list
- Fyers charges: https://fyers.in/charges-list
- MCX charges and CTT: https://v2.webnotes.in/zerodha-commodity-brokerage
