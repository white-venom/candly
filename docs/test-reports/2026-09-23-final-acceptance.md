# Final acceptance run: Fyers data, validated-bucket gate, new dashboard

Tester: trader-tester. Started 2026-09-23 evening; resumed 2026-09-24 10:35 IST after a usage-limit stop and a machine restart.
API http://127.0.0.1:8000 (scheduler on), dashboard http://localhost:5173, both started by `scripts\start.ps1`.
Data: Fyers history (1D from 2005 NSE/BSE and 2018 MCX, 5m from 2017, 15m/1h built from 5m) up to 2026-09-23. Yahoo archived.
State on 2026-09-24 10:31 IST: `fyers_connected: false`, `ingest: blocked` (overnight token renewal was rejected). Live-intraday checks wait for the re-login.
Previous report: `2026-09-23-first-acceptance.md` (findings F1–F16). Go/no-go #1 summary: `2026-09-23-go-no-go-1.json`.

Status: IN PROGRESS. Sections marked (pending) are not yet filled in.

## 1. Verdict (plain trader language)

(pending)

## 2. Checks table

(pending)

## 3. Data truth on Fyers

Inventory on 2026-09-24 10:30 IST (before today's ingest):
- NSE/BSE 1D: 5,389 bars from 2005-01-03 to 2026-09-23.
- Intraday: NSE from 2017-07-03 (indices from 2017-07-17); SENSEX intraday only from 2019-05-28.
- MCX: 1D from 2018-01-01, but 1D ends **2026-09-22** while MCX 5m runs to 2026-09-23 23:25 (see D-MCX1D below).
- No Yahoo series left. Archives are in `data/archive/`.

### 3.1 Official closes

| Instrument | Date | Stored close | Official | Source | Result |
|---|---|---|---|---|---|
| NIFTY50 | 2026-09-23 | 23,446.80 | 23,446.80 | Business Standard close report | pass |
| SENSEX | 2026-09-23 | 74,828.25 | 74,828.25 | Business Standard | pass |
| BANKNIFTY | 2026-09-23 | 56,548.90 | 56,548.90 | Liquide market recap | pass |
| NIFTY50 | 2026-09-22 | 23,329.00 | 23,329.00 | Business Standard (first report) | pass (was missing on Yahoo, D1) |
| SENSEX | 2026-09-22 | 74,529.08 | 74,529.08 | Business Standard | pass (was missing, D1) |
| BANKNIFTY | 2026-09-22 | 56,215.55 | 56,215.55 | Business Standard | pass (was missing, D1) |
| NIFTY50 | 2026-02-01 (Budget Sunday) | 24,825.45 | 24,825.45 | Upstox live blog | pass (was missing, D2) |
| SENSEX | 2026-02-01 | 80,722.94 | 80,722.94 | Upstox live blog | pass |
| 14 closes from the first run (2020-03-23 … 2026-09-22, incl. RELIANCE/HDFCBANK/TCS) | | | | first report §3.1 | 14/14 pass (`test_daily_close_matches_official_close`) |
| MCX GOLD (Oct contract) | 2026-09-22 | 1,52,716 | 1,52,716 "previous close" | Business Standard, 23 Sep MCX opening report | pass |
| MCX CRUDEOIL (Oct contract) | 2026-09-23 | 1D bar **missing**; last 5m trade 8,800 | 8,825 previous close | IIFL contract page, 24 Sep | **fail** (1D stale); the 5m last trade is 0.28% off the official close, as expected for last trade vs settlement |
| MCX CRUDEOIL today | 2026-09-24 | 5m open 8,769, high 8,798, OI 11,916 | open 8,769, high 8,798, OI 11,918 | IIFL live quote, 10:27 | pass |

Budget and Saturday sessions are now stored. NIFTY 1D has 2026-02-01, 2025-02-01 (23,482.15), 2024-01-20 (21,571.80) and 2020-02-01 (11,661.8). The same days are in SENSEX and BANKNIFTY. First-run D2 is fixed in the data. `markets.yaml` now lists them for NSE/BSE, and `is_trading_day` is True for NSE and BSE on 2026-02-01.

### 3.2 Splits, bonuses, bad bars

- 7/7 split and bonus ex-dates show no mechanical gap (RELIANCE 2017/2024, HDFCBANK 2011/2019/2025, TCS 2009/2018).
- The LT 2006-09-27 half-price bar is gone (D3 fixed). No 1D spike-and-reverse bar remains in any NSE/BSE series.

### 3.3 Session bar counts (NSE/BSE, all sessions before 2026-09-24)

| Series | 5m full (75) | 15m full (25) | 1h full (7) | Short sessions |
|---|---|---|---|---|
| NIFTY50 | 99.5% | 99.6% | 99.8% | Muhurat 2021-11-04 and 2025-10-21; DR-drill sessions 2024-03-02 and 2024-05-18 (21 bars, correct) |
| SENSEX | 99.6% | 99.6% | 99.6% | same days |
| RELIANCE | **97.8%** | 99.6% | 99.8% | **every session since 2026-08-03 has 74 bars** (see §4.2) |
| TCS | **97.9%** | 99.6% | 99.8% | same |

No session has too many bars, and no bar sits off the grid.

### 3.4 MCX sessions and the US-DST close

- The last 5m bar is 23:50 (close 23:55) until the US switch, then 23:25 (close 23:30).
  - 2025-03-07 → 03-10: 23:50 → 23:25.
  - 2025-10-31 → 11-03: 23:25 → 23:50.
  - 2026-03-06 → 03-09: 23:50 → 23:25.
  - All match the rule. **pass**
- A full session is 174 bars (179 in US winter). Evening-only sessions are stored (e.g. 2025-11-05, 17:00–23:55).
- **F13 still open.** In 2026 so far, MCX traded an evening session (17:00–23:25/23:50) on 9 of 11 NSE holidays: 01-15, 03-03, 03-26, 03-31 (thin, 14 bars), 04-14, 05-01, 05-28, 06-26 and 09-14. `markets.yaml` marks all of them fully closed, and `is_trading_day("MCX", …)` returns False.
- MCX also traded 09:00–17:00 on the Budget Sunday 2026-02-01: GOLD 72 bars, CRUDEOIL 95. `markets.yaml` has MCX `open: null`, so the calendar says closed. This is why `test_calendar_knows_the_2026_budget_sunday_session` (strict xfail) still fails, now only on MCX.
- Consequences on such evenings:
  - `intraday_cycle` skips MCX (`cal.is_open` is False), so the evening bars arrive only with the next day's backfill;
  - the health phase says closed;
  - stale checks and ghost-candle times are wrong.
- Likely next affected days: 10-20, 11-10 and 11-24. Verify against the MCX circular.

### 3.5 MCX continuous front-month rolls (HIGH)

Question from the lead: is CRUDEOIL −5.62% on 22 Sep (and NATURALGAS +3.52%) a roll artefact?

**How the roll shows up.** In the continuous series (`cont_flag=1`), the contract switch is visible as an open-interest jump at a session boundary:
- CRUDEOIL 5m OI: 17,234 (16 Sep open) → 13,228 → 14,674 → 10,130 → 6,304 (21 Sep close; the September contract dying).
- Then **12,769 at the 09:00 open on 22 Sep**, with an overnight gap of **−2.58%** (9,160 → 8,924).
- The September contract expired on 21 Sep: 19 Sep was a Saturday, and IIFL lists a `crudeoil/21-sep-2026` contract.
- So the 22 Sep daily bar compares the October contract's close (8,643) with the September contract's close (9,158). The −5.62% change mixes the market move with the Sep/Oct spread.
- I could not find a reliable published October close for 21 Sep: three search summaries gave 8,876, 9,133 and 9,213. The split between real move and roll therefore can't be pinned down from outside sources. That is the point: the displayed number is not a same-contract change.

**NATURALGAS +3.52% on 22 Sep is a real move.** There is no OI jump (25,561 → 13,655 falling through the day), the September contract is still live (expiry 25 Sep), and the move is intraday (open 273.0 → close 282.4).

**Across the whole history** (roll day = 1D OI ratio > 1.5):

| Instrument | Roll days | Roll-day gap: median / mean \|gap\| / share > 0 | Other days: median / mean \|gap\| / share > 0 | \|gap\| in ATR, roll vs other (median) |
|---|---|---|---|---|
| SILVER | 14 | **+2.44% / 2.38% / 100%** | +0.03% / 0.48% / 53% | **0.97 vs 0.15** |
| GOLD | 17 | +0.67% / 0.91% / 76% | +0.02% / 0.29% / 53% | 0.47 vs 0.16 |
| NATURALGAS | 37 | +1.95% / **7.80%** / 68% | +0.07% / 0.92% / 52% | **1.12 vs 0.13** |
| CRUDEOIL | 45 | −0.24% / 0.73% / 31% | +0.05% / 0.98% / 53% | 0.14 vs 0.14 |

Every silver roll is a fake gap-up of about 1 ATR (contango carry). Gold is the same, smaller. Natural gas roll gaps are about 8× normal.

**The expiring contract is dead for days before the switch.** The series stays on the old contract until expiry, so bullion charts show a contract nobody trades:

| Instrument | Last session before roll: median 5m bars (of 174) / volume vs normal | 2 sessions before | 3 sessions before |
|---|---|---|---|
| GOLD | **26 / 0.01×** | 36 / 0.01× | 53 / 0.02× |
| SILVER | **24 / 0.00×** | 39 / 0.01× | 72 / 0.03× |
| NATURALGAS | 174 / 0.21× | 174 / 0.50× | 174 / 0.76× |
| CRUDEOIL | 174 / 0.57× | 174 / 0.81× | 174 / 0.98× |

Example: GOLD on 4–5 Aug 2026 traded 93 and 139 lots (OI 71 and 140) in 40 and 47 bars. On 6 Aug it jumped +2.6% onto the October contract with OI 10,286.

**What it breaks.**
- `change_pct` on roll days (the scanner shows −5.62% for crude).
- ATR: the true range includes the roll gap, so the 0.5 ATR stop floor and ghost-candle ranges widen.
- Gaps and patterns across the roll. On 1D, directional patterns are not over-represented on roll days (0–0.11 per roll day vs 0.13 otherwise), so the main damage is to gaps, ATR, labels and the 3-bar outcome paths behind the analogs and scorecard.
- Candles from an illiquid contract for about 3 sessions before each bullion expiry.
- Today's NATURALGAS chart already shows the dying September contract: OI 5,691, last-day volume about 0.2× normal. GOLD will do the same from about 30 Sep (expiry 5 Oct).

**Code.** `data/expiries.py:165 mcx_roll_dates()` exists, but nothing in patterns, labels, the scorecard, context, ATR or the scanner calls it. `grep` finds no other use of "roll". CONTRACTS.md already says historical roll masking is "to do".

**Fix.**
1. Detect historical rolls from the OI jump. It is reliable in this data: 45/37/17/14 roll days on the expected days of month.
2. Store `roll` flags next to the candles.
3. Exclude any pattern, label or analog path that spans a roll, and reset ATR at a roll, or compute the true range without the cross-roll gap.
4. Compute `change_pct` against the same contract.
5. For bullion (and ideally natural gas), switch the live series to the next contract once its OI exceeds the front's, typically about 5 sessions before expiry, instead of riding the tender period. The Fyers symbol master already lists both contracts.

### 3.6 Other data notes

- **D-MCX1D (Med).** MCX 1D has no 2026-09-23 bar.
  - The MCX daily bar is fetched only at 00:10, with one catch-up at 08:40 (`jobs/scheduler.py`). Both ran while the Fyers token was rejected overnight.
  - Nothing re-runs the daily ingest when Fyers reconnects (10:33 today). MCX 1D stays one session stale until 00:10 tonight.
  - The API handles this correctly: `/api/levels` says `stale: true`, as of 09-22, and the 1D forecasts abstain with "stale data".
  - Fix: run the 1D catch-up for every exchange right after a successful login or token refresh.
- **MCX incremental ingest rewrote history.** At 10:36 the 5m ingest reported 47 (CRUDEOIL), 64 (NATURALGAS), 132 (GOLD) and 111 (SILVER) rows "added/changed", about 19 new bars each, so 28–113 changed bars from 23 Sep. These are probably OI or volume revisions (`oi_flag=1`). Worth logging which columns changed.
- **Index volume.** NIFTY/SENSEX/BANKNIFTY 1D volume is 0 on 94% of days (Fyers only has it for recent years), but now non-zero recently. `/api/levels` now draws a VWAP for NIFTY 5m (23,237.05 at 10:45). An index VWAP built from aggregate constituent volume is not what a trader uses; they use the futures VWAP. The first run left it out for indices. Suggest leaving it out again, or labelling it.

## 4. Intraday truth

### 4.1 09:15 opening volume (first-run F6): fixed

- Fyers 1h and 5m have **no zero-volume 09:15 bars**: 0 of about 2,280 sessions for RELIANCE, HDFCBANK, ICICIBANK and INFY. On Yahoo it was 425/487.
- The 09:15 1h bar carries 20–23% of the day's volume (median), and the 09:15 5m bar 4–5%. That is the normal U-shape.
- Opening-bar `rel_volume` over the last 60 sessions: median 0.60–0.82, maximum 2.1–6.4×. No more fake 25× spikes.
- The two F6 xfails now XPASS, and their markers are removed (§12).

### 4.2 The 15:15 "stub" (first-run F7) is now the NSE Closing Auction Session (HIGH)

The Yahoo-era stub is gone. Fyers shows a different and real effect.
- **From 2026-08-03**, every NSE stock in the watchlist has:
  - a **flat 15:15 5m bar** (RELIANCE 23 Sep: O=H=L=C 1,246.1 on 1,221 shares);
  - **no 15:20 bar**;
  - a **15:25 bar that jumps to the official close on auction volume** (O 1,246.1 → C 1,248.0 on 536,931 shares).
- Over the 37 sessions since 1 Aug, 15:20 is missing on 36–37 of 37 for all 10 stocks. Before August it is missing on 0–5%.
- After 3 Aug the last 5m close equals the official 1D close on **100%** of sessions. Before, it was 1.8% (`|diff| > 0.5 bp` on 98.2% of sessions).
- NIFTY freezes during the auction and then jumps: 23 Sep 15:15 range 0.3 points, 15:20 O=H=L=C 23,431.35, 15:25 jumps to 23,446.80.

This is SEBI's **Closing Auction Session** (Zerodha Z-Connect, Outlook Business, NSE product page), from 3 Aug 2026 for Category I (F&O) stocks:

| Phase | Time |
|---|---|
| Continuous trading | 09:15–15:15 |
| Reference price | 15:15–15:20 |
| Order entry (market and limit) | 15:20–15:25 |
| Order entry (limit only) | 15:25–15:30 |
| Matching | 15:30–15:35 |

**F&O contracts now trade until 15:40.**

candly's calendar still models continuous trading to 15:30 for all of NSE/BSE. Effects measured on the 10 stocks (median across stocks):

| 15:25 5m bar | Before 3 Aug (124 sessions) | After (38 sessions) |
|---|---|---|
| Body in ATR | 0.42 | **1.75** |
| Share with body ≥ 0.7 ATR (marubozu threshold) | 28% | **78%** |
| Directional patterns on that bar per session | 0.21 | **0.76** |

- **Fake close-of-day signals.** Most days now end with a fake marubozu or engulfing on 5m, and the 1h 15:15 bar is the auction print (body = range). This is why `test_recent_closing_hour_bars_are_real_bars` still fails, now for a real reason. I replaced it (§12).
- **Stranded forecasts.** The 15:20 slot never exists. The calendar expects it, so any 5m forecast whose target includes 15:20 can never be graded and becomes `void` after 7 days (CONTRACTS grading rule).
- **Cleaning.** `clean.py` drops a flat zero-volume 15:20 bar every day ("NSE 5m: dropped 1 fake bars" in today's log, once per stock). That is the right outcome for the wrong reason.
- **Session model.** "close" phase, horizon-crosses-close gate, cost holding type. The index-futures session now runs to 15:40, so a NIFTY futures trader sees 10 minutes that candly's spot index doesn't have.
- **Fix.**
  - Add CAS to `markets.yaml` with a `from: 2026-08-03` date and a per-kind session: equities continuous to 15:15, then an auction bar.
  - Mark the auction bar and keep it out of patterns, `rel_volume` baselines, ATR and intraday labels. Keep it for the close and VWAP.
  - Stop expecting a 15:20 5m slot.
  - Decide whether index futures (to 15:40) should replace spot for intraday index work.

### 4.3 VWAP vs hand calculation: pass

Session VWAP, Σ(typical price × volume) / Σ volume, resets per IST date. Latest session (24 Sep):

| Series | Hand | candly | Diff |
|---|---|---|---|
| RELIANCE 5m (20 bars) | 1,238.2319 | 1,238.2319 | 0.000000 |
| RELIANCE 1h (1 bar) | 1,238.2667 | 1,238.2667 | 0.000000 |
| TCS 5m / 1h | 2,084.5526 / 2,080.2333 | same | 0.000000 |
| CRUDEOIL 5m / 1h | 8,779.4989 / 8,779.3333 | same | 0.000000 |

`/api/levels` RELIANCE 5m VWAP 1,238.2319 is the same value (closed bars only). The 1h VWAP uses 1h typical prices, so it differs from the 5m one by about 0.3 bp. A broker VWAP is tick-based. Fine for levels.

### 4.4 Intraday PDH/PDL/PDC

- Intraday levels now come from the official daily bar. NIFTY 5m on 24 Sep: PDH 23,466.90, PDL 23,349.55, PDC 23,446.80, all equal to the 23 Sep 1D bar.
- First-run F1/F7 "two different previous closes" is fixed for NSE/BSE.
- MCX intraday PDC is still the last 5m trade (CRUDEOIL 15m PDC 8,800, official close 8,825), because the MCX 1D bar is stale (§3.6). Once 1D is fresh it should follow the same rule.
- `/api/levels` now has `as_of` and `stale`. RELIANCE 1h at 10:48 said `stale: true` although as-of was the right session (09-23). The 1h store had no 24 Sep bar yet (§4.5), so the rule compared 1h with the fresher 5m store. That case is a false alarm.

### 4.5 Live intraday (Fyers connected 10:33:10)

(filled in below from the 75-minute poller)

## 5. Calls under the validated-bucket gate

(pending)

## 6. Expiry and calendar

(pending)

## 7. Dashboard as a trader

(pending)

## 8. Briefs

(pending)

## 9. Engineering

(pending)

## 10. Findings ranked by severity

(pending)

## 11. Status of first-run findings F1–F16

(pending)

## 12. Acceptance tests

(pending)

## Sources

(pending)
