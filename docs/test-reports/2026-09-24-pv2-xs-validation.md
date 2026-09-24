# pv2_xs validation: candlestick-pattern counts added to the xs_v1 ranking

Generated 2026-09-24T12:24:19Z. Same universe, splits, model, portfolio and costs as xs_v1; only the candlestick_patterns group (confirmed bullish/bearish counts over 5 and 20 sessions) is added. Primary: paired rank-IC improvement over xs_v1.

**Per-horizon result: FAIL at every horizon** (the v2 family BH decision is the lead's).

> Survivorship bias: today's Nifty 200 constituents, so every number is an upper bound.

## h = 5 days (weekly_first_trading_day): FAIL

Primary: IC improvement -0.0004, 95% CI [-0.0028, +0.0021], one-sided p = 0.6462. Pattern features' share of model gain: +0.174.

| strategy | rank IC [95% CI] | excess after cost / yr [95% CI] | gross excess / yr | weekly hit | turnover / rebal | max DD excess |
|---|---|---|---|---|---|---|
| pv2_xs | +0.0330 [+0.0214, +0.0441] | +14.58% [+7.31%, +22.01%] | +23.32% | 55.3% | 48% | 20.4% |
| xs_v1 | +0.0334 [+0.0217, +0.0445] | +14.30% [+7.36%, +21.06%] | +23.06% | 54.7% | 48% | 20.3% |

Gates: IC improvement CI lower > 0: FAIL; excess after cost not worse than xs_v1: pass.

Survivorship check (diagnostic, not a gate):
- smallest traded value alone: +15.50%/yr [+9.44%, +21.40%]; most recent listing alone: +11.93%/yr [+5.85%, +17.98%]; momentum + small traded value (hand-built): +21.95%/yr [+15.90%, +28.11%].
- pv2_xs holdings: mean traded-value rank 0.38, momentum rank 0.71, median listing age 14.0y (candidates 18.4y).
- liquid half only (~84 candidates): pv2_xs -0.32%/yr [-11.67%, +10.54%], momentum_12_1 +1.04%/yr [-9.65%, +10.88%].

## h = 20 days (monthly_first_trading_day): FAIL

Primary: IC improvement -0.0026, 95% CI [-0.0078, +0.0029], one-sided p = 0.8346. Pattern features' share of model gain: +0.095.

| strategy | rank IC [95% CI] | excess after cost / yr [95% CI] | gross excess / yr | weekly hit | turnover / rebal | max DD excess |
|---|---|---|---|---|---|---|
| pv2_xs | +0.0606 [+0.0381, +0.0826] | +20.46% [+13.44%, +27.22%] | +22.76% | 60.1% | 53% | 12.2% |
| xs_v1 | +0.0632 [+0.0410, +0.0849] | +19.76% [+13.25%, +25.93%] | +22.02% | 59.9% | 52% | 8.0% |

Gates: IC improvement CI lower > 0: FAIL; excess after cost not worse than xs_v1: pass.

Survivorship check (diagnostic, not a gate):
- smallest traded value alone: +16.36%/yr [+9.90%, +22.64%]; most recent listing alone: +13.42%/yr [+7.48%, +19.52%]; momentum + small traded value (hand-built): +23.33%/yr [+16.50%, +29.77%].
- pv2_xs holdings: mean traded-value rank 0.27, momentum rank 0.74, median listing age 13.8y (candidates 18.3y).
- liquid half only (~84 candidates): pv2_xs +4.39%/yr [-4.89%, +14.51%], momentum_12_1 +7.31%/yr [-2.90%, +18.16%].

Deviations and caveats are listed in the JSON.
