# xs_v1 validation: Nifty 200 cross-sectional ranking

Generated 2026-09-24T12:24:19Z. Validation 2016-01-01 to 2025-10-01 (holdout untouched), yearly expanding refits, purge 20 sessions. Costs: equity delivery round trip 0.344% per unit of one-way turnover. Pass rule: at least one horizon passes every check.

**Overall: PASS**

> Survivorship bias: today's Nifty 200 constituents, so every number is an upper bound.

## h = 5 days (weekly_first_trading_day): PASS

Primary: excess after cost +14.30%/yr, 95% CI [+7.36%, +21.06%], one-sided p = 0.0005 (508 rebalances).

| strategy | rank IC [95% CI] | excess after cost / yr [95% CI] | gross excess / yr | weekly hit | turnover / rebal | max DD excess |
|---|---|---|---|---|---|---|
| xs_v1 | +0.0334 [+0.0217, +0.0445] | +14.30% [+7.36%, +21.06%] | +23.06% | 54.7% | 48% | 20.3% |
| reversal_1w | +0.0392 [+0.0277, +0.0510] | -5.67% [-12.68%, +1.61%] | +10.15% | 44.5% | 88% | 55.4% |
| momentum_12_1 | +0.0243 [+0.0095, +0.0395] | +12.72% [+4.66%, +20.43%] | +15.34% | 56.1% | 14% | 19.0% |
| low_volatility | +0.0057 [-0.0108, +0.0234] | -8.65% [-14.84%, -2.38%] | -5.83% | 43.9% | 16% | 57.8% |

Gates: rank IC CI lower > 0: pass; excess CI lower > 0: pass; beats best baseline (momentum_12_1, +12.72%/yr): pass (paired difference +1.58%/yr, 95% CI [-6.63%, +10.14%]).

Survivorship check (diagnostic, not a gate):
- smallest traded value alone: +15.50%/yr [+9.44%, +21.40%]; most recent listing alone: +11.93%/yr [+5.85%, +17.98%]; momentum + small traded value (hand-built): +21.95%/yr [+15.90%, +28.11%].
- xs_v1 holdings: mean traded-value rank 0.37, momentum rank 0.71, median listing age 14.0y (candidates 18.4y).
- liquid half only (~84 candidates): xs_v1 -0.10%/yr [-10.93%, +10.25%], momentum_12_1 +1.04%/yr [-9.65%, +10.88%].

## h = 20 days (monthly_first_trading_day): PASS

Primary: excess after cost +19.76%/yr, 95% CI [+13.25%, +25.93%], one-sided p = 0.0005 (116 rebalances).

| strategy | rank IC [95% CI] | excess after cost / yr [95% CI] | gross excess / yr | weekly hit | turnover / rebal | max DD excess |
|---|---|---|---|---|---|---|
| xs_v1 | +0.0632 [+0.0410, +0.0849] | +19.76% [+13.25%, +25.93%] | +22.02% | 59.9% | 52% | 8.0% |
| reversal_1w | +0.0178 [-0.0036, +0.0406] | -0.10% [-7.32%, +7.20%] | +3.65% | 48.8% | 88% | 30.1% |
| momentum_12_1 | +0.0409 [+0.0088, +0.0723] | +15.94% [+7.74%, +23.92%] | +17.12% | 57.9% | 27% | 15.3% |
| low_volatility | -0.0092 [-0.0447, +0.0262] | -4.85% [-11.22%, +1.61%] | -3.34% | 46.4% | 36% | 47.2% |

Gates: rank IC CI lower > 0: pass; excess CI lower > 0: pass; beats best baseline (momentum_12_1, +15.94%/yr): pass (paired difference +3.82%/yr, 95% CI [-4.17%, +11.63%]).

Survivorship check (diagnostic, not a gate):
- smallest traded value alone: +16.36%/yr [+9.90%, +22.64%]; most recent listing alone: +13.42%/yr [+7.48%, +19.52%]; momentum + small traded value (hand-built): +23.33%/yr [+16.50%, +29.77%].
- xs_v1 holdings: mean traded-value rank 0.27, momentum rank 0.74, median listing age 13.5y (candidates 18.3y).
- liquid half only (~84 candidates): xs_v1 +8.24%/yr [-1.26%, +18.17%], momentum_12_1 +7.31%/yr [-2.90%, +18.16%].

Deviations from a literal reading and caveats are listed in the JSON (`deviations`, `caveats`).
