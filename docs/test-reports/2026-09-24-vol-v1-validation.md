# vol_v1 validation: NIFTY 50 realised volatility vs India VIX

Generated 2026-09-24T10:34:16Z. Period: validation (walk-forward, 2016-01-01 to 2025-10-01, holdout untouched). Pass rule: at least one horizon passes both checks. Gated tests: 4 (BH q-values at FDR 0.1 shown for context).

**Overall: FAIL**

> a pass is a lead, not a strategy: real option prices, margins and tail risk are checked on logged option chains before anything is traded

Realised variance: RV_h(t) = 252/h * sum_{i=1..h} r_{t+i}^2, r = close-to-close log return (overnight gaps included, not demeaned); realised vol = 100*sqrt(RV_h), annualised %.

## 5-day horizon: FAIL

n_scored 2407 days (483 5-day blocks), 10 yearly folds, purge 5 rows, runtime 2.4s.

| forecaster | QLIKE | mean forecast vol | model improvement | 95% CI | CI (60d blocks) |
|---|---|---|---|---|---|
| realized_vol_model | 0.3521 | 14.14 | - | - | - |
| india_vix | 0.3855 | 16.70 | 0.0334 | [-0.0133, +0.0719] | [-0.0179, +0.0793] |
| har_rv | 0.4698 | 17.19 | 0.1177 | [+0.0838, +0.1506] | [+0.0776, +0.1574] |
| ewma_rv | 0.4298 | 14.44 | 0.0776 | [+0.0368, +0.1322] | [+0.0288, +0.1348] |
| india_vix_rescaled (diagnostic) | 0.3419 | 14.35 | -0.0102 | [-0.0480, +0.0211] | [-0.0544, +0.0304] |

Mean realised vol 13.32.

Variance-swap proxy: 483 non-overlapping periods, mean implied 6.34, mean realised 5.39, mean cost 0.049 (squared vol points per period).

| strategy | short/long/flat | mean net | Sharpe | max drawdown | worst period |
|---|---|---|---|---|---|
| always_short | 483/0/0 | 0.909 | 0.486 | 405.34 | -198.21 |
| conditional | 21/4/458 | 0.517 | 0.383 | 61.15 | -37.68 |

Sharpe difference (conditional - always short) -0.103, 95% CI [-2.229, +0.766]; 60-day-block CI [-2.895, +0.766]; over all 5 start phases min -0.271, median -0.103, max 0.084.

Gates: qlike_improvement_vs_vix FAIL (CI [-0.0133, +0.0719], q 0.158); conditional_vs_always_short_sharpe FAIL (CI [-2.2288, +0.7659], q 0.707).

## 20-day horizon: FAIL

n_scored 2392 days (120 20-day blocks), 10 yearly folds, purge 20 rows, runtime 1.8s.

| forecaster | QLIKE | mean forecast vol | model improvement | 95% CI | CI (60d blocks) |
|---|---|---|---|---|---|
| realized_vol_model | 0.2590 | 14.79 | - | - | - |
| india_vix | 0.2857 | 16.74 | 0.0267 | [-0.0078, +0.0630] | [-0.0177, +0.0691] |
| har_rv | 0.3925 | 18.30 | 0.1335 | [+0.0906, +0.1759] | [+0.0732, +0.1942] |
| ewma_rv | 0.3507 | 14.47 | 0.0916 | [+0.0152, +0.1978] | [+0.0045, +0.2136] |
| india_vix_rescaled (diagnostic) | 0.2808 | 14.93 | 0.0217 | [-0.0434, +0.1297] | [-0.0468, +0.1248] |

Mean realised vol 14.09.

Variance-swap proxy: 120 non-overlapping periods, mean implied 25.60, mean realised 21.65, mean cost 0.196 (squared vol points per period).

| strategy | short/long/flat | mean net | Sharpe | max drawdown | worst period |
|---|---|---|---|---|---|
| always_short | 120/0/0 | 3.750 | 0.468 | 365.43 | -230.46 |
| conditional | 4/3/113 | 1.020 | 0.241 | 71.72 | -71.72 |

Sharpe difference (conditional - always short) -0.227, 95% CI [-2.215, +0.655]; 60-day-block CI [-2.416, +0.523]; over all 20 start phases min -0.762, median -0.119, max 0.367.

Gates: qlike_improvement_vs_vix FAIL (CI [-0.0078, +0.0630], q 0.158); conditional_vs_always_short_sharpe FAIL (CI [-2.2152, +0.6548], q 0.707).

Caveats:
- India VIX is a 30-calendar-day risk-neutral volatility. It carries the variance risk premium, so it overstates realised variance on average; a QLIKE win over raw VIX can come from that bias alone. india_vix_rescaled (diagnostic) shows how much is left once the bias is removed.
- At h = 5 VIX measures a 30-day horizon: its use as a 5-day forecast and a 5-day swap strike is a mismatch the pre-registration accepted.
- Costs: costs.yaml has no option slippage, so the default 0.05% per side applies; real bid-ask on Nifty options, delta-hedging costs and margin are not included.
- The P&L is per unit of variance notional. March 2020 is in the sample, but a short variance position has no loss cap, so a worse tail than the sample's is possible.
- Blocks of h days are short for volatility, which clusters for months; the 60-day-block CIs are a diagnostic of that.
