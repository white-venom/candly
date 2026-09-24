# regime_v1 validation (go/no-go #2)

Generated 2026-09-24T06:22:32Z. Period: validation (walk-forward). Pass rule: at least one horizon passes every check. Hypotheses: 2 horizons.

**Overall: FAIL**

## 5-day horizon: FAIL

n_scored 20025 (1670 dates, 335 5-day blocks), 14 folds, gap 20 bars, skill q 0.999.

| method | Brier | skill vs base | 95% CI (blocks) | calib p | calib p clustered (diag) | top-decile net | CI |
|---|---|---|---|---|---|---|---|
| regime_v1 | 0.2548 | -0.0267 | [-0.0460, -0.0082] | 0.000 | 0.000 | -0.00028 | [-0.0087, +0.0081] |
| base_rate | 0.2482 | 0.0000 | [+0.0000, +0.0000] | 0.000 | 0.114 | -0.00064 | [-0.0036, +0.0022] |
| trend_rule_ema200 | 0.2482 | -0.0001 | [-0.0012, +0.0010] | 0.004 | 0.045 | -0.00040 | [-0.0027, +0.0018] |
| momentum_sign_20d | 0.2485 | -0.0012 | [-0.0028, +0.0003] | 0.000 | 0.002 | -0.00154 | [-0.0039, +0.0007] |

Gates: brier_skill_ci_lower FAIL, calibration FAIL, min_scored pass, top_decile_expectancy_after_costs FAIL.

regime_v1 Brier skill vs baselines: base_rate -0.0267 [-0.0460, -0.0082]; trend_rule_ema200 -0.0266 [-0.0460, -0.0081]; momentum_sign_20d -0.0255 [-0.0450, -0.0067].

## 20-day horizon: FAIL

n_scored 19851 (1655 dates, 83 20-day blocks), 14 folds, gap 30 bars, skill q 0.999.

| method | Brier | skill vs base | 95% CI (blocks) | calib p | calib p clustered (diag) | top-decile net | CI |
|---|---|---|---|---|---|---|---|
| regime_v1 | 0.2516 | -0.0296 | [-0.0549, -0.0067] | 0.000 | 0.000 | 0.01510 | [-0.0033, +0.0303] |
| base_rate | 0.2444 | 0.0000 | [+0.0000, +0.0000] | 0.000 | 0.017 | 0.00465 | [-0.0059, +0.0153] |
| trend_rule_ema200 | 0.2442 | 0.0007 | [-0.0022, +0.0038] | 0.000 | 0.070 | 0.01204 | [-0.0042, +0.0275] |
| momentum_sign_20d | 0.2451 | -0.0028 | [-0.0066, +0.0012] | 0.000 | 0.001 | 0.00169 | [-0.0133, +0.0150] |

Gates: brier_skill_ci_lower FAIL, calibration FAIL, min_scored pass, top_decile_expectancy_after_costs pass.

regime_v1 Brier skill vs baselines: base_rate -0.0296 [-0.0549, -0.0067]; trend_rule_ema200 -0.0303 [-0.0558, -0.0078]; momentum_sign_20d -0.0268 [-0.0530, -0.0034].

Caveats:
- Universe is today's watchlist large caps (survivorship bias): results are an upper bound.
- The pre-registered calibration gate simulates independent outcomes; overlapping labels and same-day cross-instrument correlation make it reject calibrated forecasts more often than 5%. calibration_clustered (block-clustered Wald test of intercept 0, slope 1) and calibration_p_thinned are diagnostics only and do not change the gate.
