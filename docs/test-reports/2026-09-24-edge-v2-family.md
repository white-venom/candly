# Edge search v2: family result (validation only, 2026-09-24)

Pre-registered in `config/edge_search_v2.yaml` (commit 5fce74f) before any test ran. The family has 17 primary one-sided p-values, with Benjamini–Hochberg at q = 0.10. The holdout was not read.

| Hypothesis | p | BH q | Own pass rule | Verdict |
|---|---|---|---|---|
| xs_v1 h=5 (Nifty 200 ranking) | 0.0005 | 0.004 | pass | **survives, but survivorship-suspect** |
| xs_v1 h=20 | 0.0005 | 0.004 | pass | **survives, but survivorship-suspect** |
| pv2_pooled (patterns, 200 stocks) | 0.060 | 0.340 | 1 certified bucket | fail (FDR) |
| pv2_range 1h / 5m / 15m | 0.436 / 0.472 / 0.865 | 1.0 | fail | fail |
| vrp_v1 h=21 / h=63 | 0.582 / 0.842 | 1.0 | fail | fail |
| vol_v2 h=20 / h=5 | 0.598 / 0.879 | 1.0 | fail | fail |
| pv2_xs h=5 / h=20 | 0.646 / 0.835 | 1.0 | fail | fail |
| tsmom_v1 | 0.909 | 1.0 | fail | fail |
| im_v1 NIFTY / BANKNIFTY × sign_r1 / agree | 1.0 (all four) | 1.0 | fail | fail |

## Why xs_v1 is not trusted yet

The xs_v1 universe is today's Nifty 200 constituents. Stocks that were small in the past and are in the index today are, by construction, stocks that went on to win. The xs agent's own diagnostics:
- Two crude rules match the model: "buy the smallest traded value" (+15.5%/yr at h=5) and "buy the newest listings" (+11.9%/yr).
- In the more liquid half of the candidates, which is less exposed to future index entrants:
  - at h=5 the excess is −0.1%/yr, CI [−10.9, +10.3];
  - at h=20 it is +8.2%/yr, CI [−1.3, +18.2].
- xs_v1 minus plain 12-1 momentum is not significant: +1.6%/yr and +3.8%/yr, both CIs span 0.

**Next:** re-test with a point-in-time universe (xs_v2, pre-registered separately) before any holdout run.

## Earlier attempts (v1)

These all failed and are not re-run:
- candlestick scorecards
- analog_v1
- regime_v1
- vol_v1

range_v1 passed on intraday validation; its holdout run is still pending.
