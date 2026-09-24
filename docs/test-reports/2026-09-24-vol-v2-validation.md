# vol_v2 validation: log-HAR on 5m realised variance + VIX vs a recalibrated VIX

Generated 2026-09-24T11:39:04Z from edge_search_v2.yaml sha256 `2c7ea1287598`. Train 2017-07-17 to 2021-01-01, validation 2021-01-01 to 2025-10-01 (IST dates; holdout untouched). Fitted once on train.

A horizon passes only if both registered checks pass AND its primary p survives Benjamini-Hochberg at q = 0.1 across the whole v2 family (pending the other v2 tests).

## 5-day horizon: pass_if NOT MET

n_scored 1164 days (235 5-day blocks), n_train 826.

| forecaster | n | QLIKE | mean forecast vol | model improvement | 95% CI (h-day blocks) | 95% CI (60d blocks) |
|---|---|---|---|---|---|---|
| vol_v2_model | 1164 | 0.3394 | 13.73 | - | - | - |
| recalibrated_vix | 1164 | 0.3237 | 13.77 | -0.0157 | [-0.0478, +0.0093] | [-0.0554, +0.0111] |
| india_vix_raw (context) | 1164 | 0.3720 | 15.80 | 0.0326 | [-0.0096, +0.0655] | [-0.0209, +0.0791] |
| vol_v1_model (context) | 1164 | 0.3861 | 13.15 | 0.0466 | [+0.0121, +0.0839] | [-0.0035, +0.1185] |
| har_rv_daily (context) | 1164 | 0.4895 | 16.41 | 0.1501 | [+0.1128, +0.1892] | [+0.1082, +0.1960] |
| har_5m_log (context) | 1164 | 0.3842 | 14.24 | 0.0448 | [+0.0174, +0.0859] | [+0.0117, +0.1037] |

Mean realised / mean forecast variance (1 = unbiased): vol_v2_model 0.99, recalibrated_vix 1.00, india_vix_raw 0.77.

Primary: QLIKE improvement over recalibrated VIX -0.0157, one-sided p 0.8791 -> FAIL.

Variance-swap proxy, 235 periods: always short Sharpe 1.676 (max drawdown 51.6); conditional short 213/235 periods, Sharpe 1.517 (max drawdown 51.6). Difference -0.159, CI [-0.388, +0.032] (1-period blocks), [-0.364, +0.009] (60d blocks) -> FAIL.

## 20-day horizon: pass_if NOT MET

n_scored 1149 days (58 20-day blocks), n_train 811.

| forecaster | n | QLIKE | mean forecast vol | model improvement | 95% CI (h-day blocks) | 95% CI (60d blocks) |
|---|---|---|---|---|---|---|
| vol_v2_model | 1149 | 0.2013 | 16.28 | - | - | - |
| recalibrated_vix | 1149 | 0.1995 | 16.38 | -0.0019 | [-0.0172, +0.0116] | [-0.0179, +0.0144] |
| india_vix_raw (context) | 1149 | 0.1766 | 15.87 | -0.0247 | [-0.0420, -0.0082] | [-0.0440, -0.0057] |
| vol_v1_model (context) | 1149 | 0.2095 | 14.04 | 0.0082 | [-0.0388, +0.0589] | [-0.0542, +0.0727] |
| har_rv_daily (context) | 1149 | 0.3594 | 18.46 | 0.1580 | [+0.1188, +0.1998] | [+0.1006, +0.2232] |
| har_5m_log (context) | 1149 | 0.2354 | 16.63 | 0.0341 | [+0.0167, +0.0555] | [+0.0123, +0.0613] |

Mean realised / mean forecast variance (1 = unbiased): vol_v2_model 0.73, recalibrated_vix 0.72, india_vix_raw 0.76.

Primary: QLIKE improvement over recalibrated VIX -0.0019, one-sided p 0.5982 -> FAIL.

Variance-swap proxy, 58 periods: always short Sharpe 1.566 (max drawdown 39.4); conditional short 22/58 periods, Sharpe 1.372 (max drawdown 28.6). Difference -0.194, CI [-1.009, +0.515] (1-period blocks), [-1.096, +0.499] (60d blocks) -> FAIL.

Deviations (chosen before any result):
- Block length is not registered: the QLIKE gate uses both h-day blocks (vol_v1's gate) and 60-day blocks and keeps the more conservative (larger p; both CI lower bounds must be above 0). Same for the P&L check (one-period and ceil(60/h)-period blocks).
- log of the overnight squared return needs a floor: |g| is floored at 0.0001 (1 bp) before the log; chosen before any fit.
- The model and baseline are fitted once on the train window (the split is a fixed train/validation split); there is no refit inside validation, unlike vol_v1's yearly walk-forward.
- The train window starts on the first 5m session (2017-07-17), so the first 21 sessions have no 22-day HAR component and are not trained on.
- A session with fewer than 60 of 75 5m bars (Muhurat, weekend DR drills, the 2021-02-24 outage) or with no 5m bars (7 Muhurat days) has no rv5m: the HAR windows skip it (means over the last 1/5/22 regular sessions) and that day gets no forecast. The target still includes those days' returns.
- The conditional rule is exactly the registered one (short when VIX^2 > forecast, else flat); unlike vol_v1 there is no margin and no long-volatility leg.

Caveats:
- variance-swap proxy only; real straddle P&L waits for logged option chains (vol_v3, pre-registered later)
- Smearing factors are estimated on 2017-07..2020-12 residuals, which include March 2020; the same factor type scales the model and the recalibrated VIX, so the comparison is like for like, but both can be biased in validation (see realised / forecast variance per horizon).
- The recalibrated VIX is fitted on 2017-07..2020-12, which contains the March 2020 crash; both it and the model can be miscalibrated in 2021-2025 if the level of the variance risk premium moved.
- The variance-swap proxy uses VIX (a 30-calendar-day implied vol) as the strike at both horizons; at h = 5 that is a horizon mismatch (as in vol_v1).
- Option costs: costs.yaml has no option slippage, so the default 0.05% per side applies; delta-hedging, margin and real quotes are not modelled.
- Short variance has no loss cap; the sample's worst period is not the worst possible.
- Validation (2021-01..2025-09) had no crash of March-2020 size; a calm sample flatters short-vol P&L.
