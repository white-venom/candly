# pv2_range validation: do candlestick patterns sharpen range_v1?

Generated 2026-09-24T12:19:23+00:00 by `python -m candly.research.pv2_range_eval`. Pre-registered in `config/edge_search_v2.yaml` (patterns_range, sha256 `2c7ea1287598`). Validation span only; the holdout was not read.

**Result: FAIL (pass_if not met)** (0 of 3 timeframes meet pass_if; rule: at least 2 of 3 timeframes; each needs improvement >= 1% and CI lower > 0). Family Benjamini-Hochberg: pending: a timeframe passes only if its primary p also survives Benjamini-Hochberg at q = 0.10 across every edge_search_v2.yaml primary (applied by the lead).

| tf | rows | Winkler range_v1 / pv2 (per step) | improvement [95% CI] | one-sided p | coverage range_v1 / pv2 | pinball close change | pattern gain share | pass_if |
|---|---|---|---|---|---|---|---|---|
| 5m | 610,652 | 3.3709 / 3.3709 | 0.00% [-0.02%, 0.02%] | 0.4723 | 80.3% / 80.3% | -0.00% [-0.01%, 0.01%] | 0.97% | fail |
| 15m | 203,528 | 3.4469 / 3.4474 | -0.02% [-0.04%, 0.01%] | 0.8651 | 79.9% / 79.9% | -0.02% [-0.03%, -0.00%] | 0.72% | fail |
| 1h | 56,988 | 3.6455 / 3.6453 | 0.01% [-0.05%, 0.06%] | 0.4363 | 80.0% / 80.1% | 0.00% [-0.04%, 0.03%] | 0.47% | fail |

Improvement = 1 - W(pv2_range) / W(range_v1) on the same validation forecasts (3 steps each); positive means patterns narrowed the intervals or cut the misses. CIs and p resample whole IST dates (2000 resamples); p = (1 + #{bootstrap improvement <= 0}) / 2001.

## Reproduction of range_v1

| tf | reported Winkler | harness on cached predictions | reproduced (retrained here) | difference | reproduced? | max abs prediction diff |
|---|---|---|---|---|---|---|
| 5m | 3.370931 | 3.370931 | 3.370931 | 0.000% | True | 0.0 |
| 15m | 3.446884 | 3.446884 | 3.446884 | 0.000% | True | 0.0 |
| 1h | 3.645538 | 3.645538 | 3.645538 | 0.000% | True | 0.0 |

## Pattern features in the pv2 model

| tf | gain share (all / high / low / close) | pattern features used | top pattern feature (gain share, rank) | rows with a pattern on bar t |
|---|---|---|---|---|
| 5m | 0.97% / 0.79% / 1.00% / 1.19% | 51 of 70 | pat_inside_bar_lag2 (0.06%, #33) | 39.7% |
| 15m | 0.72% / 0.60% / 0.73% / 0.89% | 51 of 70 | pat_inside_bar_lag0 (0.08%, #33) | 40.3% |
| 1h | 0.47% / 0.37% / 0.47% / 0.61% | 51 of 70 | pat_outside_bar_lag2 (0.04%, #33) | 46.2% |

## Unregistered choices (made before any result)

- Pattern group: for each of the 23 patterns of candly.patterns and each lag k = 0, 1, 2, a 0/1 column 'confirmed with its last bar on bar t-k' (multi-hot: several patterns can end on one bar), 69 columns, plus pat_forming; appended after range_v1's features, before the instrument column.
- Lags are by bar position in each instrument's series (a lag can reach into the previous session, as the detector's multi-bar patterns do).
- Primary comparison: pv2_range against range_v1 retrained in the same run with the same settings and thread count (the reproduced baseline), so the feature group is the only difference. pv2_range against the cached range_v1 predictions is reported alongside.
- range_v1 counts as reproduced when its Winkler per step is within 0.1% of the value range_eval reported (a tenth of the 1% pass bar).
- Bootstrap blocks are whole IST dates, as range_v1's evaluation used (edge_search.yaml common.bootstrap kind date_block, 2000 resamples, seed 20260924, 95%). Month blocks are shown for information only.
- Coverage, pinball and 'same' changes use the same bootstrap draws as the primary.

## Deviations

- Forming flag: pat_forming is 0 at every reference bar. A range forecast is made when bar t closes; at that instant no bar is forming, and the next partial bar is the target bar t+1 itself, whose shape would leak the target. Any non-constant causal definition would have to be invented after the fact (for example 'the first bars of a 3-bar pattern are in place'), so the most conservative reading keeps the registered column and gives it no information. The test therefore measures confirmed patterns only.
- LightGBM num_threads = 2 instead of range_v1's 4 (the laptop was shared with three other jobs). Every other hyperparameter, the sample and the seeds are range_v1's; the reproduction check shows whether the thread count changed any prediction.

## Caveats

- Validation span only (train_end 2023-01-01 to 2025-10-01); the holdout was never loaded.
- The 12 NSE watchlist instruments are today's large caps (survivorship bias), as in range_v1.
- Intraday fits use range_v1's seeded sample of at most 120,000 training bars per fold.
- Rare patterns could not enter the model: piercing line, dark cloud cover, morning and evening star, three white soldiers and three black crows fire on roughly 70-350 of the 120,000 sampled training bars, and with range_v1's min_data_in_leaf = 300 and 70% bagging no split can isolate them (they get no split in any fold). The test measures the common patterns; the rare ones were not testable under range_v1's hyperparameters, which the registration fixes.
- Whole-date blocks ignore serial dependence across days (volatility clustering); the month-block CI is shown for information.
- This is one of several primary p-values in edge_search_v2.yaml; the family Benjamini-Hochberg at q = 0.10 across every v2 primary is applied by the lead once all v2 tests have reported.
