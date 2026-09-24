# vrp_v1 validation: does the variance risk premium predict NIFTY 50 returns?

Generated 2026-09-24T11:39:34Z from edge_search_v2.yaml sha256 `2c7ea1287598`. Train 2009-01-01 to 2016-01-01, validation 2016-01-01 to 2025-10-01 (IST dates) with yearly expanding refits (holdout untouched).

A horizon passes only if Clark-West p < 0.05, the timing rule's after-cost Sharpe beats buy-and-hold, AND its primary p survives Benjamini-Hochberg at q = 0.1 across the v2 family (pending the other v2 tests).

Round trip (index futures, costs.yaml): 0.00144.

## 21-day horizon: pass_if NOT MET

n_scored 2391 days (115 21-day blocks), 10 yearly folds.

- Clark-West t -0.208, one-sided p 0.5824 (FAIL at < 0.05).
- OOS R^2 -0.1615, 95% CI [-0.6260, +0.0163]; by yearly fold: -0.028, -0.062, -0.036, -0.060, -0.302, 0.012, 0.010, -0.013, -0.032, -0.012.
- Slope on vrp by fold: 6.17, 6.12, 6.06, 5.95, 5.76, 2.35, 2.39, 2.40, 2.38, 2.30 (HAC t in the first fold 2.86).
- Timing: long 95% of days, 29 switches, Sharpe after cost 0.200 vs buy-and-hold 0.400; difference -0.200, CI [-0.412, -0.016] (FAIL).
- Diagnostic only, carry-adjusted threshold: Sharpe 0.065, long 91% of days.

## 63-day horizon: pass_if NOT MET

n_scored 2349 days (38 63-day blocks), 10 yearly folds.

- Clark-West t -1.003, one-sided p 0.8420 (FAIL at < 0.05).
- OOS R^2 -0.1592, 95% CI [-0.4460, +0.0046]; by yearly fold: -0.069, -0.071, -0.002, -0.028, -0.328, -0.001, 0.006, -0.005, 0.001, 0.005.
- Slope on vrp by fold: 7.62, 7.35, 7.21, 7.23, 6.82, 0.61, 0.67, 0.81, 0.80, 0.71 (HAC t in the first fold 1.62).
- Timing: long 99% of days, 5 switches, Sharpe after cost 0.321 vs buy-and-hold 0.400; difference -0.079, CI [-0.273, +0.000] (FAIL).
- Diagnostic only, carry-adjusted threshold: Sharpe 0.298, long 97% of days.

Deviations (chosen before any result):
- VIX^2 in monthly units is (VIX/100)^2 / 12 (BTZ's convention), not x 22/252 or x 30/365; chosen before any fit.
- Clark-West p uses the standard normal for the HAC t statistic (as Clark and West tabulate).
- Execution: a decision made at t's close is traded at t+1's close (one-day lag), the conservative reading of a rule that uses t's closing VIX and NIFTY.
- The rule compares the forecast of the SPOT log return with the round trip, as registered; it ignores the ~5%/yr carry a futures holder pays. A carry-adjusted threshold is reported as a diagnostic only.
- Futures rolls: one full round trip per 21 sessions held, charged pro rata per day (tsmom's 'one roll per month'); the same for buy-and-hold.
- A day without a VIX print keeps the previous decision (no forecast can be made that day).
- The OOS R^2 CI (h-day blocks) is reported for context; the registered primary is the Clark-West p.

Caveats:
- NIFTY 50 here is the price index; dividends (~1-1.5%/yr) are not in the target or in buy-and-hold. The futures proxy's 5% carry stands in for (interest - dividend yield), a constant that ignores rate changes.
- One index, ~9.7 years of validation: 63-day targets give only ~38 non-overlapping observations, so the test has little power at h = 63.
- Buy-and-hold beat-or-not is a point comparison as registered; its bootstrap CI is wide.
- VIX methodology and the NIFTY option market changed over 2009-2025 (weekly options, retail flows); a constant VRP slope is a strong assumption.
