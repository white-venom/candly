# tsmom_v1 validation: 12-1 time-series momentum on indices and MCX

Generated 2026-09-24T11:50:32Z. Validation only: indices from 2008-01-01, MCX from 2019-01-01, until the holdout (2025-10-01, never read). Rule-based; nothing fitted.

**Own pass rule: FAIL.** Primary: combined-portfolio annualised Sharpe after cost -0.32, 95% CI [-0.80, +0.16], one-sided p = 0.9090 (213 months, month blocks, B = 2000). A pass also needs Benjamini-Hochberg across the whole v2 family (computed by the lead).

Bottom line: the combined tsmom portfolio's Sharpe is -0.18 before costs and -0.32 after (1.20% a year of costs); buy-and-hold equal risk scored 0.23 after costs.

| portfolio | Sharpe after cost | 95% CI | Sharpe before cost | return/yr | vol/yr | cost/yr | max DD |
|---|---|---|---|---|---|---|---|
| tsmom | -0.32 | [-0.80, +0.16] | -0.18 | -2.65% | 8.27% | 1.20% | 52.32% |
| buy and hold, equal risk | 0.23 | [-0.23, +0.71] | 0.34 | 2.06% | 9.02% | 0.98% | 24.41% |
| always flat | 0 (no risk) | - | - | 0 | 0 | 0 | 0 |

Pass checks:

- sharpe_after_cost_ci_lower_above: Sharpe -0.32, CI lower -0.80 > 0.0: FAIL
- sharpe_beats_buy_and_hold_point: tsmom -0.32 vs buy-and-hold 0.23: FAIL

Per instrument (not gated; q = BH across the seven):

| instrument | from | tsmom Sharpe | 95% CI | p | q | buy-and-hold Sharpe | long/short days | flips |
|---|---|---|---|---|---|---|---|---|
| NSE:NIFTY50 | 2008-01-01 | -0.39 | [-0.84, +0.08] | 0.944 | 0.998 | 0.12 | 68%/32% | 31 |
| NSE:BANKNIFTY | 2008-01-01 | -0.64 | [-1.09, -0.18] | 0.998 | 0.998 | 0.16 | 67%/33% | 26 |
| BSE:SENSEX | 2008-01-01 | -0.37 | [-0.82, +0.10] | 0.942 | 0.998 | 0.12 | 68%/32% | 33 |
| MCX:CRUDEOIL | 2019-01-01 | 0.02 | [-0.94, +0.63] | 0.507 | 0.998 | 0.07 | 46%/54% | 12 |
| MCX:NATURALGAS | 2019-01-01 | 0.02 | [-0.76, +0.75] | 0.493 | 0.998 | -0.08 | 42%/58% | 10 |
| MCX:GOLD | 2019-01-01 | 0.71 | [-0.02, +1.51] | 0.030 | 0.210 | 1.26 | 83%/17% | 5 |
| MCX:SILVER | 2019-01-01 | -0.41 | [-1.20, +0.35] | 0.847 | 0.998 | 0.58 | 61%/38% | 10 |

Costs per round trip: index futures 0.144% (Rs 143.52 per Rs 1 lakh), MCX futures 0.124% (Rs 124.39).

Diagnostics (never part of the gate):

- Sharpe difference vs buy-and-hold -0.55, CI [-1.13, -0.03].
- 2008-01-01..2019-01-01 (indices only): tsmom Sharpe -0.18 CI [-0.73, +0.40], buy-and-hold 0.11.
- 2019-01-01..2025-10-01 (all seven): tsmom Sharpe -0.79 CI [-1.61, -0.05], buy-and-hold 0.56.
- Indices-only portfolio Sharpe -0.52; MCX-only 0.15 (from 2019-01-01).
- mcx_rolls_evidenced_only: tsmom Sharpe -0.32 CI [-0.81, +0.16], buy-and-hold 0.24.
- mcx_no_roll_adjustment: tsmom Sharpe -0.29 CI [-0.77, +0.19], buy-and-hold 0.28.
- crude_2020_04_20_floored_price_kept: tsmom Sharpe -0.31 CI [-0.79, +0.17], buy-and-hold 0.21.
- index_signal_on_spot_without_carry: tsmom Sharpe -0.21 CI [-0.67, +0.27], buy-and-hold 0.23.

MCX roll rule (fixed from the series before any strategy number):

- MCX:CRUDEOIL: 93 rolls, evidence {'weak_volume_jump': 49, 'volume_switch': 34, 'oi_switch': 10}.
- MCX:NATURALGAS: 93 rolls, evidence {'weak_volume_jump': 44, 'volume_switch': 34, 'oi_switch': 15}.
- MCX:GOLD: 46 rolls, evidence {'volume_switch': 34, 'oi_switch': 10, 'data_hole': 2}.
- MCX:SILVER: 39 rolls, evidence {'volume_switch': 30, 'oi_switch': 9}.

Readings of the registration:
- Evaluation windows start at indices_from (2008-01-01) and mcx_from (2019-01-01); the data before each start is the signal warm-up (MCX data begins 2018-01-01, so its first year is exactly the warm-up).
- '60-day EWMA' is read as MOP 2012's estimator: exponential weights with a centre of mass of 60 days on squared daily returns (not demeaned), annualised by 252, at least 60 returns.
- The signal uses the instrument's futures-proxy return (spot minus carry for indices, roll-adjusted for MCX), as MOP's futures excess return; a spot-price signal for indices is a diagnostic.
- Rebalance: decided and traded at the close of the first trading day of each IST month on the instrument's own calendar; the new weight earns from the next bar.

Deviations:
- MCX roll days are detected from the series itself (no historical MCX expiry calendar in the repo): gold and silver rolls all have contract-change evidence, but many crude oil (2018-2020, 2024-25) and natural gas (2018-2020) windows have none and use the largest volume jump in the usual days of the month (a weak guess). Diagnostics show the result with only evidenced rolls and with no roll rule.
- Data repairs not in the registration, applied identically to strategy and baselines: open-to-close on the first bar after a data hole (> 5 calendar days; GOLD 2019-04-05..2019-05-09 and 2019-05-29..2019-06-05), open-to-close on both bars of a one-day off-market print (tiny volume, a big gap reversed next day), and zero return on the CRUDEOIL 2020-04-20 bar whose close is the vendor floor 1.0 (MCX settled the April 2020 contract at a negative price). Keeping the floored price is a diagnostic.
- Costs: 'one round trip per position change' is read conservatively as a full round trip on the traded notional |weight change| at every monthly rebalance (a flip pays for twice the position), plus a full round trip on every open position each month for the roll, also for gold and silver, whose contracts roll every two months.
- Cost rates are today's (futures STT 0.05% since 2026-04-01) applied to the whole history, and brokerage is costed at the Rs 1 lakh reference notional; both overstate historical costs.

Caveats:
- Index futures are proxied by spot minus 5% a year; the real basis moved with rates and dividends, and SENSEX futures were illiquid for most of the sample.
- NIFTY 50, SENSEX and BANKNIFTY are highly correlated, so the equal-risk portfolio is mostly one equity bet before 2019; the sample has about 213 monthly observations and one crisis (2008) at its start.
- The MCX continuous series is Fyers's unadjusted front month; its switch day is not always the exchange's expiry day, which is why the roll days come from the data.
- Gold and silver are costed with a roll every month although their contracts roll every two months (registered; conservative).
- Validation only: the holdout (>= 2025-10-01) was not read.
