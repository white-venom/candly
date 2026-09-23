"""analog_v1: p(up), ghost candles and bands from past bars that looked like the reference bar.

An analog is a past closed bar j with the same active pattern (any of them) and the same trend bucket
as the reference bar, whose whole outcome path (bars j+1 .. j+steps) had closed by the reference bar.
Same-instrument analogs come first; other instruments' analogs from the scorecard's library are added
only when the instrument alone has fewer than `abstain.min_analogs`. With no pattern on the reference
bar, every past bar in the same trend bucket is an analog.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from candly.core.instruments import exchange_of
from candly.core.schema import validate_candles
from candly.features.context import compute_context
from candly.forecast.models import Band, Candle, Driver, Forecast, ForecastContext
from candly.forecast.timing import drop_unclosed, future_bar_times, last_expected_closed_bar, to_unix
from candly.patterns import PATTERN_INFO, detect_patterns, load_pattern_config
from candly.research.config import ResearchConfig, load_research_config
from candly.research.labels import forward_labels, forward_paths, path_columns
from candly.research.scorecard import Scorecard
from candly.research.stats import beta_interval, beta_posterior

METHOD = "analog_v1"
ANALOG_CONTEXT = "trend"


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _direction(p_up: float | None, base: float | None) -> str:
    if p_up is None or base is None or p_up == base:
        return "neutral"
    return "bullish" if p_up > base else "bearish"


def _pooled_paths(
    scorecard: Scorecard | None,
    instrument_id: str,
    patterns: list[str],
    trend: str,
    ref_ts: pd.Timestamp,
    steps: int,
) -> np.ndarray:
    lib = scorecard.analogs if scorecard is not None else None
    if lib is None or lib.empty or not patterns:
        return np.empty((0, steps, 4))
    cols = path_columns(steps)
    if any(c not in lib.columns for c in cols):
        return np.empty((0, steps, 4))
    sel = lib[
        (lib["instrument"] != instrument_id)
        & lib["pattern"].isin(patterns)
        & (lib["trend"] == trend)
        & (pd.to_datetime(lib["end_ts"], utc=True) <= ref_ts)
    ].drop_duplicates(["instrument", "ts"])
    paths = sel[cols].to_numpy(dtype=float).reshape(len(sel), steps, 4)
    return paths[~np.isnan(paths).any(axis=(1, 2))]


def make_forecast(
    instrument_id: str,
    tf: str,
    candles: pd.DataFrame,
    scorecard: Scorecard | None,
    steps: int | None = None,
    *,
    now: pd.Timestamp | None = None,
) -> Forecast:
    cfg = load_research_config()
    steps = int(steps or cfg.forecast_steps)
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    exchange = exchange_of(instrument_id)
    df = drop_unclosed(validate_candles(candles), exchange, tf, now)
    if df.empty:
        raise ValueError(f"no closed candles for {instrument_id} {tf}")
    return _AnalogRun(instrument_id, tf, exchange, df, scorecard, steps, now, cfg).run()


class _AnalogRun:
    def __init__(self, instrument_id, tf, exchange, df, scorecard, steps, now, cfg: ResearchConfig):
        self.instrument_id, self.tf, self.exchange = instrument_id, tf, exchange
        self.df, self.scorecard, self.steps, self.now, self.cfg = df, scorecard, steps, now, cfg
        self.ref = len(df) - 1
        self.ref_ts = df["ts"].iloc[-1]
        self.ref_close = float(df["close"].iloc[-1])
        self.ctx = compute_context(df, tf, exchange)
        self.patterns = detect_patterns(df, tf)
        self.active = self.patterns[self.patterns["ts"] == self.ref_ts]
        last = self.ctx.iloc[-1]
        self.atr = float(last["atr14"]) if pd.notna(last["atr14"]) else None
        self.trend = last["trend"] if isinstance(last["trend"], str) else None
        up = forward_labels(df, [steps])[f"up_{steps}"].dropna()
        self.base = float(up.mean()) if len(up) else None
        self.context = ForecastContext(
            patterns=list(self.active["pattern"]),
            trend=self.trend,
            vol_regime=last["vol_regime"] if isinstance(last["vol_regime"], str) else None,
            session_phase=last["session_phase"] if isinstance(last["session_phase"], str) else None,
            rel_volume=float(last["rel_volume"]) if pd.notna(last["rel_volume"]) else None,
            atr=self.atr,
        )

    def _forecast(self, **fields) -> Forecast:
        base = {
            "instrument": self.instrument_id,
            "tf": self.tf,
            "method": METHOD,
            "made_at": to_unix(self.now),
            "ref_time": to_unix(self.ref_ts),
            "ref_close": self.ref_close,
            "horizon_bars": self.steps,
            "p_up": None,
            "p_up_ci": None,
            "base_rate": self.base,
            "abstain": True,
            "abstain_reason": None,
            "confidence": None,
            "expected_move_pct": None,
            "ghost_candles": [],
            "bands": [],
            "invalidation": None,
            "drivers": [],
            "n_analogs": 0,
            "explanation": None,
            "context": self.context,
        }
        base.update(fields)
        return Forecast(**base)

    def _instrument_paths(self) -> np.ndarray:
        n = len(self.df)
        atr = self.ctx["atr14"].to_numpy()
        eligible = (
            (np.arange(n) + self.steps <= self.ref) & (atr > 0) & (self.ctx["trend"] == self.trend).to_numpy()
        )
        if not self.active.empty:
            names = set(self.active["pattern"])
            hist = self.patterns[self.patterns["pattern"].isin(names)]
            has_pattern = np.zeros(n, dtype=bool)
            has_pattern[pd.Index(self.df["ts"]).get_indexer(hist["ts"])] = True
            eligible &= has_pattern
        return forward_paths(self.df, self.ctx["atr14"], self.steps)[eligible]

    def run(self) -> Forecast:
        cfg = self.cfg
        if self.atr is None or self.atr <= 0 or self.trend is None or self.base is None:
            return self._forecast(
                abstain_reason=f"not enough history: {len(self.df)} bars, need ATR, trend and a base rate",
                drivers=self._context_drivers(),
            )
        expected = last_expected_closed_bar(self.exchange, self.tf, self.now)
        if expected is not None and self.ref_ts < expected:
            return self._forecast(
                abstain_reason=(
                    f"stale data: last closed bar {self.ref_ts.isoformat()}, expected {expected.isoformat()}"
                ),
                drivers=self._context_drivers(),
            )

        paths = self._instrument_paths()
        source = "same-instrument"
        if len(paths) < cfg.min_analogs and not self.active.empty:
            pooled = _pooled_paths(
                self.scorecard,
                self.instrument_id,
                list(self.active["pattern"]),
                self.trend,
                self.ref_ts,
                self.steps,
            )
            if len(pooled):
                paths = np.concatenate([paths, pooled])
                source = "same-instrument + pooled"
        n = len(paths)
        drivers = self._pattern_drivers() + self._context_drivers()
        if n == 0:
            return self._forecast(abstain_reason="no analogs in this pattern/trend bucket", drivers=drivers)

        hits = int((paths[:, -1, 3] > 0).sum())
        k = cfg.prior_strength
        p_up = float(beta_posterior(hits, n, self.base, k))
        lo, hi = beta_interval(hits, n, self.base, k, cfg.ci_level)
        edge = p_up - self.base
        bucket = f"trend={self.trend}" + (
            f", pattern in {{{', '.join(self.active['pattern'])}}}" if not self.active.empty else ", any bar"
        )
        drivers.insert(
            0,
            Driver(
                name="Analogs",
                effect=_direction(p_up, self.base),
                detail=(
                    f"{n} {source} analogs ({bucket}): {hits} closed higher after {self.steps} bars; "
                    f"posterior {_pct(p_up)} vs base rate {_pct(self.base)}"
                ),
            ),
        )
        fields = {
            "p_up": p_up,
            "p_up_ci": (float(lo), float(hi)),
            "n_analogs": n,
            "drivers": drivers,
        }
        if n < cfg.min_analogs:
            return self._forecast(abstain_reason=f"too few analogs: {n} < {cfg.min_analogs}", **fields)
        ghosts, bands = self._ghosts(paths)
        fields.update(
            ghost_candles=ghosts,
            bands=bands,
            expected_move_pct=(100.0 * (ghosts[-1].close - self.ref_close) / self.ref_close)
            if ghosts
            else None,
        )
        if abs(edge) < cfg.min_edge:
            return self._forecast(
                abstain_reason=f"edge below minimum: |p_up - base_rate| = {abs(edge):.3f} < {cfg.min_edge}",
                **fields,
            )
        excludes_base = lo > self.base or hi < self.base
        confidence = (
            "high"
            if excludes_base and abs(edge) >= 2 * cfg.min_edge
            else ("medium" if excludes_base else "low")
        )
        return self._forecast(
            abstain=False, confidence=confidence, invalidation=self._invalidation(edge), **fields
        )

    def _ghosts(self, paths: np.ndarray) -> tuple[list[Candle], list[Band]]:
        times = future_bar_times(self.exchange, self.tf, self.ref_ts, self.steps)
        med = np.median(paths, axis=0)
        closes = paths[:, :, 3]
        qs = np.quantile(closes, self.cfg.bands, axis=0)
        ghosts, bands = [], []
        for s, ts in enumerate(times):
            o, h, lo, c = (self.ref_close + v * self.atr for v in med[s])
            ghosts.append(
                Candle(time=to_unix(ts), open=o, high=max(h, o, c), low=min(lo, o, c), close=c, volume=0.0)
            )
            p10, p50, p90 = (self.ref_close + q * self.atr for q in qs[:, s][:3])
            bands.append(Band(time=to_unix(ts), p10=p10, p50=p50, p90=p90))
        return ghosts, bands

    def _invalidation(self, edge: float) -> float | None:
        want = "bullish" if edge > 0 else "bearish"
        values = self.active.loc[self.active["direction"] == want, "invalidation"].dropna()
        if values.empty:
            return None
        return float(values.max() if want == "bullish" else values.min())

    def _pattern_drivers(self) -> list[Driver]:
        out = []
        for _, row in self.active.iterrows():
            detail = "Confirmed on the reference bar."
            if self.scorecard is not None:
                stats = self.scorecard.stats_for(
                    row["pattern"], self.instrument_id, self.trend, self.steps, self.cfg.min_samples
                )
                if stats is not None:
                    q = f"q={stats.q_value:.3f}" if stats.q_value is not None else "q=n/a"
                    detail += (
                        f" Scorecard (train, {stats.horizon_bars} bars): hit {_pct(stats.hit_rate)} vs base "
                        f"{_pct(stats.base_rate)}, n={stats.n}, {q}"
                        + (", certified" if stats.certified else ", not certified")
                    )
            out.append(
                Driver(name=PATTERN_INFO[row["pattern"]].label, effect=row["direction"], detail=detail)
            )
        if self.active.empty:
            out.append(
                Driver(
                    name="No pattern",
                    effect="neutral",
                    detail=(
                        "No pattern on the reference bar; "
                        "analogs are all past bars in the same trend bucket."
                    ),
                )
            )
        return out

    def _context_drivers(self) -> list[Driver]:
        last = self.ctx.iloc[-1]
        out = []
        if self.trend is not None:
            effect = {"up": "bullish", "down": "bearish"}.get(self.trend, "neutral")
            adx = last["trend_strength"]
            out.append(
                Driver(
                    name="Trend",
                    effect=effect,
                    detail=f"{self.trend}" + (f" (ADX {adx:.0f})" if pd.notna(adx) else ""),
                )
            )
        if isinstance(last["vol_regime"], str):
            out.append(
                Driver(
                    name="Volatility regime",
                    effect="neutral",
                    detail=(
                        f"{last['vol_regime']} (ATR at the {100 * last['vol_pct']:.0f}th percentile "
                        "of the trailing year)"
                    ),
                )
            )
        if isinstance(last["near_level"], str):
            within = load_pattern_config()["context"]["near_level_atr"]
            out.append(
                Driver(
                    name="Near level",
                    effect="neutral",
                    detail=f"close within {within} ATR of {last['near_level']}",
                )
            )
        return out
