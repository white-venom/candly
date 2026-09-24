"""analog_v1: p(up), ghost candles and bands from past bars that looked like the reference bar.

An analog is a past closed bar j with the same active pattern (any of them; analog.patterns) and the
same trend bucket (analog.bucket) as the reference bar, whose whole outcome path (bars j+1 .. j+steps)
had closed by the reference bar. Same-instrument analogs come first; analogs of other instruments on the
same exchange, from the scorecard's library, are added only when the instrument alone has fewer than
`abstain.min_analogs`. With no pattern on the reference bar, every past bar in the same trend bucket is
an analog (analog.no_pattern).

Consecutive analogs' outcome paths overlap, so the posterior and its CI use an effective sample of
n / steps analogs (analog.effective_n); the abstain rule still counts all n analogs.

A directional call (abstain=false) passes every gate in research.yaml `abstain`, in this order:
- intraday_within_session: on intraday timeframes the last target bar closes by the reference day's
  session close ("horizon crosses session close").
- require_validated_bucket ("unvalidated bucket"), in two parts:
  - structural, always on: the reference bar has an active pattern and the horizon is a scorecard
    horizon, so the analog bucket (the active patterns in the reference bar's trend bucket) is a
    scorecard bucket at all;
  - statistical, off in validation-period replays (`bucket_gate=False`), whose statistics come from
    the period being scored: in the scorecard of the instrument's own exchange
    (abstain.same_exchange_validation), the bucket's de-duplicated validation events form at least
    abstain.validation_min_clusters clusters and beat their bucket base rate with a one-sided
    cluster-robust p-value below abstain.validation_max_p (Scorecard.validate_bucket).
- require_edge_over_costs: the analogs' median trade return the call's way, entered at the next bar's
  open and exited at the last step's close (as the scorecard's expectancy measures trades), clears the
  round-trip cost for the instrument kind and holding type ("edge below costs").
- min_reward_risk: the trade's reward:risk is at least this ("reward below risk").
Its stop is the tightest active-pattern invalidation in the call's direction, else fallback_stop_atr
ATR beyond the reference close, and never closer than patterns.yaml min_stop_atr ATR.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from candly.core.calendar import get_calendar
from candly.core.instruments import exchange_of, get_instrument
from candly.core.schema import validate_candles
from candly.core.timeframes import is_intraday
from candly.features.context import compute_context
from candly.features.expiry import expiry_on
from candly.forecast.models import Band, Candle, Driver, Forecast, ForecastContext, Trade
from candly.forecast.timing import (
    drop_unclosed,
    future_bar_times,
    last_expected_closed_bar,
    stale_grace,
    to_unix,
)
from candly.patterns import PATTERN_INFO, detect_patterns, load_pattern_config
from candly.research.config import ResearchConfig, load_research_config
from candly.research.costs import round_trip_cost
from candly.research.labels import forward_labels, forward_paths, path_columns
from candly.research.scorecard import Scorecard
from candly.research.stats import beta_interval, beta_posterior

METHOD = "analog_v1"


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _direction(p_up: float | None, base: float | None) -> str:
    if p_up is None or base is None or p_up == base:
        return "neutral"
    return "bullish" if p_up > base else "bearish"


def confidence_label(lo: float, hi: float, base: float, edge: float, cfg: ResearchConfig) -> str:
    """The first rule in research.yaml `confidence` (high, then medium) that the forecast meets, else low."""
    excludes_base = lo > base or hi < base
    for label, rule in cfg.confidence.items():
        big_enough = abs(edge) >= rule.min_edge_multiple * cfg.min_edge
        if big_enough and (excludes_base or not rule.ci_excludes_base):
            return label
    return "low"


def ghost_path(paths: np.ndarray) -> np.ndarray:
    """(steps, 4) OHLC in ATR units from the reference close, from analog paths (n, steps, 4).

    Closes follow the median close path. Each step's body is the analogs' median body at that step,
    signed by the median path's move; its wicks are the analogs' median upper and lower wicks. Medians
    of O, H, L and C taken separately would cancel the bodies out and draw every step as a doji.
    """
    o, h, lo, c = (paths[:, :, k] for k in range(4))
    close = np.median(c, axis=0)
    body = np.median(np.abs(c - o), axis=0)
    upper = np.median(h - np.maximum(o, c), axis=0)
    lower = np.median(np.minimum(o, c) - lo, axis=0)
    opens = close - np.sign(np.diff(close, prepend=0.0)) * body
    top, bottom = np.maximum(opens, close), np.minimum(opens, close)
    return np.column_stack([opens, top + upper, bottom - lower, close])


def _pooled_paths(
    scorecard: Scorecard | None,
    instrument_id: str,
    patterns: list[str],
    bucket: str,
    value: str,
    ref_ts: pd.Timestamp,
    steps: int,
) -> np.ndarray:
    lib = scorecard.analogs if scorecard is not None else None
    if lib is None or lib.empty or not patterns:
        return np.empty((0, steps, 4))
    cols = path_columns(steps)
    if any(c not in lib.columns for c in cols):
        return np.empty((0, steps, 4))
    exchange = exchange_of(instrument_id)
    same_exchange = lib["instrument"].map(lambda i: exchange_of(i) == exchange).astype(bool)
    sel = lib[
        (lib["instrument"] != instrument_id)
        & same_exchange
        & lib["pattern"].isin(patterns)
        & (lib[bucket] == value)
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
    check_stale: bool = True,
    bucket_gate: bool = True,
) -> Forecast:
    """Forecast from the last bar closed by `now`.

    `check_stale=False` skips the calendar's stale-data abstention; only a historical replay
    (research.evaluate) should pass it, because the calendar has no pre-2026 holidays.
    `bucket_gate=False` skips the statistical part of abstain.require_validated_bucket (its structural
    part stays on); a replay of the validation period must pass it, because the scorecard's validation
    statistics come from that same period."""
    cfg = load_research_config()
    steps = int(steps or cfg.forecast_steps)
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    exchange = exchange_of(instrument_id)
    df = drop_unclosed(validate_candles(candles), exchange, tf, now)
    if df.empty:
        raise ValueError(f"no closed candles for {instrument_id} {tf}")
    run = _AnalogRun(instrument_id, tf, exchange, df, scorecard, steps, now, cfg, check_stale, bucket_gate)
    return run.run()


class _AnalogRun:
    def __init__(
        self,
        instrument_id,
        tf,
        exchange,
        df,
        scorecard,
        steps,
        now,
        cfg: ResearchConfig,
        check_stale=True,
        bucket_gate=True,
    ):
        self.instrument_id, self.tf, self.exchange = instrument_id, tf, exchange
        self.df, self.scorecard, self.steps, self.now, self.cfg = df, scorecard, steps, now, cfg
        self.check_stale, self.bucket_gate = check_stale, bucket_gate
        self.ref = len(df) - 1
        self.ref_ts = df["ts"].iloc[-1]
        self.ref_close = float(df["close"].iloc[-1])
        self.ctx = compute_context(df, tf, exchange, instrument_id=instrument_id)
        self.patterns = detect_patterns(df, tf)
        self.active = self.patterns[self.patterns["ts"] == self.ref_ts]
        last = self.ctx.iloc[-1]
        self.atr = float(last["atr14"]) if pd.notna(last["atr14"]) else None
        self.trend = last["trend"] if isinstance(last["trend"], str) else None
        bucket = last[cfg.analog_bucket]
        self.bucket = bucket if isinstance(bucket, str) else None
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
            "trade": None,
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
        same_bucket = (self.ctx[self.cfg.analog_bucket] == self.bucket).to_numpy()
        eligible = (np.arange(n) + self.steps <= self.ref) & (atr > 0) & same_bucket
        if not self.active.empty:
            names = set(self.active["pattern"])
            hist = self.patterns[self.patterns["pattern"].isin(names)]
            has_pattern = np.zeros(n, dtype=bool)
            has_pattern[pd.Index(self.df["ts"]).get_indexer(hist["ts"])] = True
            eligible &= has_pattern
        return forward_paths(self.df, self.ctx["atr14"], self.steps)[eligible]

    def run(self) -> Forecast:
        cfg = self.cfg
        if self.atr is None or self.atr <= 0 or self.bucket is None or self.base is None:
            return self._forecast(
                abstain_reason=(
                    f"not enough history: {len(self.df)} bars, need ATR, {cfg.analog_bucket} and a base rate"
                ),
                drivers=self._context_drivers(),
            )
        expected = (
            last_expected_closed_bar(self.exchange, self.tf, self.now - stale_grace(self.tf))
            if self.check_stale
            else None
        )
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
                cfg.analog_bucket,
                self.bucket,
                self.ref_ts,
                self.steps,
            )
            if len(pooled):
                paths = np.concatenate([paths, pooled])
                source = "same-instrument + pooled"
        n = len(paths)
        drivers = self._pattern_drivers() + self._context_drivers()
        if n == 0:
            return self._forecast(
                abstain_reason=f"no analogs in this pattern/{cfg.analog_bucket} bucket", drivers=drivers
            )

        hits = int((paths[:, -1, 3] > 0).sum())
        k = cfg.prior_strength
        scale = self.steps if cfg.analog_effective_n == "n_over_steps" else 1
        p_up = float(beta_posterior(hits / scale, n / scale, self.base, k))
        lo, hi = beta_interval(hits / scale, n / scale, self.base, k, cfg.ci_level)
        edge = p_up - self.base
        drivers.insert(
            0,
            Driver(
                name="Analogs",
                effect=_direction(p_up, self.base),
                detail=(
                    f"{n} {source} analogs ({self._bucket_label()}; effective n {n / scale:.0f}): {hits} "
                    f"closed higher after {self.steps} bars; posterior {_pct(p_up)} vs base rate "
                    f"{_pct(self.base)}"
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
        times = future_bar_times(self.exchange, self.tf, self.ref_ts, self.steps)
        ghosts, bands = self._ghosts(paths, times)
        expected_move = (ghosts[-1].close - self.ref_close) / self.ref_close if ghosts else None
        fields.update(
            ghost_candles=ghosts,
            bands=bands,
            expected_move_pct=100.0 * expected_move if expected_move is not None else None,
        )
        if abs(edge) < cfg.min_edge:
            return self._forecast(
                abstain_reason=f"edge below minimum: |p_up - base_rate| = {abs(edge):.3f} < {cfg.min_edge}",
                **fields,
            )
        bullish = edge > 0
        reason = self._call_gates(bullish, times, paths)
        if reason is not None:
            return self._forecast(abstain_reason=reason, **fields)
        stop = self._stop(bullish)
        target = bands[-1].p50
        sign = 1.0 if bullish else -1.0
        trade = Trade(
            entry=self.ref_close,
            stop=stop,
            target=target,
            reward_risk=sign * (target - self.ref_close) / abs(self.ref_close - stop),
        )
        if trade.reward_risk < cfg.min_reward_risk:
            return self._forecast(abstain_reason=f"reward below risk: R:R {trade.reward_risk:.2f}", **fields)
        return self._forecast(
            abstain=False,
            confidence=confidence_label(float(lo), float(hi), self.base, edge, cfg),
            invalidation=stop,
            trade=trade,
            **fields,
        )

    def _bucket_label(self) -> str:
        patterns = ", ".join(self.active["pattern"])
        where = f"pattern in {{{patterns}}}" if not self.active.empty else "any bar"
        return f"{self.cfg.analog_bucket}={self.bucket}, {where}"

    def _ghosts(self, paths: np.ndarray, times: list[pd.Timestamp]) -> tuple[list[Candle], list[Band]]:
        ohlc = self.ref_close + ghost_path(paths) * self.atr
        qs = self.ref_close + np.quantile(paths[:, :, 3], self.cfg.bands, axis=0) * self.atr
        ghosts, bands = [], []
        for s, ts in enumerate(times):
            o, h, lo, c = (float(v) for v in ohlc[s])
            ghosts.append(Candle(time=to_unix(ts), open=o, high=h, low=lo, close=c, volume=0.0))
            p10, p50, p90 = (float(v) for v in qs[:3, s])
            bands.append(Band(time=to_unix(ts), p10=p10, p50=p50, p90=p90))
        return ghosts, bands

    def _crosses_session_close(self, times: list[pd.Timestamp]) -> bool:
        if not is_intraday(self.tf):
            return False
        if not times:
            return True
        cal = get_calendar()
        _, close = cal.session_times(self.exchange, cal.local_date(self.ref_ts))
        return cal.bar_close_time(self.exchange, times[-1], self.tf) > close

    def _call_gates(self, bullish: bool, times: list[pd.Timestamp], paths: np.ndarray) -> str | None:
        """The abstain reason of the first research.yaml `abstain` gate the call fails before its trade
        is drawn up, else None."""
        cfg = self.cfg
        crosses = self._crosses_session_close(times)
        if cfg.intraday_within_session and crosses:
            last = times[-1].isoformat() if times else "none"
            return f"horizon crosses session close: last target bar {last} ends after this session"
        if cfg.require_validated_bucket:
            reason = self._not_a_bucket() or (self._unvalidated(bullish) if self.bucket_gate else None)
            if reason is not None:
                return f"unvalidated bucket: {reason}"
        if cfg.require_edge_over_costs:
            holding = "intraday" if is_intraday(self.tf) and not crosses else "multi_day"
            kind = get_instrument(self.instrument_id).kind
            cost = round_trip_cost(kind, holding, side="long" if bullish else "short")
            move = (1.0 if bullish else -1.0) * self._median_trade_return(paths)
            if move < cost:
                return (
                    f"edge below costs: median move {100 * move:.2f}% the call's way from the next open vs "
                    f"{100 * cost:.2f}% round trip ({kind}, {holding.replace('_', '-')})"
                )
        return None

    def _median_trade_return(self, paths: np.ndarray) -> float:
        """Median over the analogs of a long entered at the next bar's open and exited at the last step's
        close, as a return on the entry price."""
        first_open, last_close = paths[:, 0, 0], paths[:, -1, 3]
        entry = self.ref_close + first_open * self.atr
        return float(np.median((last_close - first_open) * self.atr / entry))

    def _not_a_bucket(self) -> str | None:
        """The structural part of the validated-bucket gate, on in every replay."""
        if self.active.empty:
            return f"{self._bucket_label()} is not a scorecard bucket"
        if self.steps not in self.cfg.horizons:
            return f"{self.steps} bars is not a scorecard horizon {list(self.cfg.horizons)}"
        return None

    def _unvalidated(self, bullish: bool) -> str | None:
        """The statistical part of the validated-bucket gate, which reads validation-span statistics."""
        cfg = self.cfg
        if self.scorecard is None:
            return "no scorecard"
        card_exchange = self.scorecard.meta.exchange
        if cfg.same_exchange_validation and card_exchange not in (None, self.exchange):
            return f"the scorecard is {card_exchange}'s, not {self.exchange}'s"
        v = self.scorecard.validate_bucket(
            self.active["pattern"],
            self.instrument_id,
            f"{cfg.analog_bucket}={self.bucket}",
            self.steps,
            bullish,
            min_clusters=cfg.validation_min_clusters,
            max_p=cfg.validation_max_p,
            same_exchange=cfg.same_exchange_validation,
        )
        if v.instrument is None:
            return (
                f"{self._bucket_label()} has {v.n_clusters} independent validation clusters ({v.n} events), "
                f"fewer than {cfg.validation_min_clusters}"
            )
        if not v.validated:
            return (
                f"{self._bucket_label()} ({v.instrument}) moved the call's way {_pct(v.hit_rate)} of the "
                f"time in validation vs a base rate of {_pct(v.base_rate)}, n={v.n} in {v.n_clusters} "
                f"clusters, one-sided p={v.p_value:.3f} (needs < {cfg.validation_max_p})"
            )
        return None

    def _stop(self, bullish: bool) -> float:
        want = "bullish" if bullish else "bearish"
        values = self.active.loc[self.active["direction"] == want, "invalidation"].dropna()
        if len(values):
            stop = float(values.max() if bullish else values.min())
        else:
            away = self.cfg.fallback_stop_atr * self.atr
            stop = self.ref_close - away if bullish else self.ref_close + away
        floor = float(load_pattern_config()["min_stop_atr"]) * self.atr
        return min(stop, self.ref_close - floor) if bullish else max(stop, self.ref_close + floor)

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
                        f"analogs are all past bars in the same {self.cfg.analog_bucket} bucket."
                    ),
                )
            )
        return out

    def _expiry_driver(self, days_to_expiry) -> Driver | None:
        if pd.isna(days_to_expiry) or days_to_expiry > 1:
            return None
        info = expiry_on(self.instrument_id, get_calendar().local_date(self.ref_ts))
        if info is None:
            return None
        when = "on" if days_to_expiry == 0 else "1 trading day before"
        return Driver(
            name="Expiry",
            effect="neutral",
            detail=f"The reference bar is {when} the {info.kind} expiry ({info.next_expiry.isoformat()}).",
        )

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
        context_cfg = load_pattern_config()["context"]
        if isinstance(last["vol_regime"], str):
            slot = " at this time of day" if is_intraday(self.tf) else ""
            out.append(
                Driver(
                    name="Volatility regime",
                    effect="neutral",
                    detail=(
                        f"{last['vol_regime']} (ATR at the {100 * last['vol_pct']:.0f}th percentile "
                        f"of the last {context_cfg['vol_window_days']} sessions{slot})"
                    ),
                )
            )
        if isinstance(last["near_level"], str):
            within = context_cfg["near_level_atr"]
            out.append(
                Driver(
                    name="Near level",
                    effect="neutral",
                    detail=f"close within {within} ATR of {last['near_level']}",
                )
            )
        expiry = self._expiry_driver(last["days_to_expiry"])
        if expiry is not None:
            out.append(expiry)
        return out
