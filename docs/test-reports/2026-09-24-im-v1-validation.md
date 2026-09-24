# im_v1 validation: intraday momentum on NIFTY 50 and BANKNIFTY

Generated 2026-09-24T11:50:51Z. Train 2017-07-17..2023-01-01, validation 2023-01-01..2025-10-01 (holdout never read). Round trip 0.144% (Rs 143.52 per Rs 1 lakh) per trade. bp = basis points of notional.

Bottom line: before costs the rules made -0.73bp to +0.08bp per trade in validation, against a round trip of +14.35bp.

**Own pass rule: FAIL on all four.** A pass also needs Benjamini-Hochberg across the whole v2 family (computed by the lead).

| instrument | variant | validation mean net/day | 95% CI | p (one-sided) | train mean net/day | trades/days | pass |
|---|---|---|---|---|---|---|---|
| NSE:NIFTY50 | sign_r1 | -14.27bp | [-15.63, -12.98]bp | 1.0000 | -13.73bp | 672/672 | FAIL |
| NSE:NIFTY50 | agree | -7.78bp | [-8.82, -6.73]bp | 1.0000 | -6.35bp | 354/672 | FAIL |
| NSE:BANKNIFTY | sign_r1 | -15.08bp | [-16.69, -13.40]bp | 1.0000 | -14.88bp | 672/672 | FAIL |
| NSE:BANKNIFTY | agree | -7.57bp | [-8.90, -6.32]bp | 1.0000 | -6.22bp | 338/672 | FAIL |

Per trade, validation (gross = before costs):

| instrument | variant | gross/trade | net/trade | hit rate | hit rate if independent | gross/day CI |
|---|---|---|---|---|---|---|
| NSE:NIFTY50 | sign_r1 | +0.08bp | -14.27bp | 50.6% | 50.3% | [-1.27, +1.38]bp |
| NSE:NIFTY50 | agree | -0.41bp | -14.76bp | 50.0% | 50.4% | [-1.14, +0.65]bp |
| NSE:BANKNIFTY | sign_r1 | -0.73bp | -15.08bp | 47.5% | 49.8% | [-2.33, +0.95]bp |
| NSE:BANKNIFTY | agree | -0.70bp | -15.05bp | 45.9% | 49.7% | [-1.54, +0.80]bp |

Predictive regressions (slope, HC1 t-stat):

| instrument | period | n | target on r1 | target on r12 | R^2 (both) |
|---|---|---|---|---|---|
| NSE:NIFTY50 | train | 1340 | 0.0165 (t 1.31) | 0.0699 (t 1.46) | 0.69% |
| NSE:NIFTY50 | validation | 672 | -0.0384 (t -1.43) | -0.1079 (t -1.39) | 2.19% |
| NSE:BANKNIFTY | train | 1340 | 0.0003 (t 0.02) | 0.0653 (t 1.41) | 0.51% |
| NSE:BANKNIFTY | validation | 672 | -0.0367 (t -1.82) | -0.0478 (t -0.69) | 1.12% |

Deviations:
- r12 starts at the close of the 14:25 bar (the price at 14:30), so that bar is required too; the registration names only the 14:30..15:00 window.
- A day whose previous session was special is skipped as well (its r1 would start from a special session's close); the registration names only special sessions themselves.
- Costs use today's rates (futures STT 0.05% since 2026-04-01) for the whole history and brokerage at the Rs 1 lakh reference notional (Rs 20 an order = 0.02%); one NIFTY lot is ~Rs 15-20 lakh, so brokerage is overstated. Both make the after-cost numbers conservative.

Caveats:
- Spot index bars proxy index futures: the futures basis, futures bid-ask and the 15:30 closing auction of the futures are not modelled; entry and exit are at the 5m bar closes at 15:00 and 15:30.
- The spot index's last print is not the official closing price (a 30-minute VWAP of constituents); the target uses the last 5m bar's close as registered.
- sign_r1 and agree on the same instrument share most trades, and NIFTY and BANKNIFTY are correlated, so the four p-values are dependent; BH is valid under positive dependence.
- Validation only: the holdout (>= 2025-10-01) was not read.
