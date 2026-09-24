# pv2_pooled validation: the v1 candlestick scorecard on the Nifty 200

Generated 2026-09-24T11:46:20+00:00 by `python -m candly.research.pv2_pooled_eval`. Pre-registered in `config/edge_search_v2.yaml` (patterns_pooled, sha256 `2c7ea1287598`). Train bars before 2019-01-01 (less the purge gap), validation 2019-01-01 to 2025-10-01; the holdout was not read.

**Result: PASS.** 1 certified buckets out of 13,380 tests (BH family, q < 0.1); Simes global p = 0.060. Rule: at least 1 certified bucket.

- Instruments: 196 used of 202 (6 have no bar before the holdout).
- Bars: 613,363 train, 309,285 validation. Pattern events: 286,476 train, 137,559 validation.
- Rows: 68,397, of which 13,380 are in the BH family (522 pooled ALL rows).
- Data quality: 149 overnight gaps beyond 25% in 26 instruments (unadjusted corporate actions or bad prints; counted, not removed).
- For scale, the v1 NSE 1D build (12 watchlist instruments): 1,551 tests, 0 certified, Simes p 0.220.

## Certified buckets

| pattern | dir | instrument | context | h | n (clusters) | hit vs base | p | q | expectancy after cost | validation n / hit vs base | validation p (info) / expectancy | certified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bearish_harami | bearish | ALL | vol_regime=high | 1 | 1329 (991) | 56.2% vs 49.7% | 4.5e-06 | 0.060 | +0.134% | 676 / 51.8% vs 48.5% | 0.063 / +0.217% | True |

Events of bearish_harami / ALL / vol_regime=high / h=1 (information only):

- train: n 1329, hit 56.2%, net after cost mean +0.134% / median +0.233%, 0 trades beyond 15% (hit without them 56.2%); by year: 1999: 29 at 62%, 2000: 17 at 47%, 2001: 25 at 44%, 2002: 58 at 53%, 2003: 113 at 54%, 2004: 49 at 67%, 2005: 79 at 54%, 2006: 77 at 62%, 2007: 66 at 59%, 2008: 39 at 59%, 2009: 81 at 58%, 2010: 56 at 52%, 2011: 53 at 53%, 2012: 63 at 56%, 2013: 102 at 61%, 2014: 93 at 55%, 2015: 68 at 63%, 2016: 60 at 62%, 2017: 109 at 42%, 2018: 92 at 59%.
- validation: n 676, hit 51.8%, net after cost mean +0.217% / median +0.125%, 0 trades beyond 15% (hit without them 51.8%); by year: 2019: 95 at 42%, 2020: 98 at 45%, 2021: 122 at 57%, 2022: 64 at 66%, 2023: 96 at 52%, 2024: 159 at 50%, 2025: 42 at 62%.

## Smallest q-values (top 15)

| pattern | dir | instrument | context | h | n (clusters) | hit vs base | p | q | expectancy after cost | validation n / hit vs base | validation p (info) / expectancy | certified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bearish_harami | bearish | ALL | vol_regime=high | 1 | 1329 (991) | 56.2% vs 49.7% | 4.5e-06 | 0.060 | +0.134% | 676 / 51.8% vs 48.5% | 0.063 / +0.217% | True |
| bullish_harami | bullish | ALL | vol_regime=high | 3 | 2203 (461) | 57.7% vs 50.8% | 3.4e-05 | 0.126 | +0.053% | 955 / 57.9% vs 52.7% | 0.022 / +0.087% | False |
| hanging_man | bearish | ALL | vol_regime=high | 3 | 1707 (529) | 54.7% vs 48.2% | 3.5e-05 | 0.126 | +0.152% | 1056 / 49.3% vs 46.8% | 0.052 / -0.242% | False |
| shooting_star | bearish | ALL | vol_regime=high | 5 | 2500 (270) | 52.6% vs 47.8% | 4.1e-05 | 0.126 | -0.064% | 1276 / 48.7% vs 46.1% | 0.063 / -0.101% | False |
| shooting_star | bearish | NSE:GAIL | all | 5 | 32 (31) | 84.4% vs 47.5% | 4.7e-05 | 0.126 | +1.712% | 20 / 70.0% vs 48.2% | 0.027 / +2.609% | False |
| bullish_marubozu | bullish | NSE:NIFTY200 | all | 1 | 58 (58) | 77.6% vs 54.5% | 0.00011 | 0.256 | +0.033% | 20 / 50.0% vs 55.8% | 0.696 / -0.170% | False |
| inside_bar | neutral | NSE:GMRAIRPORT | trend=down | 3 | 134 (116) | 64.9% vs 48.0% | 0.00028 | 0.518 | n/a | 39 / 64.1% vs 55.6% | 0.284 / n/a | False |
| hanging_man | bearish | ALL | vol_regime=high | 5 | 1707 (304) | 52.8% vs 47.3% | 0.00036 | 0.518 | +0.023% | 1056 / 49.9% vs 45.7% | 0.019 / -0.342% | False |
| bearish_marubozu | bearish | NSE:DRREDDY | all | 1 | 58 (58) | 70.7% vs 48.4% | 0.0004 | 0.518 | +0.774% | 8 / 62.5% vs 48.4% | 0.214 / -0.039% | False |
| shooting_star | bearish | NSE:BEL | vol_regime=high | 1 | 32 (32) | 78.1% vs 48.1% | 0.00045 | 0.518 | +1.596% | 15 / 66.7% vs 46.4% | 0.063 / -0.017% | False |
| bearish_harami | bearish | ALL | vol_regime=high | 5 | 1329 (312) | 52.7% vs 47.7% | 0.00046 | 0.518 | -1.621% | 675 / 50.7% vs 46.0% | 0.009 / -0.273% | False |
| inverted_hammer | bullish | NSE:MARICO | all | 3 | 41 (40) | 75.6% vs 50.8% | 0.00051 | 0.518 | +0.516% | 7 / 42.9% vs 50.1% | 0.650 / -0.193% | False |
| shooting_star | bearish | NSE:TITAN | trend=up | 5 | 30 (30) | 76.7% vs 45.2% | 0.00054 | 0.518 | +2.947% | 9 / 55.6% vs 43.4% | 0.237 / +1.202% | False |
| shooting_star | bearish | NSE:PIDILITIND | vol_regime=high | 5 | 42 (39) | 76.2% vs 50.6% | 0.00055 | 0.518 | +2.847% | 4 / 25.0% vs 43.8% | 0.787 / -0.090% | False |
| bullish_harami | bullish | ALL | all | 3 | 4861 (569) | 54.8% vs 50.4% | 0.00061 | 0.518 | -0.099% | 2225 / 56.3% vs 51.9% | 0.018 / -0.057% | False |

## Where buckets fall short (BH-family rows passing each part of the rule)

- directional: 3,814
- q_below_fdr: 1
- hit_rate_above_base: 6,777
- expectancy_after_cost_positive: 1,634
- n_clusters_min: 13,380
- validation_n_min: 9,676
- validation_hit_above_base: 6,493
- q_below_fdr_and_directional: 1

## Unregistered choices (made before any result)

- Instruments: config/universe_nifty200.yaml filtered by the v1 NSE build's own rule (research.yaml go_no_go_1.slice: NSE, kinds equity and index, INDIAVIX excluded, tradable, has 1D). This keeps the file's two benchmark indices (NIFTY200, NIFTY50) alongside the 200 stocks.
- Instruments with no bar before the holdout (listed after 2025-10-01) contribute nothing and are listed.
- Primary p = the smallest q-value of the scorecard's Benjamini-Hochberg family (rows with n_clusters >= min_samples), which is the Simes global p-value of that family; neutral patterns keep the scorecard's two-sided p.

## Deviations

- build_scorecard itself cannot take the universe (it resolves ids through the dashboard watchlist), so its internal steps are run on the universe's instrument records; outcomes are aggregated per pattern to fit in memory. A unit test checks the rows equal build_scorecard's on the same instruments.
- Expiry context: candly's expiry schedule resolves only watchlist instruments, so the 'expiry=yes/no' buckets exist for the watchlist names in the universe (the 12 v1 instruments that are in it) and for NIFTY50; the other stocks enter the 'all', trend and vol_regime buckets only. Giving every stock the stock-F&O monthly rule would assert F&O eligibility many of them did not have for their whole history.

## Caveats

- Survivorship bias: the universe is today's Nifty 200 (constituents file downloaded 2026-09-24). Stocks that left the index, were delisted, merged or failed before today are missing, and each stock is in the sample only from its first candle, so the train years hold the firms that later did well enough to be large today. The registration treats a pass as an upper bound. Each bucket is tested against the base rate of the same survivor instruments and bucket, so the selection moves hit rates and base rates together and the direction of its effect on a hit-vs-base edge is not known; a bearish edge cannot be assumed understated. Only a point-in-time universe would settle it.
- Validation span only for certification (train before 2019-01-01 less the gap, validation 2019-01-01 to 2025-10-01); the holdout was never loaded.
- Hit = close[t+h] beyond close[t] in the pattern's direction, but the expectancy trade enters at the next open (labels.trade_ret): for a bearish row at h=1 that is a short from the next open to the next close, an intraday short a cash-equity trader can take, charged the higher multi-day cost. Bearish rows at h > 1 need an overnight short (futures, available only for F&O names).
- Nifty 200 stocks move together: the cluster-robust test counts overlapping outcome windows across instruments as one cluster in the pooled ALL rows, so 200 stocks do not give 200x the independent evidence.
- This p-value is one of the primaries of edge_search_v2.yaml; the family Benjamini-Hochberg at q = 0.10 across every v2 primary is applied by the lead once all v2 tests have reported.
