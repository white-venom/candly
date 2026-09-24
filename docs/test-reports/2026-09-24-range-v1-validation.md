# range_v1 validation (go/no-go #2, range)

Generated 2026-09-24T10:41:50+00:00 by `python -m candly.research.range_eval`. Validation period only (walk-forward, train_end to 2025-10-01); the holdout was not read. Units: ATR(14) at the reference bar.

**Go/no-go #2 (range, NSE): PASS** (3 of 4 timeframes pass; rule: at least 2 of 4 timeframes pass, and 1D or 1h is one of them).

| exchange | tf | n | coverage p10-p90 | best baseline | Winkler range_v1 / baseline | improvement [95% CI] | month-block CI (info) | same / close / wrong | gate |
|---|---|---|---|---|---|---|---|---|---|
| NSE | 5m | 610,652 | 80.3% | analog_v1 | 3.390 / 3.659 | 7.3% [6.6%, 8.0%] | [6.5%, 8.1%] | 7.1% / 73.2% / 19.7% | PASS |
| NSE | 15m | 203,528 | 79.9% | atr_bands | 3.447 / 3.797 | 9.2% [8.7%, 9.8%] | [8.6%, 9.9%] | 7.2% / 72.7% / 20.1% | PASS |
| NSE | 1h | 56,988 | 80.0% | analog_v1 | 3.677 / 3.931 | 6.5% [5.8%, 7.1%] | [6.0%, 7.0%] | 7.8% / 72.3% / 20.0% | PASS |
| NSE | 1D | 20,063 | 78.5% | atr_bands | 3.554 / 3.580 | 0.7% [0.3%, 1.1%] | [0.1%, 1.3%] | 7.1% / 71.3% / 21.5% | fail |
| BSE | 5m | 50,889 | 79.8% | analog_v1 | 3.763 / 4.105 | 8.3% [5.9%, 11.1%] | [5.8%, 11.1%] | 6.6% / 73.2% / 20.2% | (not gated) |
| BSE | 15m | 16,961 | 78.7% | atr_bands | 3.781 / 4.198 | 9.9% [8.8%, 11.1%] | [8.6%, 11.3%] | 7.3% / 71.3% / 21.3% | (not gated) |
| BSE | 1h | 4,749 | 77.7% | analog_v1 | 4.012 / 4.262 | 5.9% [4.2%, 7.5%] | [4.4%, 7.2%] | 7.9% / 69.7% / 22.3% | (not gated) |
| BSE | 1D | 1,672 | 77.8% | atr_bands | 3.808 / 3.796 | -0.3% [-1.3%, 0.7%] | [-1.5%, 0.8%] | 6.4% / 71.4% / 22.2% | (not gated) |
| MCX | 5m | 464,652 | 78.3% | analog_v1 | 3.917 / 4.152 | 5.7% [4.9%, 6.5%] | [4.8%, 6.6%] | 6.6% / 71.7% / 21.7% | (not gated) |
| MCX | 15m | 157,084 | 78.7% | analog_v1 | 3.768 / 4.115 | 8.5% [7.7%, 9.2%] | [7.5%, 9.4%] | 6.9% / 71.8% / 21.3% | (not gated) |
| MCX | 1h | 40,806 | 78.9% | analog_v1 | 3.896 / 4.193 | 7.1% [6.3%, 7.8%] | [6.4%, 7.7%] | 7.2% / 71.7% / 21.1% | (not gated) |
| MCX | 1D | 6,910 | 76.3% | atr_bands | 3.785 / 3.750 | -0.9% [-1.5%, -0.3%] | [-1.8%, -0.1%] | 6.1% / 70.2% / 23.7% | (not gated) |

Winkler scores are per forecast step, in ATR, on the forecasts both methods made (analog_v1 draws bands on a subset). Improvement = 1 - Winkler(range_v1) / Winkler(baseline) on those forecasts, pooled over the 3 steps; the best baseline is the one range_v1 improves on least. CIs resample whole IST dates (2000 resamples), as pre-registered; the month-block CI resamples whole months and is shown for information only. Categories pool the 3 steps; `wrong` = close outside p10-p90, `same` = close within 0.25 ATR of p50 and the bar inside the ghost candle's p50 high/low.

Tests: 12 range_v1-vs-baseline comparisons on the gated NSE slice (36 in all). A timeframe passes only if range_v1 clears the bar against every baseline (the gate uses the baseline it improves on least), an intersection-union test, so more baselines make a pass harder, not easier; no FDR adjustment applies. The pass rule then asks for 2 of 4 timeframes, 1D or 1h among them.

## Coverage and categories by step (range_v1)

| exchange | tf | coverage step 1 / 2 / 3 | same / close / wrong, step 1 |
|---|---|---|---|
| NSE | 5m | 80.3% / 80.4% / 80.3% | 10.0% / 70.3% / 19.7% |
| NSE | 15m | 80.0% / 79.8% / 79.8% | 10.1% / 69.8% / 20.0% |
| NSE | 1h | 79.9% / 80.2% / 80.0% | 11.1% / 68.8% / 20.1% |
| NSE | 1D | 78.9% / 78.6% / 77.8% | 10.2% / 68.6% / 21.1% |
| BSE | 5m | 80.0% / 79.9% / 79.7% | 9.3% / 70.7% / 20.0% |
| BSE | 15m | 78.8% / 78.9% / 78.3% | 10.2% / 68.5% / 21.2% |
| BSE | 1h | 77.7% / 77.7% / 77.5% | 10.7% / 67.0% / 22.3% |
| BSE | 1D | 77.6% / 78.0% / 77.8% | 9.4% / 68.2% / 22.4% |
| MCX | 5m | 78.3% / 78.3% / 78.4% | 9.3% / 69.0% / 21.7% |
| MCX | 15m | 78.7% / 78.8% / 78.7% | 9.4% / 69.3% / 21.3% |
| MCX | 1h | 78.8% / 78.9% / 79.0% | 10.1% / 68.8% / 21.2% |
| MCX | 1D | 77.0% / 76.0% / 75.8% | 9.2% / 67.9% / 23.0% |

## By instrument (range_v1; improvement vs that timeframe's best baseline, no CI)

| exchange | tf | instrument | n | coverage | improvement | same / close / wrong |
|---|---|---|---|---|---|---|
| NSE | 5m | NSE:NIFTY50 | 50,889 | 80.8% | 8.0% | 6.6% / 74.2% / 19.2% |
| NSE | 5m | NSE:BANKNIFTY | 50,889 | 80.9% | 7.1% | 6.9% / 74.0% / 19.1% |
| NSE | 5m | NSE:RELIANCE | 50,881 | 80.4% | 7.4% | 7.0% / 73.4% / 19.6% |
| NSE | 5m | NSE:HDFCBANK | 50,889 | 80.5% | 7.8% | 7.0% / 73.5% / 19.5% |
| NSE | 5m | NSE:ICICIBANK | 50,889 | 81.2% | 7.6% | 7.1% / 74.0% / 18.8% |
| NSE | 5m | NSE:INFY | 50,889 | 79.9% | 9.3% | 7.1% / 72.8% / 20.1% |
| NSE | 5m | NSE:TCS | 50,889 | 79.8% | 9.8% | 7.1% / 72.7% / 20.2% |
| NSE | 5m | NSE:SBIN | 50,889 | 80.5% | 6.0% | 6.9% / 73.6% / 19.5% |
| NSE | 5m | NSE:BHARTIARTL | 50,889 | 79.9% | 7.6% | 7.7% / 72.3% / 20.1% |
| NSE | 5m | NSE:ITC | 50,881 | 80.0% | 5.4% | 7.8% / 72.2% / 20.0% |
| NSE | 5m | NSE:LT | 50,889 | 79.6% | 6.0% | 7.5% / 72.0% / 20.4% |
| NSE | 5m | NSE:AXISBANK | 50,889 | 80.2% | 5.4% | 6.8% / 73.4% / 19.8% |
| NSE | 15m | NSE:NIFTY50 | 16,961 | 80.5% | 10.1% | 7.4% / 73.0% / 19.5% |
| NSE | 15m | NSE:BANKNIFTY | 16,961 | 80.4% | 8.5% | 7.4% / 73.0% / 19.6% |
| NSE | 15m | NSE:RELIANCE | 16,959 | 80.0% | 9.5% | 6.7% / 73.3% / 20.0% |
| NSE | 15m | NSE:HDFCBANK | 16,961 | 80.1% | 9.7% | 7.0% / 73.2% / 19.9% |
| NSE | 15m | NSE:ICICIBANK | 16,961 | 80.9% | 8.8% | 7.0% / 73.9% / 19.1% |
| NSE | 15m | NSE:INFY | 16,961 | 79.2% | 13.6% | 7.3% / 71.9% / 20.8% |
| NSE | 15m | NSE:TCS | 16,961 | 79.1% | 11.2% | 7.0% / 72.1% / 20.9% |
| NSE | 15m | NSE:SBIN | 16,961 | 80.3% | 6.9% | 7.3% / 73.0% / 19.7% |
| NSE | 15m | NSE:BHARTIARTL | 16,961 | 79.5% | 8.1% | 7.5% / 72.0% / 20.5% |
| NSE | 15m | NSE:ITC | 16,959 | 79.6% | 6.9% | 7.6% / 72.0% / 20.4% |
| NSE | 15m | NSE:LT | 16,961 | 78.9% | 9.3% | 7.2% / 71.7% / 21.1% |
| NSE | 15m | NSE:AXISBANK | 16,961 | 79.8% | 7.2% | 7.0% / 72.8% / 20.2% |
| NSE | 1h | NSE:NIFTY50 | 4,749 | 79.8% | 6.6% | 8.5% / 71.2% / 20.2% |
| NSE | 1h | NSE:BANKNIFTY | 4,749 | 80.3% | 5.1% | 8.8% / 71.5% / 19.7% |
| NSE | 1h | NSE:RELIANCE | 4,749 | 80.6% | 6.3% | 7.1% / 73.5% / 19.4% |
| NSE | 1h | NSE:HDFCBANK | 4,749 | 79.3% | 6.6% | 7.9% / 71.4% / 20.7% |
| NSE | 1h | NSE:ICICIBANK | 4,749 | 81.6% | 6.3% | 6.7% / 74.9% / 18.4% |
| NSE | 1h | NSE:INFY | 4,749 | 77.9% | 10.3% | 7.7% / 70.2% / 22.1% |
| NSE | 1h | NSE:TCS | 4,749 | 79.5% | 8.4% | 7.8% / 71.6% / 20.5% |
| NSE | 1h | NSE:SBIN | 4,749 | 81.9% | 4.8% | 8.0% / 73.9% / 18.1% |
| NSE | 1h | NSE:BHARTIARTL | 4,749 | 80.3% | 6.4% | 7.8% / 72.5% / 19.7% |
| NSE | 1h | NSE:ITC | 4,749 | 79.9% | 4.9% | 7.9% / 72.0% / 20.1% |
| NSE | 1h | NSE:LT | 4,749 | 78.6% | 6.6% | 7.7% / 71.0% / 21.4% |
| NSE | 1h | NSE:AXISBANK | 4,749 | 80.7% | 4.3% | 7.3% / 73.4% / 19.3% |
| NSE | 1D | NSE:NIFTY50 | 1,672 | 78.0% | 2.4% | 6.5% / 71.5% / 22.0% |
| NSE | 1D | NSE:BANKNIFTY | 1,671 | 80.2% | 0.8% | 6.5% / 73.7% / 19.8% |
| NSE | 1D | NSE:RELIANCE | 1,672 | 76.5% | 0.3% | 7.4% / 69.2% / 23.5% |
| NSE | 1D | NSE:HDFCBANK | 1,672 | 76.4% | -0.5% | 7.3% / 69.1% / 23.6% |
| NSE | 1D | NSE:ICICIBANK | 1,672 | 80.1% | 0.2% | 6.9% / 73.2% / 19.9% |
| NSE | 1D | NSE:INFY | 1,672 | 77.2% | 0.9% | 6.5% / 70.8% / 22.8% |
| NSE | 1D | NSE:TCS | 1,672 | 77.8% | 0.8% | 6.6% / 71.1% / 22.2% |
| NSE | 1D | NSE:SBIN | 1,672 | 77.9% | 0.7% | 7.9% / 70.0% / 22.1% |
| NSE | 1D | NSE:BHARTIARTL | 1,672 | 79.8% | 1.0% | 8.0% / 71.9% / 20.2% |
| NSE | 1D | NSE:ITC | 1,672 | 78.8% | 0.8% | 8.0% / 70.8% / 21.2% |
| NSE | 1D | NSE:LT | 1,672 | 79.1% | 0.8% | 6.8% / 72.3% / 20.9% |
| NSE | 1D | NSE:AXISBANK | 1,672 | 79.6% | 0.2% | 7.2% / 72.4% / 20.4% |
| BSE | 5m | BSE:SENSEX | 50,889 | 79.8% | 8.3% | 6.6% / 73.2% / 20.2% |
| BSE | 15m | BSE:SENSEX | 16,961 | 78.7% | 9.9% | 7.3% / 71.3% / 21.3% |
| BSE | 1h | BSE:SENSEX | 4,749 | 77.7% | 5.9% | 7.9% / 69.7% / 22.3% |
| BSE | 1D | BSE:SENSEX | 1,672 | 77.8% | -0.3% | 6.4% / 71.4% / 22.2% |
| MCX | 5m | MCX:CRUDEOIL | 120,331 | 77.4% | 5.5% | 6.7% / 70.7% / 22.6% |
| MCX | 5m | MCX:NATURALGAS | 120,494 | 78.0% | 5.3% | 6.6% / 71.4% / 22.0% |
| MCX | 5m | MCX:GOLD | 110,288 | 78.7% | 4.0% | 6.7% / 72.0% / 21.3% |
| MCX | 5m | MCX:SILVER | 113,539 | 79.1% | 7.6% | 6.4% / 72.7% / 20.9% |
| MCX | 15m | MCX:CRUDEOIL | 40,241 | 78.2% | 6.4% | 6.6% / 71.6% / 21.8% |
| MCX | 15m | MCX:NATURALGAS | 40,242 | 78.4% | 9.7% | 6.9% / 71.5% / 21.6% |
| MCX | 15m | MCX:GOLD | 37,875 | 78.9% | 8.4% | 7.0% / 71.8% / 21.1% |
| MCX | 15m | MCX:SILVER | 38,726 | 79.6% | 9.2% | 7.1% / 72.5% / 20.4% |
| MCX | 1h | MCX:CRUDEOIL | 10,301 | 78.4% | 4.5% | 6.4% / 72.1% / 21.6% |
| MCX | 1h | MCX:NATURALGAS | 10,301 | 78.1% | 8.9% | 6.9% / 71.2% / 21.9% |
| MCX | 1h | MCX:GOLD | 10,025 | 79.7% | 6.7% | 7.8% / 71.8% / 20.3% |
| MCX | 1h | MCX:SILVER | 10,179 | 79.6% | 7.9% | 7.8% / 71.8% / 20.4% |
| MCX | 1D | MCX:CRUDEOIL | 1,731 | 78.1% | -0.9% | 5.8% / 72.3% / 21.9% |
| MCX | 1D | MCX:NATURALGAS | 1,735 | 74.2% | -1.4% | 4.8% / 69.4% / 25.8% |
| MCX | 1D | MCX:GOLD | 1,709 | 76.8% | -0.4% | 6.9% / 69.9% / 23.2% |
| MCX | 1D | MCX:SILVER | 1,735 | 76.1% | -1.0% | 6.7% / 69.4% / 23.9% |

## All baselines

| exchange | tf | method | n | coverage | Winkler | pinball close | range IoU | close MAE | improvement vs it [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| NSE | 5m | range_v1 | 610,652 | 80.3% | 3.371 | 0.228 | 0.384 | 0.694 |  |
| NSE | 5m | atr_bands | 610,652 | 81.0% | 3.646 | 0.237 | 0.372 | 0.695 | 7.5% [7.2%, 7.9%] (n=610,652) |
| NSE | 5m | rolling_quantiles | 610,652 | 79.2% | 3.675 | 0.239 | 0.375 | 0.698 | 8.3% [8.0%, 8.6%] (n=610,652) |
| NSE | 5m | analog_v1 | 23,999 | 80.9% | 3.659 | 0.238 | 0.289 | 0.698 | 7.3% [6.6%, 8.0%] (n=23,999) |
| NSE | 15m | range_v1 | 203,528 | 79.9% | 3.447 | 0.233 | 0.381 | 0.706 |  |
| NSE | 15m | atr_bands | 203,528 | 80.6% | 3.797 | 0.244 | 0.366 | 0.707 | 9.2% [8.7%, 9.8%] (n=203,528) |
| NSE | 15m | rolling_quantiles | 203,528 | 79.2% | 3.836 | 0.246 | 0.368 | 0.710 | 10.1% [9.7%, 10.7%] (n=203,528) |
| NSE | 15m | analog_v1 | 23,999 | 80.6% | 3.797 | 0.245 | 0.318 | 0.709 | 9.3% [8.6%, 10.0%] (n=23,999) |
| NSE | 1h | range_v1 | 56,988 | 80.0% | 3.646 | 0.245 | 0.369 | 0.740 |  |
| NSE | 1h | atr_bands | 56,988 | 80.7% | 3.912 | 0.254 | 0.344 | 0.741 | 6.8% [6.2%, 7.4%] (n=56,988) |
| NSE | 1h | rolling_quantiles | 56,988 | 79.1% | 3.952 | 0.256 | 0.344 | 0.744 | 7.8% [7.1%, 8.4%] (n=56,988) |
| NSE | 1h | analog_v1 | 23,994 | 80.3% | 3.931 | 0.255 | 0.313 | 0.745 | 6.5% [5.8%, 7.1%] (n=23,994) |
| NSE | 1D | range_v1 | 20,063 | 78.5% | 3.554 | 0.243 | 0.341 | 0.746 |  |
| NSE | 1D | atr_bands | 20,063 | 78.7% | 3.580 | 0.244 | 0.337 | 0.745 | 0.7% [0.3%, 1.1%] (n=20,063) |
| NSE | 1D | rolling_quantiles | 20,063 | 79.3% | 3.598 | 0.244 | 0.336 | 0.747 | 1.2% [0.8%, 1.6%] (n=20,063) |
| NSE | 1D | analog_v1 | 19,997 | 78.1% | 3.586 | 0.244 | 0.307 | 0.746 | 0.9% [0.5%, 1.2%] (n=19,997) |
| BSE | 5m | range_v1 | 50,889 | 79.8% | 3.728 | 0.253 | 0.370 | 0.772 |  |
| BSE | 5m | atr_bands | 50,889 | 80.9% | 4.073 | 0.265 | 0.359 | 0.774 | 8.5% [7.7%, 9.3%] (n=50,889) |
| BSE | 5m | rolling_quantiles | 50,889 | 79.2% | 4.106 | 0.266 | 0.360 | 0.777 | 9.2% [8.5%, 10.0%] (n=50,889) |
| BSE | 5m | analog_v1 | 1,999 | 80.9% | 4.105 | 0.266 | 0.323 | 0.776 | 8.3% [5.9%, 11.1%] (n=1,999) |
| BSE | 15m | range_v1 | 16,961 | 78.7% | 3.781 | 0.254 | 0.370 | 0.767 |  |
| BSE | 15m | atr_bands | 16,961 | 80.2% | 4.198 | 0.268 | 0.358 | 0.767 | 9.9% [8.8%, 11.1%] (n=16,961) |
| BSE | 15m | rolling_quantiles | 16,961 | 79.1% | 4.243 | 0.270 | 0.357 | 0.769 | 10.9% [9.8%, 12.1%] (n=16,961) |
| BSE | 15m | analog_v1 | 1,985 | 80.8% | 4.259 | 0.271 | 0.334 | 0.771 | 11.9% [9.2%, 14.5%] (n=1,985) |
| BSE | 1h | range_v1 | 4,749 | 77.7% | 3.961 | 0.267 | 0.347 | 0.808 |  |
| BSE | 1h | atr_bands | 4,749 | 80.1% | 4.240 | 0.275 | 0.327 | 0.804 | 6.6% [5.2%, 8.0%] (n=4,749) |
| BSE | 1h | rolling_quantiles | 4,749 | 79.2% | 4.293 | 0.278 | 0.324 | 0.807 | 7.7% [6.3%, 9.2%] (n=4,749) |
| BSE | 1h | analog_v1 | 1,961 | 80.5% | 4.262 | 0.276 | 0.304 | 0.806 | 5.9% [4.2%, 7.5%] (n=1,961) |
| BSE | 1D | range_v1 | 1,672 | 77.8% | 3.808 | 0.263 | 0.304 | 0.819 |  |
| BSE | 1D | atr_bands | 1,672 | 80.8% | 3.796 | 0.264 | 0.301 | 0.822 | -0.3% [-1.3%, 0.7%] (n=1,672) |
| BSE | 1D | rolling_quantiles | 1,672 | 79.0% | 3.838 | 0.264 | 0.299 | 0.817 | 0.8% [-0.3%, 1.8%] (n=1,672) |
| BSE | 1D | analog_v1 | 1,594 | 79.7% | 3.824 | 0.264 | 0.268 | 0.817 | 0.1% [-1.0%, 1.2%] (n=1,594) |
| MCX | 5m | range_v1 | 464,652 | 78.3% | 3.918 | 0.265 | 0.327 | 0.809 |  |
| MCX | 5m | atr_bands | 464,652 | 78.3% | 4.176 | 0.274 | 0.314 | 0.810 | 6.2% [6.0%, 6.4%] (n=464,652) |
| MCX | 5m | rolling_quantiles | 464,652 | 79.2% | 4.208 | 0.276 | 0.312 | 0.813 | 6.9% [6.7%, 7.1%] (n=464,652) |
| MCX | 5m | analog_v1 | 8,000 | 79.5% | 4.152 | 0.272 | 0.220 | 0.803 | 5.7% [4.9%, 6.5%] (n=8,000) |
| MCX | 15m | range_v1 | 157,084 | 78.7% | 3.785 | 0.256 | 0.348 | 0.777 |  |
| MCX | 15m | atr_bands | 157,084 | 78.8% | 4.135 | 0.267 | 0.327 | 0.777 | 8.5% [8.2%, 8.7%] (n=157,084) |
| MCX | 15m | rolling_quantiles | 157,084 | 79.1% | 4.175 | 0.269 | 0.325 | 0.780 | 9.3% [9.0%, 9.6%] (n=157,084) |
| MCX | 15m | analog_v1 | 8,000 | 79.1% | 4.115 | 0.267 | 0.253 | 0.780 | 8.5% [7.7%, 9.2%] (n=8,000) |
| MCX | 1h | range_v1 | 40,806 | 78.9% | 3.843 | 0.259 | 0.341 | 0.787 |  |
| MCX | 1h | atr_bands | 40,806 | 79.2% | 4.149 | 0.270 | 0.324 | 0.788 | 7.4% [6.9%, 7.8%] (n=40,806) |
| MCX | 1h | rolling_quantiles | 40,806 | 79.0% | 4.199 | 0.272 | 0.322 | 0.791 | 8.5% [8.0%, 8.9%] (n=40,806) |
| MCX | 1h | analog_v1 | 7,988 | 79.3% | 4.193 | 0.272 | 0.282 | 0.795 | 7.1% [6.3%, 7.8%] (n=7,988) |
| MCX | 1D | range_v1 | 6,910 | 76.3% | 3.785 | 0.257 | 0.316 | 0.787 |  |
| MCX | 1D | atr_bands | 6,910 | 80.0% | 3.750 | 0.255 | 0.319 | 0.781 | -0.9% [-1.5%, -0.3%] (n=6,910) |
| MCX | 1D | rolling_quantiles | 6,866 | 79.1% | 3.769 | 0.256 | 0.317 | 0.782 | -0.5% [-1.1%, 0.1%] (n=6,866) |
| MCX | 1D | analog_v1 | 6,455 | 78.7% | 3.778 | 0.257 | 0.293 | 0.784 | -0.1% [-0.8%, 0.5%] (n=6,455) |

## Notes

- analog_v1 is replayed on every validation bar for 1D and on a seeded sample of 2000 bars per instrument intraday; its comparison uses only the bars where it drew bands (it draws none below abstain.min_analogs analogs).
- atr_bands is refitted on each walk-forward window's training rows, like range_v1.
- rolling_quantiles uses the last 250 realised targets per instrument that had closed by the reference bar.
- Intraday fits use a seeded uniform sample of at most range_model.MAX_TRAIN_ROWS training bars.
- Only the NSE slice (equities and indices, INDIAVIX excluded) is gated; BSE (SENSEX only) and MCX are reported for information. MCX continuous futures are not roll-adjusted, so roll gaps stay in the targets.
- Watchlist instruments are today's large caps (survivorship bias): treat results as an upper bound.
